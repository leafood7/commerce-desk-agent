"""Bounded source expansion. No model text becomes evidence."""


def expand_context(anchors, documents, budget, mode="neighbors"):
    if mode not in {"neighbors", "section"}:
        raise ValueError("Unknown context mode")
    selected = []
    used = set()
    chars = 0
    for row in anchors:
        if row["id"] not in used and chars + len(row["content"]) <= budget:
            selected.append(row)
            used.add(row["id"])
            chars += len(row["content"])
    for anchor in list(selected):
        candidates = [
            row
            for row in documents
            if row["version_id"] == anchor["version_id"]
            and row.get("organization_id") == anchor.get("organization_id")
            and 0 < abs(row["position"] - anchor["position"]) <= (2 if mode == "section" else 1)
            and (
                mode != "section"
                or not anchor.get("section")
                or row.get("section") == anchor["section"]
            )
        ]
        for row in sorted(
            candidates, key=lambda x: (abs(x["position"] - anchor["position"]), x["position"])
        ):
            if row["id"] not in used and chars + len(row["content"]) <= budget:
                selected.append({**row, "context_of": anchor["id"]})
                used.add(row["id"])
                chars += len(row["content"])
    return selected
