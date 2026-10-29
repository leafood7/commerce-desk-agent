"""Offline authored development checks and historical call-gate replay. No network."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from workbench.return_decisions import needs_model  # noqa: E402
from workbench.return_fact_checks import question_requirements  # noqa: E402
from workbench.return_intake import condition_candidate  # noqa: E402

DATA = ROOT / "data/evaluation/return_intake_v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="*")
    args = parser.parse_args()
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["hashes"].items():
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != digest:
            parser.error("Frozen fixture hash mismatch: " + name)
    replay = ROOT / "docs/fullstack/return-agent-evaluation/model-requests.jsonl"
    if hashlib.sha256(replay.read_bytes()).hexdigest() != manifest["replay_sha256"]:
        parser.error("Historical replay hash mismatch")
    protocol = ROOT / "docs/fullstack/return-intake-protocol.md"
    if hashlib.sha256(protocol.read_bytes()).hexdigest() != manifest["protocol_sha256"]:
        parser.error("Frozen protocol hash mismatch")
    conditions = json.loads((DATA / "conditions.json").read_text(encoding="utf-8"))
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    valid = {c["id"] for c in conditions + questions}
    if args.ids and (set(args.ids) - valid):
        parser.error("Unknown sample IDs: " + ", ".join(sorted(set(args.ids) - valid)))
    traces = []
    for case in conditions + questions:
        if args.ids and case["id"] not in args.ids:
            continue
        if "text" in case:
            actual = condition_candidate(case["text"])
            passed = actual["candidate"] == case["candidate"] and actual["label"] == case["label"]
            if actual["source"]:
                source = actual["source"]
                passed &= case["text"][source["start"] : source["end"]] == source["quote"]
        else:
            actual = question_requirements(case["question"])
            passed = [r["field"] for r in actual["requested"]] == case[
                "requested_facts"
            ] and actual["read_only"] == case["read_only"]
        traces.append({"id": case["id"], "passed": passed, "actual": actual})
    calls = []
    for line in replay.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        observation = json.loads(row["request"]["messages"][-1]["content"])
        allowed = observation["allowed_actions"]
        calls.append(
            {
                "id": row["case_id"],
                "allowed_actions": allowed,
                "would_call_model": needs_model(allowed),
                "historical_charged_cny": row["accounting"]["charged_cny"],
            }
        )
    skipped = [c for c in calls if not c["would_call_model"]]
    result = {
        "version": "guided_v3",
        "split": "authored_development_only",
        "samples": len(traces),
        "passed": sum(t["passed"] for t in traces),
        "new_paid_calls": 0,
        "new_paid_cost_cny": 0,
        "historical_calls": len(calls),
        "counterfactual_skipped_calls": len(skipped),
        "counterfactual_historical_charge_cny": round(
            sum(c["historical_charged_cny"] for c in skipped), 8
        ),
        "limitations": "Offline gate replay, not measured new model performance, latency or savings. No ledger refund. Literal parsing and explicit read-only patterns only.",
        "traces": traces,
        "replay": calls,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"traces", "replay"}}, ensure_ascii=False
        )
    )
    return 0 if all(t["passed"] for t in traces) else 1


if __name__ == "__main__":
    raise SystemExit(main())
