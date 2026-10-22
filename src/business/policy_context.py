"""从命中的政策文件补充真实上下文，不改变原始片段与重排分数。"""

CONTEXT_CHAR_BUDGET = 1800


def flatten_policy_matches(matches):
    """将锚点和同源上下文展开成可审计证据，按真实片段ID去重。"""
    evidence = {}
    for anchor in matches or []:
        if not isinstance(anchor, dict) or not isinstance(anchor.get("id"), str):
            continue
        evidence.setdefault(anchor["id"], {k: v for k, v in anchor.items() if k != "context_chunks"})
        for item in anchor.get("context_chunks", []) or []:
            if (isinstance(item, dict)
                    and all(isinstance(item.get(k), str) for k in ["id", "source", "content"])
                    and item["source"] == anchor.get("source")):
                evidence.setdefault(item["id"], {**item, "context_of": anchor["id"]})
    return list(evidence.values())


def expand_policy_context(anchors, documents, char_budget=CONTEXT_CHAR_BUDGET):
    """在全局正文预算内轮流补充同源邻段，保留锚点ID、原文和分数。"""
    if char_budget < 0:
        raise ValueError("上下文预算不能为负数。")
    result = [{**row, "context_chunks": []} for row in anchors]
    used_ids = {row["id"] for row in anchors}
    used_chars = sum(len(row["content"]) for row in anchors)
    queues = []
    seen_sources = set()
    for index, anchor in enumerate(anchors):
        source = anchor["source"]
        if source in seen_sources:
            continue
        seen_sources.add(source)
        # 片段ID来自切块函数，末尾数字对应原文件中的段落位置。
        siblings = sorted([row for row in documents if row["source"] == source],
                          key=lambda row: (int(row["id"].rsplit("-", 1)[-1])
                                           if row["id"].rsplit("-", 1)[-1].isdigit() else -1, row["id"]))
        positions = [i for i, row in enumerate(siblings) if row["id"] in used_ids]
        if not positions:
            continue
        choices = sorted(enumerate(siblings), key=lambda pair: (
            min(abs(pair[0] - position) for position in positions), pair[0]))
        queues.append((index, [row for _, row in choices if row["id"] not in used_ids]))
    while any(queue for _, queue in queues):
        for index, queue in queues:
            if not queue:
                continue
            row = queue.pop(0)
            if row["id"] in used_ids or used_chars + len(row["content"]) > char_budget:
                continue
            result[index]["context_chunks"].append({k: row[k] for k in ["id", "source", "content"]})
            used_ids.add(row["id"])
            used_chars += len(row["content"])
    return result
