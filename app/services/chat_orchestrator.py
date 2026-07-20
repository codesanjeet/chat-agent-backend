import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import chat as chat_crud
from app.crud import escalation as escalation_crud
from app.crud import fact as fact_crud
from app.crud import gallery as gallery_crud
from app.crud import lead as lead_crud
from app.models.gallery import GalleryItemType
from app.services import retrieval
from app.services.chat_llm import generate_chat_response
from app.services.embeddings import embed_text


def _serialize_gallery_hit(hit: dict) -> dict:
    item = hit["item"]
    parent = hit["parent_folder"]
    return {
        "item_id": str(item.id),
        "item_type": item.item_type.value,
        "title": item.title,
        "image_url": item.image_url,
        "external_url": item.external_url,
        "parent_folder": (
            {"id": str(parent.id), "title": parent.title, "external_url": parent.external_url}
            if parent is not None
            else None
        ),
    }


async def run_chat_turn(db: AsyncSession, tenant_id: uuid.UUID, session_id: str, user_message: str) -> dict:
    # 1. Persist the user's message first.
    await chat_crud.save_message(db, tenant_id, session_id, role="user", content=user_message)

    # 2. Gather grounding context.
    query_vector = await embed_text(user_message)

    chunks = await retrieval.search_document_chunks(db, tenant_id, query_vector, top_k=4)
    gallery_hits = await retrieval.search_gallery_items(db, tenant_id, query_vector, top_k=5)
    facts = await fact_crud.list_facts(db, tenant_id)
    links_record = await gallery_crud.get_business_links(db, tenant_id)
    links = links_record.links if links_record else []
    lead_forms = await lead_crud.list_lead_forms(db, tenant_id)
    history = await chat_crud.get_recent_messages(db, tenant_id, session_id, limit=10)

    # Candidates handed to the LLM prompt -- it picks which of these (if any)
    # actually answer the user's question; retrieval has already put the
    # single best match first, so the LLM's job is just to confirm/filter,
    # not to re-rank. `folder_id` is only set when the candidate is an IMAGE
    # that belongs to a folder -- lets the LLM know it can offer both the
    # image and its "explore more" folder link together.
    gallery_candidates = [
        {
            "id": hit["item"].id,
            "item_type": hit["item"].item_type.value,
            "title": hit["item"].title,
            "description": hit["item"].description,
            "folder_id": hit["parent_folder"].id if hit["parent_folder"] else None,
        }
        for hit in gallery_hits
    ]

    # 3. Ask the LLM.
    result = await generate_chat_response(
        db=db,
        tenant_id=tenant_id,
        user_message=user_message,
        facts=facts,
        links=links,
        lead_forms=lead_forms,
        chunks=chunks,
        gallery_candidates=gallery_candidates,
        history=history,
    )

    # 4. Resolve the LLM's chosen gallery item ids back into full hit objects
    #    (still carrying each image's parent folder), in the SAME order the
    #    LLM/retrieval ranked them -- best match first, folder link after.
    hits_by_id = {hit["item"].id: hit for hit in gallery_hits}
    gallery_items_out = [
        _serialize_gallery_hit(hits_by_id[item_id])
        for item_id in result.show_gallery_item_ids
        if item_id in hits_by_id
    ]

    links_out = [l for l in links if l["platform"] in result.link_platforms_to_show]

    lead_form_out = None
    if result.trigger_lead_form_id:
        form = next((f for f in lead_forms if f.id == result.trigger_lead_form_id), None)
        if form:
            lead_form_out = {
                "id": form.id,
                "name": form.name,
                "requires_payment": form.requires_payment,
                "form_schema": form.form_schema,
            }

    # 5. Persist the assistant's message -- the FULL gallery snapshot goes
    #    into history, not just an id, so "what did the AI suggest" survives
    #    even if that image/folder is later edited or deleted.
    assistant_message = await chat_crud.save_message(
        db,
        tenant_id,
        session_id,
        role="assistant",
        content=result.reply,
        gallery_items_shown=gallery_items_out or None,
        links_shown=links_out or None,
        lead_form_id=lead_form_out["id"] if lead_form_out else None,
        was_escalated=result.could_not_answer,
    )

    # 6. Log escalation if unanswered.
    if result.could_not_answer:
        await escalation_crud.create_escalation(
            db, tenant_id, session_id, question=user_message, chat_message_id=assistant_message.id
        )

    await db.commit()

    return {
        "reply": result.reply,
        "gallery_items": gallery_items_out,
        "links": links_out,
        "lead_form": lead_form_out,
        "escalated": result.could_not_answer,
    }