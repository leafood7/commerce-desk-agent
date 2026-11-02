"""Server-owned question tasks and source-bound collaboration handoff, not policy truth."""

import re

from sqlalchemy import select

from workbench.domain import fingerprint
from workbench.knowledge_models import Document, DocumentVersion, KnowledgeChunk, PolicyExtraction
from workbench.policy_extraction import PATTERNS, binding_digest
from workbench.source_reading import search_targets, target

LABELS = {
    "scope": "适用范围",
    "window": "退货期限",
    "condition": "适用条件",
    "exception": "例外条款",
    "responsibility": "费用与责任",
    "overview": "所问政策",
}
TRIGGERS = {
    "scope": ("适用", "范围", "哪些商品", "哪些货", "地区", "渠道"),
    "window": ("期限", "几天", "多久", "时限", "超过", "七天", "7天", "window", "deadline"),
    "condition": (
        "条件",
        "要求",
        "限制",
        "未使用",
        "拆封",
        "开封",
        "激活",
        "缺陷确认",
        "照片",
        "质检",
        "unused",
    ),
    "exception": ("例外", "除外", "定制", "特殊情况", "豁免", "exception"),
    "responsibility": ("运费", "谁出", "谁付", "费用", "承担", "shipping fee"),
}


def plan_question(query):
    tasks = []
    for category, words in TRIGGERS.items():
        trigger = next((w for w in words if w.lower() in query.lower()), None)
        if trigger:
            tasks.append(
                {
                    "id": "t" + str(len(tasks) + 1),
                    "category": category,
                    "label": LABELS[category],
                    "trigger": trigger,
                }
            )
    return tasks or [
        {"id": "t1", "category": "overview", "label": LABELS["overview"], "trigger": ""}
    ]


def current_annotations(session, actor, matches):
    """Read reviewed categories/locations only. Statements never become model evidence."""
    result = []
    versions = sorted({r["version_id"] for r in matches})
    for version_id in versions:
        version = session.get(DocumentVersion, version_id)
        if version is None or version.organization_id != actor.organization_id:
            continue
        document = session.get(Document, version.document_id)
        pointer = (version.parse_metadata or {}).get("policy_extraction_id")
        row = session.get(PolicyExtraction, pointer) if pointer else None
        if (
            not row
            or row.status != "published"
            or row.version_id != version.id
            or row.organization_id != actor.organization_id
            or document.active_version_id != version.id
        ):
            continue
        chunks = session.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.version_id == version.id,
                KnowledgeChunk.organization_id == actor.organization_id,
            )
            .order_by(KnowledgeChunk.position)
            .limit(201)
        ).all()
        if len(chunks) > 200 or binding_digest(version, chunks) != row.source_hash:
            continue
        parents = (version.parse_metadata or {}).get("parent_child", {})
        by_id = {c.id: c for c in chunks}
        for card in row.payload.get("cards", []):
            valid = []
            for ref in card["sources"]:
                chunk = by_id.get(ref["chunk_id"])
                if (
                    chunk is None
                    or ref["end"] > len(chunk.content)
                    or chunk.content[ref["start"] : ref["end"]] != ref["quote"]
                ):
                    valid = []
                    break
                parent_id = parents.get("children", {}).get(str(chunk.position))
                parent = next((p for p in parents.get("parents", []) if p["id"] == parent_id), None)
                valid.append(
                    {
                        "chunk_id": chunk.id,
                        "quote": ref["quote"],
                        "parent_id": parent_id,
                        "section": chunk.section,
                        "offset": max(0, (parent or {}).get("text", "").find(ref["quote"]) - 200),
                    }
                )
            if valid:
                result.append(
                    {
                        "category": card["category"],
                        "version_id": version.id,
                        "candidate_id": row.id,
                        "revision": row.revision,
                        "sources": valid,
                    }
                )
    return result


def evidence_ids(matches):
    return {key for row in matches for key in [row["id"], *row.get("covered_chunk_ids", [])]}


