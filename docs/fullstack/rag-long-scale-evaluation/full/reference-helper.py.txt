"""Bounded explicit same-version article expansion, never a semantic policy resolver."""

import re

ARTICLE = re.compile(r"^第([0-9]{1,3})条(?:\s|$)")
REFERENCES = re.compile(r"(?:参见|另见|详见)((?:第[0-9]{1,3}条[、，,；;\s]*)+)")


def article_number(row):
    match = ARTICLE.match(str(row.get("section") or "").split("/")[-1].strip())
    return int(match[1]) if match else None


def references(text):
    return list(
        dict.fromkeys(
            int(number)
            for match in REFERENCES.finditer(text)
            for number in re.findall(r"第([0-9]{1,3})条", match[1])
        )
    )[:8]


def expand_references(anchors, documents, budget, *, max_hops=2, max_chunks=8):
    if budget < 1 or not 0 <= max_hops <= 2 or not 1 <= max_chunks <= 8:
        raise ValueError("Invalid reference expansion bounds")
    allowed = {row["id"]: row for row in documents}
    selected, used, queue, trace = (
        [],
        set(),
        [],
        {"resolved": [], "missing": [], "limited": [], "mode": "explicit_article_references"},
    )
    chars = 0
    for anchor in anchors:
        source = allowed.get(anchor["id"])
        if (
            not source
            or source["version_id"] != anchor.get("version_id")
            or source.get("organization_id") != anchor.get("organization_id")
            or source["content"] != anchor.get("content")
        ):
            trace["limited"].append({"source_id": anchor.get("id"), "reason": "invalid_anchor"})
            continue
        if (
            source["id"] not in used
            and len(selected) < max_chunks
            and chars + len(source["content"]) <= budget
        ):
            selected.append(anchor)
            used.add(source["id"])
            chars += len(source["content"])
            queue.append((source, 0))
        elif source["id"] not in used:
            trace["limited"].append({"source_id": source["id"], "reason": "anchor_budget_limit"})
    visited = set()
    while queue:
        source, hop = queue.pop(0)
        for number in references(source["content"]):
            key = (source.get("organization_id"), source["version_id"], number)
            if key in visited:
                continue
            visited.add(key)
            detail = {
                "source_id": source["id"],
                "version_id": source["version_id"],
                "article": number,
            }
            if hop >= max_hops:
                trace["limited"].append({**detail, "reason": "hop_limit"})
                continue
            targets = sorted(
                [
                    row
                    for row in documents
                    if row["version_id"] == source["version_id"]
                    and row.get("organization_id") == source.get("organization_id")
                    and article_number(row) == number
                ],
                key=lambda row: row["position"],
            )
            if not targets:
                trace["missing"].append(detail)
                continue
            added = []
            limited = False
            for row in targets:
                if row["id"] in used:
                    continue
                if len(selected) >= max_chunks or chars + len(row["content"]) > budget:
                    limited = True
                    continue
                selected.append(
                    {
                        **row,
                        "context_of": source["id"],
                        "reference_article": number,
                        "reference_hop": hop + 1,
                    }
                )
                used.add(row["id"])
                chars += len(row["content"])
                queue.append((row, hop + 1))
                added.append(row["id"])
            trace["resolved"].append({**detail, "added_ids": added})
            if limited:
                trace["limited"].append({**detail, "reason": "chunk_or_character_limit"})
    trace.update(
        chars=chars, chunks=len(selected), complete=not trace["missing"] and not trace["limited"]
    )
    return {"matches": selected, "trace": trace}
