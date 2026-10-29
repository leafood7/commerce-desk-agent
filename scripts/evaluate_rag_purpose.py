"""Frozen-evidence strategy replay; no retrieval, model, or provider calls."""

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from workbench.return_context import select_policy_context  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output, task_ids=None):
    data = ROOT / "data/evaluation/rag_purpose_v1"
    frozen = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in frozen["hashes"].items():
        if sha(data / name) != digest:
            raise ValueError("Frozen purpose fixture changed")
    parent = ROOT / "docs/fullstack/rag-long-scale-evaluation/full/traces.jsonl"
    cases_file = ROOT / "data/evaluation/rag_long_scale_v2/cases.json"
    rubric_file = ROOT / "data/evaluation/rag_long_scale_review_v1/rubric.json"
    for path, key in [
        (parent, "parent_trace_sha256"),
        (cases_file, "parent_cases_sha256"),
        (rubric_file, "rubric_sha256"),
        (ROOT / "docs/fullstack/rag-purpose-protocol.md", "protocol_sha256"),
    ]:
        if sha(path) != frozen[key]:
            raise ValueError("Frozen parent evidence or protocol changed")
    tasks = json.loads((data / "tasks.json").read_text(encoding="utf-8"))
    valid = {task["task_id"] for task in tasks}
    if task_ids and (len(task_ids) != len(set(task_ids)) or set(task_ids) - valid):
        raise ValueError("Invalid or duplicate task IDs")
    if output.exists():
        raise ValueError("Output exists; retain previous runs")
    selected_tasks = [task for task in tasks if not task_ids or task["task_id"] in task_ids]
    catalog = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in json.loads(cases_file.read_text(encoding="utf-8"))}
    rubric = json.loads(rubric_file.read_text(encoding="utf-8"))
    minimum = {}
    for criterion in rubric["confirmed_decisions"]:
        quote = criterion.get("minimum_quote")
        if quote is None:
            quote = cases[criterion["minimum_from_case"]]["required_quotes"][
                criterion["minimum_quote_index"]
            ]
        for identifier in criterion.get("case_ids", [criterion.get("case_id")]):
            minimum[identifier] = quote
    source_rows = [
        row
        for row in (json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines())
        if row["arm"] in {"scoped_struct600", "scoped_references"}
    ]
    rows = []
    output.mkdir(parents=True)
    for task in selected_tasks:
        case = cases[task["source_case_id"]]
        policy = catalog[case["latest_binding"]]
        required = [clause["quote"] for clause in policy["clauses"]]
        other = catalog[
            "no_reason" if case["latest_binding"] == "quality_issue" else "quality_issue"
        ]
        excluded = [c["quote"] for c in other["clauses"] if c["quote"] not in required]
        for parent_row in [r for r in source_rows if r["case_id"] == case["id"]]:
            raw = parent_row["evidence"]
            refs = [
                {"chunk_id": match["id"], "purpose": clause["purpose"], "quote": clause["quote"]}
                for match in raw
                if match["version_id"] == policy["version_id"]
                for clause in policy["clauses"]
                if clause["quote"] in match["content"]
            ]
            before = "\n".join(m["content"] for m in raw if m["version_id"] == policy["version_id"])
            for strategy in ["raw", "forced_application", "purpose_routed"]:
                if strategy == "raw":
                    context = raw
                    purpose = "unfiltered_control"
                else:
                    purpose = "application" if strategy == "forced_application" else task["purpose"]
                    context = select_policy_context(raw, refs, purpose=purpose)["context"]
                text = "\n".join(
                    m["content"] for m in context if m["version_id"] == policy["version_id"]
                )
                minimum_quote = (
                    minimum.get(case["id"]) if task["purpose"] == "policy_question" else None
                )
                row = {
                    "task_id": task["task_id"],
                    "source_case_id": case["id"],
                    "task_purpose": task["purpose"],
                    "selected_purpose": purpose,
                    "strategy": strategy,
                    "tier": parent_row["tier"],
                    "retrieval_arm": parent_row["arm"],
                    "business_binding": case["latest_binding"],
                    "metrics": {
                        "application_bundle_coverage": sum(q in text for q in required)
                        / len(required)
                        if task["purpose"] == "application"
                        else None,
                        "other_branch_full_quotes": sum(q in text for q in excluded),
                        "reviewed_question_evidence_hit": minimum_quote in text
                        if minimum_quote
                        else None,
                        "reviewed_evidence_newly_lost": bool(
                            minimum_quote and minimum_quote in before and minimum_quote not in text
                        ),
                        "empty": not context,
                        "characters": sum(len(m["content"]) for m in context),
                    },
                    "retrieved_ids": [m["id"] for m in raw],
                    "context": context,
                }
                rows.append(row)
    summary = {
        "tasks": len(selected_tasks),
        "rows": len(rows),
        "expected_rows": len(selected_tasks) * 6 * 3,
        "new_model_calls": 0,
        "new_retrieval_calls": 0,
        "results": [],
        "limits": [
            "Explicit author task metadata, not automatic language routing",
            "Same development evidence reused, not independent evaluation",
            "Evidence loss is a potential refusal risk, not measured generated refusal rate",
            "Other-branch quotes are errors only in application context, not automatically in rule questions",
        ],
    }
    for tier, arm, strategy, purpose in sorted(
        {(r["tier"], r["retrieval_arm"], r["strategy"], r["task_purpose"]) for r in rows}
    ):
        group = [
            r
            for r in rows
            if (r["tier"], r["retrieval_arm"], r["strategy"], r["task_purpose"])
            == (tier, arm, strategy, purpose)
        ]
        reviewed = [r for r in group if r["metrics"]["reviewed_question_evidence_hit"] is not None]
        app = [r for r in group if r["metrics"]["application_bundle_coverage"] is not None]
        summary["results"].append(
            {
                "tier": tier,
                "retrieval_arm": arm,
                "strategy": strategy,
                "purpose": purpose,
                "tasks": len(group),
                "reviewed_questions": len(reviewed),
                "question_evidence_hits": sum(
                    r["metrics"]["reviewed_question_evidence_hit"] for r in reviewed
                ),
                "reviewed_evidence_newly_lost": sum(
                    r["metrics"]["reviewed_evidence_newly_lost"] for r in group
                ),
                "application_wrong_branch_tasks": sum(
                    r["metrics"]["other_branch_full_quotes"] > 0 for r in app
                ),
                "full_application_bundles": sum(
                    r["metrics"]["application_bundle_coverage"] == 1 for r in app
                ),
                "application_mean_coverage": statistics.mean(
                    r["metrics"]["application_bundle_coverage"] for r in app
                )
                if app
                else None,
                "empty_tasks": sum(r["metrics"]["empty"] for r in group),
                "mean_chars": statistics.mean(r["metrics"]["characters"] for r in group),
            }
        )
    (output / "traces.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                **frozen,
                "source_sha256": sha(Path(__file__)),
                "context_selector_sha256": sha(ROOT / "src/workbench/return_context.py"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"rows": len(rows), "new_model_calls": 0, "new_retrieval_calls": 0}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--task-id", action="append")
    a = p.parse_args()
    try:
        run(a.output, a.task_id)
    except ValueError as error:
        p.error(str(error))


if __name__ == "__main__":
    main()