def annotation_matches(matches, annotation, ref):
    return [
        r["id"]
        for r in matches
        if r["version_id"] == annotation["version_id"]
        and (
            ref["chunk_id"] == r["id"]
            or ref["chunk_id"] in r.get("covered_chunk_ids", [])
            or (
                r.get("source_kind") == "source_read"
                and ref.get("parent_id") == r.get("parent_id")
                and ref["quote"] in r["content"]
            )
        )
    ]


def board(tasks, matches, annotations, gaps):
    covered = evidence_ids(matches)
    entries = []
    for task in tasks:
        direct = []
        for row in matches:
            text = row["content"]
            if task["category"] == "overview" or re.search(
                PATTERNS.get(task["category"], r"(?!)"), text, re.I
            ):
                direct.append(row["id"])
        annotated = [a for a in annotations if a["category"] == task["category"]]
        needed = {s["chunk_id"] for a in annotated for s in a["sources"]}
        for row in matches:
            if set([row["id"], *row.get("covered_chunk_ids", [])]) & needed:
                direct.append(row["id"])
        acquired = set()
        for annotation in annotated:
            for ref in annotation["sources"]:
                actual = annotation_matches(matches, annotation, ref)
                if actual:
                    acquired.add(ref["chunk_id"])
                    direct.extend(actual)
        missing = sorted(needed - covered - acquired)
        entries.append(
            {
                **task,
                "evidence_ids": list(dict.fromkeys(direct)),
                "pending_chunk_ids": missing,
                "status": "needs_read" if missing else "candidate_found" if direct else "not_found",
            }
        )
    audiences = {}
    for row in matches:
        for key, value in row.get("audience", {}).items():
            if value:
                audiences.setdefault(key, set()).add(value)
    conflicts = [
        {"kind": "scope_difference", "dimension": key, "values": sorted(values)}
        for key, values in audiences.items()
        if len(values) > 1
    ]
    # Different time limits are a review cue, never proof that the rules contradict.
    windows = sorted(
        set(re.findall(r"\d+\s*(?:天|日|days?)", "\n".join(r["content"] for r in matches), re.I))
    )
    return {
        "tasks": entries,
        "scope_conflicts": conflicts,
        "review_cues": ["different_windows_check_branch_and_scope"] if len(windows) > 1 else [],
        "reference_gaps": gaps,
        "evidence_sufficiency": "not_proven_by_keyword_or_annotation",
        "unresolved": any(e["status"] != "candidate_found" for e in entries)
        or bool(conflicts)
        or any(gaps.values()),
    }


def reading_targets(tasks, matches, annotations):
    wanted = {t["category"]: t["id"] for t in tasks}
    result = []
    audience_by_version = {r["version_id"]: r.get("audience", {}) for r in matches}
    for annotation in annotations:
        if annotation["category"] not in wanted:
            continue
        for ref in annotation["sources"]:
            if annotation_matches(matches, annotation, ref) or not ref["parent_id"]:
                continue
            item = target(
                annotation["version_id"],
                ref["parent_id"],
                ref["section"],
                offset=ref["offset"],
                reason="reviewed_requested_clause",
                audience=audience_by_version.get(annotation["version_id"]),
            )
            item["task_id"] = wanted[annotation["category"]]
            result.append(item)
    for row in matches:
        if (
            row.get("source_kind") == "source_read"
            and row.get("next_offset") is not None
            and row.get("parent_id")
        ):
            result.append(
                target(
                    row["version_id"],
                    row["parent_id"],
                    row.get("section", ""),
                    offset=row["next_offset"],
                    reason="continue_section",
                    audience=row.get("audience"),
                )
            )
    result.extend(search_targets(matches))
    unique = {}
    for item in result:
        unique.setdefault(item["id"], item)
    return list(unique.values())[:8]


def compact_board(value):
    return {
        **value,
        "tasks": [
            {k: v for k, v in t.items() if k not in {"pending_chunk_ids"}} for t in value["tasks"]
        ],
    }


def board_digest(value):
    return fingerprint(value)
