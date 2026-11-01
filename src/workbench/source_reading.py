"""Server-owned read targets and current-original evidence for return investigation."""

from workbench.domain import fingerprint
from workbench.knowledge_schemas import ReadInput

MAX_READS = 2
MAX_CHARS = 4000


def reading_evidence(result, body):
    request = body.model_dump(mode="json")
    return {
        "id": "read-" + fingerprint({"request": request, "source": result["source_sha256"]}),
        "source_kind": "source_read",
        "text_origin": result.get("text_origin", result.get("source_kind", "parsed_original")),
        "read_request": request,
        "version_id": result["version_id"],
        "version": result["version"],
        "document_id": result["document_id"],
        "title": result["title"],
        "filename": result["filename"],
        "section": result["section"],
        "policy_scope": result["policy_scope"],
        "parent_id": result["parent_id"],
        "content": result["content"],
        "audience": result["audience"],
        "page_start": None,
        "page_end": None,
        "offset": result["offset"],
        "next_offset": result["next_offset"],
        "complete": result["complete"],
        "covered_chunk_ids": result["covered_chunk_ids"],
    }


def target(version, parent, section, *, offset=0, reason="matched_section", audience=None):
    body = ReadInput(
        version_id=version, parent_id=parent, offset=offset, max_chars=2000, audience=audience
    )
    return {
        "id": fingerprint(body.model_dump(mode="json")),
        "request": body.model_dump(mode="json"),
        "section": section,
        "reason": reason,
    }


def search_targets(matches):
    result = {}
    for row in matches:
        if row.get("parent_id") and not row.get("parent_complete"):
            item = target(
                row["version_id"],
                row["parent_id"],
                row.get("section", ""),
                offset=max(0, row.get("parent_match_offset", 0) - 250),
                reason="matched_position" if row.get("parent_match_offset", 0) > 250 else "matched_section",
                audience=row.get("audience"),
            )
            result[item["id"]] = item
        for related in row.get("related_sections", []):
            item = target(
                row["version_id"],
                related["parent_id"],
                related["section"],
                reason="explicit_article_link",
                audience=row.get("audience"),
            )
            result[item["id"]] = item
    complete = {(r["version_id"], r.get("parent_id")) for r in matches if r.get("parent_complete")}
    return [
        r
        for r in result.values()
        if (r["request"]["version_id"], r["request"]["parent_id"]) not in complete
    ][:8]


def next_targets(result, body, previous):
    remaining = [r for r in previous if r["id"] != fingerprint(body.model_dump(mode="json"))]
    extra = []
    if result["next_offset"] is not None:
        extra.append(
            target(
                body.version_id,
                body.parent_id,
                result["section"],
                offset=result["next_offset"],
                reason="continue_section",
                audience=body.audience,
            )
        )
    for related in result["related_sections"]:
        extra.append(
            target(
                body.version_id,
                related["parent_id"],
                related["section"],
                reason="explicit_article_link",
                audience=body.audience,
            )
        )
    return list({r["id"]: r for r in [*extra, *remaining]}.values())[:8]


def available_targets(state):
    if (
        state.get("source_read_count", 0) >= MAX_READS
        or state.get("source_read_chars", 0) >= MAX_CHARS
    ):
        return []
    attempted = set(state.get("source_attempted_targets", []))
    complete = {
        (e["version_id"], e.get("parent_id"))
        for e in state.get("retrieved_evidence", [])
        if e.get("parent_complete") or e.get("complete")
    }
    return [
        r
        for r in state.get("source_targets", [])
        if r["id"] not in attempted
        and (r["request"]["version_id"], r["request"]["parent_id"]) not in complete
    ]


def required_ids(state):
    """Question-level requirements are separate from the whole application checklist."""
    preview = state.get("preview") or {}
    sources = preview.get("evidence", {}).get("sources", [])
    if state.get("context_purpose") != "policy_question":
        return {s["id"] for s in sources}
    from workbench.return_fact_checks import question_requirements

    query = state.get("query", "")
    policy_words = ("规则", "政策", "条件", "例外", "期限", "资格", "适用", "范围", "几天")
    if question_requirements(query)["requested"] and not any(w in query for w in policy_words):
        return set()  # Actual fields cannot be established by more policy reading.
    purposes = set()
    if any(w in query for w in ("期限", "时限", "多久", "几天", "七天", "7天", "超过", "窗口")):
        purposes.add("window")
    if any(w in query for w in ("使用", "拆封", "条件", "状态", "限制", "例外", "质量", "缺陷")):
        purposes.update(("condition", "quality", "scope"))
    if any(w in query for w in ("商品", "适用", "范围")):
        purposes.add("scope")
    clauses = preview.get("evidence", {}).get("clauses", [])
    selected = [
        c
        for c in clauses
        if not purposes or c.get("purpose", "").removesuffix("_clause") in purposes
    ]
    return {c["chunk_id"] for c in selected}


def inquiry_actions(state, original):
    if not (
        state.get("order_read") and state.get("return_checked") and state.get("policy_searched")
    ):
        return original
    missing = required_ids(state) - set(state.get("retrieved_ids", []))
    if not missing:
        return original
    choices = []
    if available_targets(state):
        choices.append("read_source")
    if state.get("source_search_count", 0) < 1:
        choices.append("supplement")
    return choices or ["stop"]


def reserve_source_io(database, run_id, kind, guard):
    """Persist before I/O so a lost checkpoint cannot restore spent read allowance."""
    from sqlalchemy import select

    from workbench.flow_models import FlowAction
    from workbench.mcp_gateway import ToolFailure
    from workbench.models import Run

    with database.sessions() as session:
        run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        guard(session, run)
        if run.status != "running":
            raise ToolFailure("cancelled")
        row = session.get(FlowAction, (run_id, "__source_budget__"))
        values = dict(row.body) if row else {"reads": 0, "supplements": 0}
        if values[kind] >= (2 if kind == "reads" else 1):
            raise ToolFailure("source_budget_exhausted")
        values[kind] += 1
        if row:
            row.body = values
        else:
            session.add(
                FlowAction(run_id=run_id, key="__source_budget__", input_hash="v1", body=values)
            )
        session.commit()
