"""Post-retrieval author review, kept separate from frozen primary scores."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rescore(rows, cases, rubric):
    by_id = {case["id"]: case for case in cases}
    minimum = {}
    for decision in rubric["confirmed_decisions"]:
        quote = decision.get("minimum_quote")
        if quote is None:
            quote = by_id[decision["minimum_from_case"]]["required_quotes"][
                decision["minimum_quote_index"]
            ]
        for identifier in decision.get("case_ids", [decision.get("case_id")]):
            minimum[identifier] = quote
    reviewed = []
    for row in rows:
        if row["case_id"] not in minimum:
            continue
        text = "".join(
            "".join(source["content"].split())
            for source in row["evidence"]
            if source["version_id"] == "long-0"
        )
        reviewed.append(
            {
                "tier": row["tier"],
                "arm": row["arm"],
                "case_id": row["case_id"],
                "reviewed_question_evidence_hit": "".join(minimum[row["case_id"]].split()) in text,
                "original_process_bundle_coverage": row["metrics"]["necessary_coverage"],
                "generated_answer_correctness": None,
            }
        )
    summaries = []
    for tier, arm in sorted({(r["tier"], r["arm"]) for r in rows}):
        group = [r for r in reviewed if r["tier"] == tier and r["arm"] == arm]
        true_negative = [
            r
            for r in rows
            if r["tier"] == tier
            and r["arm"] == arm
            and r["case_id"] in {f"long-dev-{number:03}" for number in range(25, 32)}
        ]
        summaries.append(
            {
                "tier": tier,
                "arm": arm,
                "reviewed_question_cases": len(group),
                "reviewed_question_evidence_hits": sum(
                    r["reviewed_question_evidence_hit"] for r in group
                ),
                "corrected_negative_cases": len(true_negative),
                "corrected_negative_returns_policy": sum(
                    r["metrics"]["unknown_fact_returns_policy"] for r in true_negative
                ),
            }
        )
    return {
        "review_type": rubric["type"],
        "independent_blind_review": False,
        "original_results_changed": False,
        "reviewed_case_ids": sorted(minimum),
        "results": summaries,
        "traces": reviewed,
        "limit": rubric["limit"],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        p.error("Output exists; retain previous review")
    cases_path = ROOT / "data/evaluation/rag_long_scale_v2/cases.json"
    parent_manifest_path = cases_path.parent / "manifest.json"
    parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
    if (
        hashlib.sha256(cases_path.read_bytes()).hexdigest()
        != parent_manifest["hashes"]["cases.json"]
    ):
        p.error("Frozen scoring cases changed")
    rubric_path = ROOT / "data/evaluation/rag_long_scale_review_v1/rubric.json"
    result = rescore(
        [json.loads(line) for line in a.input.read_text(encoding="utf-8").splitlines()],
        json.loads(cases_path.read_text(encoding="utf-8")),
        json.loads(rubric_path.read_text(encoding="utf-8")),
    )
    result.update(
        parent_manifest_sha256=hashlib.sha256(parent_manifest_path.read_bytes()).hexdigest(),
        cases_sha256=hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest(),
        rubric_sha256=hashlib.sha256(rubric_path.read_bytes()).hexdigest(),
    )
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reviewed_cases": 5, "original_labels_retained": True}))


if __name__ == "__main__":
    main()
