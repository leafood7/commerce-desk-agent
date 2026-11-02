"""Immutable parent text and child provenance; source text is never generated."""

import re

from workbench.document_parsing import Block, chunks_from_blocks

ARTICLE = re.compile(r"第([一二三四五六七八九十百零0-9]+)条")


def article_key(text):
    match = ARTICLE.search(text)
    if not match:
        return None
    value = match[1]
    if value.isdigit():
        return int(value)
    digits = dict(zip("零一二三四五六七八九", range(10)))
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in {"十", "百"}:
            total += (current or 1) * (10 if char == "十" else 100)
            current = 0
        else:
            return None
    return total + current


def table_context_blocks(blocks, size):
    """Repeat verbatim header per child; oversize headers are explicitly limited."""
    result = []
    for block in blocks:
        header = getattr(block, "table_header", None)
        rows = [block.text]
        if not header:
            lines = block.text.splitlines()
            if len(lines) >= 3 and "|" in lines[0] and re.fullmatch(r"[\s|:\-]+", lines[1]):
                header, rows = "\n".join(lines[:2]), lines[2:]
        if not header:
            result.append(block)
            continue
        capacity = size - len(header) - 2
        for row in rows:
            if capacity < 20:
                # Preserve all text; structure metadata signals this cannot guarantee complete header per child.
                result.append(Block(header + "\n" + row, block.page, block.section))
            else:
                for start in range(0, max(1, len(row)), capacity):
                    result.append(
                        Block(
                            header + "\n" + row[start : start + capacity], block.page, block.section
                        )
                    )
    return result


def parent_child_chunks(blocks, size=600, overlap=80):
    groups = []
    article = ""
    for number, block in enumerate(blocks):
        match = re.match(r"^(第[一二三四五六七八九十百零0-9]+条)(?:\s|　|$)", block.text)
        if match and not block.section:
            article = match[1]
        if not block.section and article:
            block = Block(block.text, block.page, article, block.table_header)
        key = (block.section, block.page if not block.section else None)
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "blocks": [], "block_start": number})
        groups[-1]["blocks"].append(block)
    chunks, parents, children = [], [], {}
    for group in groups:
        parent_id = f"p{len(parents)}"
        pieces = chunks_from_blocks(
            table_context_blocks(group["blocks"], size), size=size, overlap=overlap
        )
        positions = []
        for piece in pieces:
            piece["position"] = len(chunks)
            positions.append(piece["position"])
            children[str(piece["position"])] = parent_id
            chunks.append(piece)
        parents.append(
            {
                "id": parent_id,
                "section": group["blocks"][0].section,
                "text": "\n\n".join(b.text for b in group["blocks"]),
                "block_start": group["block_start"],
                "block_end": group["block_start"] + len(group["blocks"]),
                "positions": positions,
            }
        )
    by_article = {}
    for parent in parents:
        number = article_key(parent["section"])
        if number is not None:
            by_article.setdefault(number, []).append(parent["id"])
    for parent in parents:
        parent["links"], parent["unresolved_articles"] = [], []
        own = article_key(parent["section"])
        for mention in ARTICLE.finditer(parent["text"]):
            number = article_key(mention[0])
            if number == own:
                continue
            targets = by_article.get(number, [])
            if len(targets) == 1:
                if targets[0] not in parent["links"]:
                    parent["links"].append(targets[0])
            elif number not in parent["unresolved_articles"]:
                parent["unresolved_articles"].append(number)
        parent["links"] = parent["links"][:8]
    if len(chunks) > 2500:
        from workbench.document_parsing import IngestionError

        raise IngestionError("chunk_limit_exceeded")
    return chunks, {
        "schema": 1,
        "parents": parents,
        "children": children,
        "source_kind": "parsed_original",
        "complete_policy_claim": False,
        "table_header_limitations": [
            i for i, b in enumerate(blocks) if b.table_header and len(b.table_header) > size - 22
        ],
    }


def expand_parents(anchors, documents, budget):
    selected, seen, chars, limited = [], set(), 0, []
    for row in anchors:
        if row["id"] not in seen and chars + len(row["content"]) <= budget:
            selected.append(row)
            seen.add(row["id"])
            chars += len(row["content"])
    unresolved = []
    for anchor in list(selected):
        parent = anchor.get("parent_id")
        if not parent:
            continue
        targets = {parent, *anchor.get("parent_links", [])}
        unresolved.extend(anchor.get("unresolved_articles", []))
        candidates = [
            r
            for r in documents
            if r.get("parent_id") in targets
            and r["version_id"] == anchor["version_id"]
            and r.get("organization_id") == anchor.get("organization_id")
        ]
        for row in sorted(candidates, key=lambda r: r["position"]):
            if row["id"] in seen:
                continue
            if len(selected) >= 20 or chars + len(row["content"]) > budget:
                limited.append(row["id"])
            else:
                selected.append({**row, "context_of": anchor["id"]})
                seen.add(row["id"])
                chars += len(row["content"])
    return selected, {
        "mode": "parent_child",
        "limited_ids": list(dict.fromkeys(limited)),
        "parent_expansion_complete": not limited and not unresolved,
        "unresolved_articles": sorted(set(unresolved)),
        "link_policy": "explicit_same_version_article_mentions_one_hop",
        "policy_sufficiency": "not_established",
    }


def related_sections(parents, identifier):
    """Same-version explicit mentions in both directions, not inferred exceptions."""
    own = next((p for p in parents if p["id"] == identifier), {})
    links = []
    for parent in parents:
        if parent["id"] == identifier:
            continue
        outgoing = parent["id"] in own.get("links", [])
        incoming = identifier in parent.get("links", [])
        if outgoing or incoming:
            links.append(
                {
                    "parent_id": parent["id"],
                    "section": parent["section"],
                    "direction": "both"
                    if outgoing and incoming
                    else "outgoing"
                    if outgoing
                    else "incoming",
                }
            )
    return links
