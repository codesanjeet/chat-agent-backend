"""All Gemini calls live here -- extraction, fact-separation, and moderation.

Design notes:
- Images and PDFs are sent to Gemini as raw inline bytes (native document /
  vision understanding). No OCR library, no PDF-to-image rendering: one
  multimodal call does extraction + a confidence/reason judgement together.
- Every call asks Gemini for strict JSON (response_mime_type="application/json")
  so we never have to regex text out of a chatty response.
- These calls are NOT counted against the embedding token budget -- that
  budget is specifically the OpenAI embedding spend. Gemini extraction cost
  is a separate, smaller concern; wire it into UsageDB.ai_credits_balance
  later if you want to meter it too.
"""

import json
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from app.core.config import settings

_genai_client = genai.Client(
    vertexai=True,
    project="kiteum-production",
    location="us-central1"
)


@dataclass
class ExtractionResult:
    text: str
    confidence: str  # "high" | "low"
    reason: str  # "ok" | "blurry" | "empty" | "unsupported" | ...


@dataclass
class FactSplitResult:
    facts: list[dict]  # [{"key": ..., "value": ...}, ...]
    general_content: str


@dataclass
class ModerationResult:
    is_safe: bool
    reason: str | None


def _extract_json(raw_text: str) -> dict:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # Defensive fallback in case the model wraps JSON in fences despite instructions.
        cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return json.loads(cleaned)


EXTRACTION_PROMPT = """You are extracting business reference text from an uploaded file for a
customer-support chatbot's knowledge base.

Read the attached file and return ONLY a JSON object, no other text:
{
  "text": "<all the readable, relevant text content, cleaned up>",
  "confidence": "high" | "low",
  "reason": "ok" | "blurry" | "empty" | "unreadable" | "not_business_relevant"
}

Format "text" as clean markdown:
- Use ## headers for distinct sections/topics (e.g. product names, categories).
- Use bullet lists for feature lists, menu items, or any enumerable content.
- Keep paragraphs short and separated by blank lines.
- Preserve every piece of business-relevant information -- do not summarize,
  shorten, or omit details. This is a formatting pass, not a summarization pass.
- Remove only genuine noise: repeated navigation text, boilerplate, or
  watermark artifacts that carry no business meaning.

Use "low confidence" + an explanatory reason if the file is blurry, mostly
blank, or has no extractable business content. A human will review and edit
before anything is used."""


async def extract_text_from_image(image_bytes: bytes, mime_type: str) -> ExtractionResult:
    response = await _genai_client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            EXTRACTION_PROMPT,
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = _extract_json(response.text)
    return ExtractionResult(
        text=data.get("text", ""), confidence=data.get("confidence", "low"), reason=data.get("reason", "unreadable")
    )


async def extract_text_from_pdf(pdf_bytes: bytes) -> ExtractionResult:
    response = await _genai_client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            EXTRACTION_PROMPT,
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = _extract_json(response.text)
    return ExtractionResult(
        text=data.get("text", ""), confidence=data.get("confidence", "low"), reason=data.get("reason", "unreadable")
    )


# for web beauty text

CLEANUP_PROMPT = """You are formatting scraped website text for a business knowledge base.
The text below was extracted from a webpage and has lost its original
structure (no paragraph breaks, headers, or lists).

Reformat it as clean markdown:
- Use ## headers for distinct sections/topics you can identify.
- Use bullet lists for feature lists, menus, or enumerable items.
- Remove navigation labels, repeated boilerplate, cookie notices, or footer
  text that carries no business meaning.
- Preserve every piece of actual business content -- do not summarize or
  shorten. This is a formatting pass only.

Return ONLY JSON: {"text": "<cleaned markdown>"}

Raw text:
---
{content}
---"""


async def clean_scraped_text(raw_text: str) -> str:
    prompt = CLEANUP_PROMPT.replace("{content}", raw_text)
    response = await _genai_client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = _extract_json(response.text)
    return data.get("text", raw_text)  # fall back to raw text if the model returns nothing usable


async def extract_text_from_weblink(url: str) -> ExtractionResult:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TenantBot/1.0)"})
            resp.raise_for_status()
    except httpx.HTTPError:
        return ExtractionResult(text="", confidence="low", reason="unreadable")

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    raw_text = " ".join(soup.stripped_strings)

    if not raw_text.strip():
        return ExtractionResult(text="", confidence="low", reason="empty")

    cleaned_text = await clean_scraped_text(raw_text)
    return ExtractionResult(text=cleaned_text, confidence="high", reason="ok")




FACT_SPLIT_PROMPT = """You are organizing business content for a chatbot knowledge base.

Given the text below, split it into:
1. "facts": short, exact, volatile lookup facts -- things like hours, phone
   number, address, pricing, policies, refund rules. Each fact is a
   {"key": "snake_case_key", "value": "the exact value"} pair. Keys should be
   stable and reusable, e.g. "opening_hours", "phone_number", "refund_policy".
   Only extract facts that are explicitly and clearly stated -- do not invent
   or infer values.
2. "general_content": everything else -- descriptive, narrative content that's
   fine to answer via semantic search rather than exact lookup. Keep this in
   the original wording as much as possible, just with the extracted facts
   removed from it (do not summarize or shorten the rest).

Return ONLY JSON:
{"facts": [{"key": "...", "value": "..."}], "general_content": "..."}

Text:
---
{content}
---"""


async def separate_facts_and_content(text: str) -> FactSplitResult:
    prompt = FACT_SPLIT_PROMPT.replace("{content}", text)
    response = await _genai_client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = _extract_json(response.text)
    return FactSplitResult(facts=data.get("facts", []), general_content=data.get("general_content", text))


MODERATION_PROMPT = """You are a content-safety filter for a local business's public-facing
image gallery description. The gallery is shown directly to end customers via
a chatbot, so nothing illegal, dangerous, or exploitative should be published.

Given the description below, decide if it's safe to publish. Flag as unsafe
anything referencing illegal activity, weapons, drugs, violence, hate speech,
sexual content, or content exploiting/endangering minors.

Return ONLY JSON:
{"is_safe": true | false, "reason": null | "short reason if unsafe"}

Description:
---
{content}
---"""


async def moderate_gallery_description(description: str) -> ModerationResult:
    prompt = MODERATION_PROMPT.replace("{content}", description)
    response = await _genai_client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = _extract_json(response.text)
    return ModerationResult(is_safe=data.get("is_safe", False), reason=data.get("reason"))
