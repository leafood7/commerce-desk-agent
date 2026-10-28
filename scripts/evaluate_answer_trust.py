"""Answer trust evaluation. Default validation never imports or calls a model."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    run = commands.add_parser("run")
    run.add_argument("--online", action="store_true")
    run.add_argument("--split", choices=["dev", "holdout"], default="dev")
    run.add_argument("--ids")
    run.add_argument("--layers", default="fixed,mcp")
    run.add_argument("--variants", default="baseline,contract,guarded")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--output", required=True)
    run.add_argument("--ledger", default=str(ROOT / "work/answer-trust/budget.json"))
    run.add_argument("--baseline-root")
    run.add_argument("--candidate-lock")
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--output", required=True)
    freeze.add_argument("--scale", type=int, choices=[24, 48], default=48)
    export = commands.add_parser("export-review")
    export.add_argument("--run", required=True)
    export.add_argument("--output-dir", required=True)
    promotion = commands.add_parser("promotion")
    promotion.add_argument("--run", required=True)
    promotion.add_argument("--reviews", required=True)
    promotion.add_argument("--key", required=True)
    promotion.add_argument("--checks", required=True)
    promotion.add_argument("--output", required=True)
    estimate = commands.add_parser("estimate")
    estimate.add_argument("--calibration", required=True)
    estimate.add_argument("--scale", type=int, choices=[24, 48], default=48)
    return root

def main():
    args = parser().parse_args()
    from evaluation.answer_trust_data import (
        DATA, candidate_snapshot, digest, load_cases, read_json, validate_frozen, write_json)
    if args.command == "validate":
        manifest = validate_frozen()
        cases = load_cases("dev")
        print(json.dumps({"frozen_valid": True, "counts": manifest["counts"], "dev_validated": len(cases),
            "holdout_content_exposed": False, "model_calls": 0, "manifest_sha256": digest(DATA / "manifest.json")}))
    elif args.command == "run":
        from evaluation.answer_trust_runner import run
        result = asyncio.run(run(args))
        print(json.dumps({"status": result["metadata"]["status"], "rows": len(result["rows"]),
            "committed_cny": result["budget"]["committed_cny"], "review": "pending"}))
        return 0 if result["metadata"]["status"] == "complete" else 2
    elif args.command == "freeze":
        validate_frozen()
        dev = load_cases("dev")
        ids = [c["id"] for i,c in enumerate(dev) if args.scale == 48 or i % 4 < 2]
        lock = {"version": 1, "source_hashes": candidate_snapshot(), "manifest_sha256": digest(DATA / "manifest.json"),
            "scope": {"dev": ids, "holdout": [x.replace("-dev-", "-holdout-") for x in ids]},
            "layers": ["fixed", "mcp"], "variants": ["baseline", "contract", "guarded"], "repeat": 1,
            "config": {"model": "deepseek-flash", "temperature": 0, "max_output_tokens": 2048, "thinking": "disabled", "retries": 0}}
        write_json(args.output, lock, exclusive=True)
        print(json.dumps({"candidate_frozen": True, "scale": args.scale, "lock_sha256": digest(args.output)}))
    elif args.command == "estimate":
        result = read_json(args.calibration)
        rows = result["rows"]
        calls = [c for r in rows for c in r.get("calls", [])]
        if not calls or any(c["status"] != "settled" for c in calls):
            raise ValueError("Calibration contains missing usage; no complete-cost estimate")
        scenarios = len({r["case_id"] for r in rows})
        per_scenario = sum(c["charged_cny"] for c in calls) / scenarios
        layers_observed = sorted({r["layer"] for r in rows})
        projection = per_scenario * args.scale
        print(json.dumps({"observed_layers": layers_observed, "projected_same_layers_cny": projection,
            "with_50_percent_failure_margin_cny": projection*1.5,
            "limitation": "Do not extrapolate fixed-only calibration as full MCP cost; measure both layers.",
            "remaining_cny": result["budget"]["remaining_cny"]}))
    elif args.command == "export-review":
        from evaluation.answer_trust_review import export_review
        print(json.dumps(export_review(args.run, args.output_dir), ensure_ascii=False))
    elif args.command == "promotion":
        from evaluation.answer_trust_review import promotion_gate
        result = promotion_gate(args.run, args.reviews, args.key, args.checks)
        write_json(args.output, result, exclusive=True)
        print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

