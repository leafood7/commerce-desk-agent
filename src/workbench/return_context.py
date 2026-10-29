"""Project operator-bound quotes from actual validated retrieval, never add a source."""

import hashlib


def project_policy_context(matches, clauses):
    output = []
    for match in matches:
        excerpts = []
        for clause in clauses:
            quote = clause["quote"]
            start = match["content"].find(quote)
            if clause["chunk_id"] == match["id"] and quote and start >= 0:
                excerpts.append(
                    {
                        "purpose": clause.get("purpose"),
                        "quote": quote,
                        "start": start,
                        "end": start + len(quote),
                    }
                )
        if excerpts:
            output.append(
                {
                    "id": match["id"],
                    "version_id": match["version_id"],
                    "content": "\n".join(dict.fromkeys(e["quote"] for e in excerpts)),
                    "source_sha256": hashlib.sha256(match["content"].encode()).hexdigest(),
                    "projection": "operator_bound_quotes",
                    "offset_unit": "unicode_codepoint",
                    "excerpts": excerpts,
                }
            )
    return output


def select_policy_context(matches, clauses, *, purpose):
    """Purpose is selected by trusted program routing, never a model tool argument."""
    if purpose not in {"application", "policy_question"}:
        raise ValueError("Unknown policy context purpose")
    context = (
        project_policy_context(matches, clauses) if purpose == "application" else list(matches)
    )
    retained = {row["id"] for row in context}
    return {
        "purpose": purpose,
        "selection": "current_branch_quotes"
        if purpose == "application"
        else "retrieved_policy_sources",
        "context": context,
        "retrieved_ids": [row["id"] for row in matches],
        "retained_ids": sorted(retained),
        "suppressed_ids": sorted({row["id"] for row in matches} - retained),
        "empty": not context,
        "does_not_establish_eligibility_or_actual_facts": True,
    }
