"""The actual "salesperson" call. One Gemini request per chat turn, given:

- ALL business facts (never searched -- see the note in BusinessFactDB and
  the conversation that led here: missing an exact fact is worse than a
  slightly-too-long prompt, and the fact set is small enough that this is
  cheap regardless).
- ALL business links (small set, LLM decides which are relevant to mention).
- ALL lead forms' name + trigger_intent (LLM matches user intent -> form).
- TOP-K semantically searched document chunks (tenant-scoped, see
  services/retrieval.py).
- TOP-K semantically searched gallery items (images AND folders), offered as
  CANDIDATES only -- the LLM decides whether showing any actually helps, it
  isn't forced to pick one just because candidates were retrieved. Retrieval
  has already ranked these by similarity, so candidate order = best match
  first; the LLM's job is picking which (if any) are worth surfacing, not
  re-ranking them.
- Recent conversation history for continuity.

Gemini is asked to return one structured JSON object so a single response
can carry reply text + gallery item references + link references + a
lead-form trigger + an "I don't know this" signal, all at once.
"""

import json
import uuid
from dataclasses import dataclass, field

from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import usage as usage_service
from app.core.config import settings
from app.models.chat import ChatMessageDB
from app.models.document import BusinessFactDB, DocumentChunk
from app.models.user import LeadFormDB

_genai_client = genai.Client(
    vertexai=True,
    project="kiteum-production",
    location="us-central1"
)


@dataclass
class ChatAgentResult:
    reply: str
    show_gallery_item_ids: list[uuid.UUID] = field(default_factory=list)
    link_platforms_to_show: list[str] = field(default_factory=list)
    trigger_lead_form_id: uuid.UUID | None = None
    could_not_answer: bool = False


SYSTEM_PROMPT_TEMPLATE = """You are the customer-facing chat agent for a local business. Your job is
to be genuinely helpful like a knowledgeable staff member -- answering
questions clearly and naturally. Conversion (a lead form submission, a
phone call, a booking, or a link click) matters, but it is a byproduct of
being helpful, not something to force into every reply.


Core behavior rules:
- Keep replies SHORT. 1-3 sentences for most answers. Users chatting with a
  business bot want quick, useful answers -- not a conversation. Never
  produce long paragraphs or multi-part explanations unless the user
  explicitly asks for detail.
- Never invent facts, prices, policies, or links that aren't given to you
  below.
- Answer the user's question first and directly, the way a good staff
  member would. It is completely normal -- expected, even -- for a reply
  to contain no pitch, link, or form at all. Most replies should not
  include one.
- Only push toward action (form, phone number, link) when there is a REAL
  signal of intent, such as:
    * the user asks about price, availability, scheduling, or how to
      book/order/get started
    * the user describes their specific situation/need in a way that
      suggests they're close to deciding
    * the user has asked multiple questions that are converging on a
      decision (not just casually browsing or asking general questions)
  When one of these signals is present, weave the offer naturally into
  the answer itself -- not as a bolted-on sales line at the end.
- Never repeat the same pitch (form/phone/link) two turns in a row if the
  user didn't engage with it. If they ignored it once, drop it and just
  keep helping until a new, stronger signal appears. Repetition reads as
  robotic and pushy -- avoid it.
- If the user shows any sign of wanting more depth than facts/links can
  give -- a complex situation, negotiation, a complaint, or anything the
  facts don't cleanly cover -- guide them to contact the business directly
  (phone number if available, else a lead form) rather than attempting a
  long explanation yourself. A short honest answer + a handoff beats a
  long guess every time.

There are two different situations where you won't have a good answer --
handle them differently:

1. OFF-TOPIC: the message has nothing to do with this business (general
   knowledge, coding help, unrelated small talk, questions about some other
   company, etc). Briefly and politely say this chat is for questions about
   THIS business, and invite them to ask something related. Do NOT set
   "could_not_answer" to true for this -- there's no business information
   gap here, the question just isn't about the business at all.

2. GENUINE GAP: the message IS clearly about this business (its services,
   pricing, policies, availability, a property/product it offers, etc.) but
   the facts, knowledge, or gallery given to you below don't actually cover
   it. Say so honestly, offer to connect them directly (phone/lead form) so
   a human can help, and set "could_not_answer" to true -- this is the case
   the business owner should be notified about so they can follow up
   personally.

Only case 2 should ever set "could_not_answer" to true. If you're unsure
whether a message counts as being about the business, lean toward NOT
escalating -- escalation should flag real missed business questions, not
every message you happen not to have a full answer for.

=== BUSINESS FACTS (exact, always authoritative -- use these verbatim for
anything like hours, pricing, phone numbers, or policies. If a phone number
fact exists, it's a strong conversion tool -- offer it when contact
genuinely makes sense, not by default) ===
{facts_block}

=== BUSINESS LINKS (only mention ones actually relevant to what the user
asked; return their "platform" key in link_platforms_to_show) ===
{links_block}

=== LEAD FORMS AVAILABLE (only set trigger_lead_form_id when the user's
message shows real, specific intent to book/inquire/get a quote -- e.g.
they ask about pricing, availability, or say something like "how do I get
started." Casual questions, general curiosity, or early-stage browsing
should NOT trigger a form. When in doubt, don't trigger it -- a missed
nudge costs far less than feeling like a spam bot) ===
{forms_block}

=== RELEVANT KNOWLEDGE (retrieved based on the user's question -- use if
relevant, ignore if not. Summarize briefly rather than reproducing at
length) ===
{chunks_block}

=== GALLERY CANDIDATES (retrieved based on the user's question, listed BEST
MATCH FIRST -- each is either a single "image" or a "folder" grouping many
related images, e.g. all photos of one property/product/listing.

Only include ids in show_gallery_item_ids if they genuinely help
answer/illustrate your reply -- do not force one into every response.

When the single best match is an IMAGE, lead with that image's id, and if
it belongs to a folder (shown as "folder_id" on that candidate), you may
ALSO include that folder's id right after it in the list, so the user can
explore the rest of that set. Order matters: put the most relevant item
first, any "explore more" folder after it. Do not include more than 2-3 ids
total.) ===
{gallery_block}

=== CONVERSATION SO FAR ===
{history_block}

Respond to the user's latest message. Keep it short. Return ONLY this JSON
object, no other text:
{{
  "reply": "your reply text to the user",
  "show_gallery_item_ids": ["<id from GALLERY CANDIDATES above>", ...],
  "link_platforms_to_show": ["<platform keys from BUSINESS LINKS above>"],
  "trigger_lead_form_id": "<id from LEAD FORMS above, or null>",
  "could_not_answer": true/false
}}"""

