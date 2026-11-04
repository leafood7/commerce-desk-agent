"""Source-bound policy cards: automatic suggestions, human review, explicit publication."""

import re
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select

from workbench.domain import audit, fingerprint
from workbench.knowledge_models import KnowledgeChunk, PolicyExtraction
from workbench.knowledge_revisions import publication_lock, reviewable_blocks
from workbench.knowledge_service import scoped_document, scoped_version
from workbench.models import now
from workbench.schemas import Input

Category = Literal["scope", "window", "condition", "exception", "responsibility"]
PATTERNS = {
    "scope": r"适用|仅限|范围|applicable|applies to|only for",
    "window": r"[0-9零一二三四五六七八九十百]+\s*(?:天|日|小时|days?)|期限|deadline",
    "condition": r"须|必须|应当|未使用|拆封|开封|must|unused|required",
    "exception": r"除外|例外|不包括|但|另有|except|unless|however",
    "responsibility": r"运费|承担|负责|shipping fee|responsib",
}


class SourceSpan(Input):
    chunk_id: str = Field(min_length=1, max_length=80)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=2000)


class PolicyCard(Input):
    id: str = Field(pattern=r"^c[0-9]{1,4}$")
    category: Category
    statement: str = Field(min_length=1, max_length=2000)
    sources: list[SourceSpan] = Field(min_length=1, max_length=4)


class NoteInput(Input):
    note: str = Field(min_length=5, max_length=500)

    @field_validator("note")
    @classmethod
    def meaningful_note(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("A meaningful review note is required")
        return value.strip()


class CandidateEdit(NoteInput):
    expected_revision: int = Field(ge=1)
    cards: list[PolicyCard] = Field(min_length=1, max_length=100)
    note: str = Field(min_length=5, max_length=500)


class CandidateAction(NoteInput):
    expected_revision: int = Field(ge=1)
    note: str = Field(min_length=5, max_length=500)
    acknowledge_warnings: bool = False


class CandidateReview(CandidateAction):
    action: Literal["approve", "reject"]


def latest(session, version_id):
    return session.scalar(
        select(PolicyExtraction)
        .where(PolicyExtraction.version_id == version_id)
        .order_by(PolicyExtraction.number.desc())
        .limit(1)
    )


def source(session, actor, version_id, settings):
    version = scoped_version(session, actor, version_id)
    doc = scoped_document(session, actor, version.document_id, lock=True)
    if (
        version.status != "ready"
        or version.policy_scope != "returns"
        or (version.parse_metadata or {}).get("knowledge_kind") == "faq"
    ):
        raise HTTPException(409, "An indexed return-policy document is required")
    if settings is None:
        raise HTTPException(409, "Source settings are required for extraction publication")
    if version.effective_until and version.effective_until < now().date():
        raise HTTPException(409, "Policy has expired")
    if version.effective_from and version.effective_from > now().date():
        raise HTTPException(409, "Policy is not effective yet")
    reviewable_blocks(session, version, settings)  # Verify immutable original/parse snapshot.
    rows = session.scalars(
        select(KnowledgeChunk)
        .where(
            KnowledgeChunk.version_id == version.id,
            KnowledgeChunk.organization_id == actor.organization_id,
        )
        .order_by(KnowledgeChunk.position)
        .limit(201)
    ).all()
    if not rows or len(rows) > 200 or sum(len(r.content) for r in rows) > 200000:
        raise HTTPException(413, "Policy exceeds extraction limit; split into reviewed documents")
    digest = binding_digest(version, rows)
    return version, doc, rows, digest


def binding_digest(version, rows):
    return fingerprint(
        {
            "original": version.content_hash,
            "config": version.config_hash,
            "chunks": [{"id": r.id, "text": r.content, "section": r.section} for r in rows],
        }
    )


def risks(cards, rows):
    found = {c["category"] for c in cards}
    unknown = [k for k in PATTERNS if k not in found]
    window_entries = [
        {"value": v, "chunk_ids": sorted({s["chunk_id"] for s in c["sources"]})}
        for c in cards
        if c["category"] == "window"
        for v in re.findall(r"\d+\s*(?:天|日|days?)", c["statement"], re.I)
    ]
    window_values = sorted({e["value"] for e in window_entries})
    conflict_cues = []
    # Distinct limits are a review cue, never proof that the rules contradict each other.
    if len(window_values) > 1:
        conflict_cues.append(
            {
                "kind": "different_windows",
                "values": window_values,
                "chunk_ids": sorted({cid for e in window_entries for cid in e["chunk_ids"]}),
                "meaning": "同一提取结果出现不同期限，可能分属不同适用分支；必须人工核对各自范围，不能直接判定为矛盾",
            }
        )
    return {
        "unknown_categories": unknown,
        "review_flags": (["multiple_windows_require_scope_review"] if len(window_values) > 1 else [])
        + (["no_exception_identified_not_proof_of_absence"] if "exception" not in found else []),
        "conflict_cues": conflict_cues,
        "references": sorted(
            set(
                re.findall(
                    r"第[零一二三四五六七八九十百0-9]+条", "\n".join(r.content for r in rows)
                )
            )
        ),
    }


def validate_cards(cards, rows):
    values = [PolicyCard.model_validate(c).model_dump() for c in cards]
    if len({c["id"] for c in values}) != len(values):
        raise HTTPException(422, "Card identifiers must be distinct")
    by_id = {r.id: r for r in rows}
    for card in values:
        if not card["statement"].strip():
            raise HTTPException(422, "Card text must not be blank")
        for ref in card["sources"]:
            row = by_id.get(ref["chunk_id"])
            if (
                row is None
                or ref["end"] <= ref["start"]
                or ref["end"] > len(row.content)
                or row.content[ref["start"] : ref["end"]] != ref["quote"]
            ):
                raise HTTPException(422, "Source quote or location does not match this version")
    return values


def view(row, *, stale=False, sources=None):
    result = {
        "id": row.id,
        "version_id": row.version_id,
        "number": row.number,
        "revision": row.revision,
        "status": row.status,
        "proposed_by": row.proposed_by,
        "reviewed_by": row.reviewed_by,
        "review_note": row.review_note,
        "payload": row.payload,
        "stale": stale,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "business_rule_activation": False,
    }
    if sources is not None:
        result["sources"] = [
            {"id": r.id, "content": r.content, "section": r.section, "page": r.page_start}
            for r in sources
        ]
    return result


def scoped(session, actor, candidate_id):
    row = session.scalar(
        select(PolicyExtraction)
        .where(
            PolicyExtraction.id == candidate_id,
            PolicyExtraction.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Policy candidate not found")
    return row


def unchanged(row, version, doc, digest):
    if row.source_hash != digest or doc.active_version_id != row.base_active_id:
        raise HTTPException(409, "Source or published version changed; extract and review again")


def create(session, actor, version_id, settings):
    publication_lock(session, actor)
    version, doc, rows, digest = source(session, actor, version_id, settings)
    previous = latest(session, version.id)
    if (
        previous
        and previous.status in {"draft", "pending", "approved"}
        and previous.source_hash == digest
        and previous.base_active_id == doc.active_version_id
    ):
        return view(previous, sources=rows)
    cards, seen = [], set()
    for row in rows:
        for match in re.finditer(r"[^。！？\n.!?]+[。！？.!?]?", row.content):
            quote = match.group().strip()
            if not quote or quote.startswith("#") or len(quote) > 2000:
                continue
            start = row.content.find(quote, match.start())
            for category, pattern in PATTERNS.items():
                if re.search(pattern, quote, re.I) and (category, quote) not in seen:
                    seen.add((category, quote))
                    cards.append(
                        {
                            "id": "c" + str(len(cards) + 1),
                            "category": category,
                            "statement": quote,
                            "sources": [
                                {
                                    "chunk_id": row.id,
                                    "start": start,
                                    "end": start + len(quote),
                                    "quote": quote,
                                }
                            ],
                        }
                    )
    if len(cards) > 100:
        raise HTTPException(413, "Too many candidates; split the policy before review")
    payload = {
        "cards": cards,
        "original_cards": cards,
        **risks(cards, rows),
        "method": "rule_assisted_excerpts_v1",
        "automatic_semantic_extraction": False,
        "maintenance_note": "",
        "source_origin": (version.parse_metadata or {}).get("text_origin", "parsed_original"),
    }
    row = PolicyExtraction(
        organization_id=actor.organization_id,
        version_id=version.id,
        number=(previous.number + 1 if previous else 1),
        proposed_by=actor.id,
        payload=payload,
        source_hash=digest,
        base_active_id=doc.active_version_id,
    )
    session.add(row)
    session.flush()
    audit(session, actor, "policy.extracted", row.id, method=payload["method"], cards=len(cards))
    session.commit()
    return view(row, sources=rows)


def update(session, actor, candidate_id, body, settings, *, submit=False):
    publication_lock(session, actor)
    row = scoped(session, actor, candidate_id)
    if (
        row.status != "draft"
        or row.revision != body.expected_revision
        or latest(session, row.version_id).id != row.id
    ):
        raise HTTPException(409, "Candidate changed or is no longer editable")
    version, doc, rows, digest = source(session, actor, row.version_id, settings)
    unchanged(row, version, doc, digest)
    cards = validate_cards([c.model_dump() for c in body.cards], rows)
    previous_cards = row.payload["cards"]
    row.payload = {
        **row.payload,
        "last_edited_by": actor.id,
        "cards": cards,
        **risks(cards, rows),
        "maintenance_note": body.note,
    }
    row.status = "pending" if submit else "draft"
    row.revision += 1
    row.updated_at = now()
    audit(
        session,
        actor,
        "policy.submitted" if submit else "policy.edited",
        row.id,
        revision=row.revision,
        before_cards=previous_cards,
        after_cards=cards,
    )
    session.commit()
    return view(row, sources=rows)


def review(session, actor, candidate_id, body, settings):
    publication_lock(session, actor)
    row = scoped(session, actor, candidate_id)
    if (
        row.status != "pending"
        or row.revision != body.expected_revision
        or latest(session, row.version_id).id != row.id
    ):
        raise HTTPException(409, "Candidate is not awaiting this review")
    if actor.id in {row.proposed_by, row.payload.get("last_edited_by")}:
        raise HTTPException(403, "Independent reviewer required")
    version, doc, rows, digest = source(session, actor, row.version_id, settings)
    unchanged(row, version, doc, digest)
    validate_cards(row.payload["cards"], rows)
    if body.action == "approve" and not body.acknowledge_warnings:
        raise HTTPException(
            422, "Confirm scope, exceptions, unknowns and source coverage before approval"
        )
    row.status = "approved" if body.action == "approve" else "rejected"
    row.reviewed_by = actor.id
    row.review_note = body.note
    row.revision += 1
    row.updated_at = now()
    audit(session, actor, "policy." + row.status, row.id, revision=row.revision)
    session.commit()
    return view(row, sources=rows)


def publication_candidate(session, actor, version, doc, settings):
    row = latest(session, version.id)
    if row is None:
        return None
    if row.status == "published" and doc.active_version_id == version.id:
        _, _, _, digest = source(session, actor, version.id, settings)
        if digest != row.source_hash:
            raise HTTPException(409, "Published candidate source changed")
        return row
    if row.status != "approved":
        raise HTTPException(
            409, "Policy candidate requires independent approval before publication"
        )
    _, _, rows, digest = source(session, actor, version.id, settings)
    unchanged(row, version, doc, digest)
    validate_cards(row.payload["cards"], rows)
    return row