def _facts_block(facts: list[BusinessFactDB]) -> str:
    if not facts:
        return "(none on file)"
    return "\n".join(f"- {f.key}: {f.value}" for f in facts)


def _links_block(links: list[dict]) -> str:
    if not links:
        return "(none on file)"
    return "\n".join(f'- platform="{l["platform"]}" label="{l.get("label") or l["platform"]}"' for l in links)


def _forms_block(forms: list[LeadFormDB]) -> str:
    if not forms:
        return "(none on file)"
    lines = []
    for f in forms:
        intent = f.trigger_intent or "(no specific trigger -- use as the general inquiry form)"
        lines.append(f'- id="{f.id}" name="{f.name}" trigger_intent="{intent}"')
    return "\n".join(lines)


def _chunks_block(chunks: list[DocumentChunk]) -> str:
    if not chunks:
        return "(no matching knowledge found for this question)"
    return "\n\n".join(f"- {c.chunk_text}" for c in chunks)


def _gallery_block(gallery_candidates: list[dict]) -> str:
    if not gallery_candidates:
        return "(no matching images or folders found for this question)"
    lines = []
    for c in gallery_candidates:
        line = f'- id="{c["id"]}" type="{c["item_type"]}" title="{c.get("title") or "(untitled)"}" description="{c.get("description") or ""}"'
        if c.get("folder_id"):
            line += f' folder_id="{c["folder_id"]}"'
        lines.append(line)
    return "\n".join(lines)


def _history_block(history: list[ChatMessageDB]) -> str:
    if not history:
        return "(this is the first message in the conversation)"
    lines = []
    for m in history:
        speaker = "User" if m.role == "user" else "Agent"
        lines.append(f"{speaker}: {m.content}")
    return "\n".join(lines)


def _extract_json(raw_text: str) -> dict:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return json.loads(cleaned)


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value or value == "null":
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _parse_uuid_list(values: list | None) -> list[uuid.UUID]:
    if not values:
        return []
    parsed = [_parse_uuid(v) for v in values]
    return [p for p in parsed if p is not None]




async def generate_chat_response(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_message: str,
    facts: list[BusinessFactDB],
    links: list[dict],
    lead_forms: list[LeadFormDB],
    chunks: list[DocumentChunk],
    gallery_candidates: list[dict],
    history: list[ChatMessageDB],
) -> ChatAgentResult:
    await usage_service.check_ai_credits_available(db, tenant_id)

    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        facts_block=_facts_block(facts),
        links_block=_links_block(links),
        forms_block=_forms_block(lead_forms),
        chunks_block=_chunks_block(chunks),
        gallery_block=_gallery_block(gallery_candidates),
        history_block=_history_block(history),
    )

    response = await _genai_client.aio.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=[prompt, f"User's latest message: {user_message}"],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    usage_meta = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
    completion_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
    await usage_service.charge_ai_credits(db, tenant_id, prompt_tokens, completion_tokens)

    data = _extract_json(response.text)

    return ChatAgentResult(
        reply=data.get("reply", "").strip() or "Sorry, I'm not sure how to answer that.",
        show_gallery_item_ids=_parse_uuid_list(data.get("show_gallery_item_ids")),
        link_platforms_to_show=data.get("link_platforms_to_show") or [],
        trigger_lead_form_id=_parse_uuid(data.get("trigger_lead_form_id")),
        could_not_answer=bool(data.get("could_not_answer", False)),
    )