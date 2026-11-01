"""Authorized <=32-call, <=1 CNY development comparison, no automatic retries."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PHASE = "workbench-answer-gate-v1-20261101"


class BatchStop(RuntimeError):
    pass


def load(dataset, identifiers=None):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    if (
        hashlib.sha256((dataset / "cases.json").read_bytes()).hexdigest()
        != manifest["cases_sha256"]
    ):
        raise ValueError("Frozen cases changed")
    if (
        hashlib.sha256(
            (ROOT / "docs/fullstack/answer-gate-v1/protocol.md").read_bytes()
        ).hexdigest()
        != manifest["protocol_sha256"]
    ):
        raise ValueError("Frozen protocol changed")
    rows = json.loads((dataset / "cases.json").read_text(encoding="utf-8"))
    allowed = {r["id"] for r in rows}
    if identifiers and (len(identifiers) != len(set(identifiers)) or set(identifiers) - allowed):
        raise ValueError("Unknown or duplicate case ID")
    return [r for r in rows if not identifiers or r["id"] in identifiers]


def main():
    from dotenv import dotenv_values
    from pydantic import SecretStr

    from evaluation.answer_trust_budget import BudgetLedger, cost
    from evaluation.answer_trust_data import write_json
    from workbench.answer_contract import (
        messages,
        prompt_hash,
        question_context,
        render_draft,
        validate_draft,
        validate_review,
    )
    from workbench.answer_gate import model_call
    from workbench.config import WorkbenchSettings
    from workbench.context_management import pack_messages

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "data/evaluation/workbench_answer_v1"
    )
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--version", choices=["contract-vs-reviewed"], default="contract-vs-reviewed"
    )
    parser.add_argument(
        "--baseline", choices=["same-draft-contract"], default="same-draft-contract"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        cases = load(args.dataset, args.case_id)
        if args.output.exists():
            raise ValueError("Output exists; preserve results")
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "validate":
        print(json.dumps({"cases": len(cases), "max_calls": 2 * len(cases), "paid_calls": 0}))
        return
    ledger_path = ROOT / "work/answer-trust/budget.json"
    if not ledger_path.exists():
        parser.error("Existing cumulative ledger required; do not reset")
    key = dotenv_values(ROOT / ".env").get("COMPATIBLE_API_KEY")
    if not key:
        parser.error("Existing official API key unavailable")
    settings = WorkbenchSettings(
        _env_file=None,
        policy_answer_mode="reviewed",
        agent_router_url="https://api.deepseek.com/chat/completions",
        agent_router_model="deepseek-flash",
        agent_router_key=SecretStr(key),
        policy_answer_timeout_seconds=30,
    )
    ledger = BudgetLedger(ledger_path)
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    attempts = []
    reserve = cost(settings.context_input_tokens, settings.policy_answer_max_tokens)

    def dispatch(phase, ctx, draft, case_id):
        # Check complete input before reserving and calling the production adapter.
        pack_messages(
            settings, messages(phase, ctx, draft), output_tokens=settings.policy_answer_max_tokens
        )
        token = uuid4().hex
        with ledger.transaction() as state:
            prior = [e for e in state["entries"] if e.get("metadata", {}).get("phase") == PHASE]
            if (
                len(prior) >= 32
                or sum(e["charged_cny"] for e in prior) + reserve > 1
                or sum(e["charged_cny"] for e in state["entries"]) + reserve > state["limit_cny"]
            ):
                raise BatchStop("Authorization or cumulative budget exhausted")
            if any(
                e.get("metadata", {}).get("case_id") == case_id
                and e.get("metadata", {}).get("stage") == phase
                for e in prior
            ):
                raise BatchStop("Request already attempted; no replay")
            state["entries"].append(
                {
                    "id": token,
                    "status": "reserved",
                    "reserved_cny": reserve,
                    "charged_cny": reserve,
                    "started_at": datetime.now(UTC).isoformat(),
                    "metadata": {
                        "phase": PHASE,
                        "stage": phase,
                        "case_id": case_id,
                        "authorization_max_calls": 32,
                        "authorization_max_cny": 1,
                    },
                    "usage": None,
                }
            )
            write_json(ledger.path, state)
        start = perf_counter()
        result = None
        error = None
        try:
            result = model_call(settings, phase, ctx, draft, timeout=30)
        except Exception as exc:
            error = type(exc).__name__
        usage = result.get("usage") if result else None
        known = isinstance(usage, dict) and all(
            type(usage.get(k)) is int and usage[k] >= 0
            for k in ("prompt_tokens", "completion_tokens")
        )
        exceeded = known and (
            usage["prompt_tokens"] > settings.context_input_tokens
            or usage["completion_tokens"] > settings.policy_answer_max_tokens
        )
        with ledger.transaction() as state:
            entry = next(e for e in state["entries"] if e["id"] == token)
            entry.update(
                status="provider_limit_violation"
                if exceeded
                else "settled"
                if known
                else "unknown_usage",
                usage=usage,
                charged_cny=cost(usage["prompt_tokens"], usage["completion_tokens"])
                if known
                else reserve,
                elapsed_ms=(perf_counter() - start) * 1000,
                response_model=result.get("response_model") if result else None,
                error=error,
            )
            write_json(ledger.path, state)
            attempts.append({k: v for k, v in entry.items() if k != "id"})
        (args.output / "calls.json").write_text(
            json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if exceeded:
            raise BatchStop("Provider exceeded reservation; stop batch")
        if not result:
            raise RuntimeError("Provider call failed: " + str(error))
        return result

    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "date": "2026-11-01",
                "authorization": PHASE,
                "max_calls": 32,
                "max_cny": 1,
                "dataset_sha256": hashlib.sha256(
                    (args.dataset / "cases.json").read_bytes()
                ).hexdigest(),
                "prompt_sha256": prompt_hash(),
                "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "endpoint": "https://api.deepseek.com/chat/completions",
                "model": "deepseek-flash",
                "thinking": "disabled",
                "temperature": 0,
                "max_output_tokens": settings.policy_answer_max_tokens,
                "pricing": "peak uncached2 input/8 output CNY per million, conservative not invoice",
                "pricing_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                "comparison": "same draft; natural and injected stress reported separately",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for case in cases:
        ctx = question_context(
            case["question"], case["evidence"], order=case.get("order"), latest=case.get("latest")
        )
        row = {
            "case_id": case["id"],
            "arm": case["arm"],
            "context": ctx,
            "expected": case["expected"],
            "stress_target": case["stress_target"],
        }
        try:
            generated = dispatch("generate", ctx, None, case["id"])
            row["generation"] = generated
            raw = case["injected_draft"] if case["arm"] == "injected_stress" else generated["raw"]
            draft = validate_draft(raw, ctx)
            row["compared_draft"] = draft.model_dump()
            row["contract_answer"] = render_draft(draft, ctx)
            reviewed = dispatch("review", ctx, draft.model_dump(), case["id"])
            row["review_response"] = reviewed
            decision = validate_review(reviewed["raw"])
            row["review"] = decision.model_dump()
            row["reviewed_answer"] = (
                row["contract_answer"]
                if decision.approved
                else "当前回答未通过核验，暂不能确认相关结论。"
            )
        except BatchStop as exc:
            row["error"] = type(exc).__name__
            records.append(row)
            with (args.output / "answers.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            break
        except Exception as exc:
            row["error"] = type(exc).__name__
            # Never refill a failed sample or silently retry.
        records.append(row)
        with (args.output / "answers.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "cases": len(records),
        "requests": len(attempts),
        "charged_cny": sum(a["charged_cny"] for a in attempts),
        "cumulative_cny": ledger.snapshot()["committed_cny"],
        "errors": sum("error" in r for r in records),
        "arms": {
            arm: {
                "cases": sum(r["arm"] == arm for r in records),
                "approved": sum(
                    r["arm"] == arm and r.get("review", {}).get("approved", False) for r in records
                ),
                "blocked": sum(
                    r["arm"] == arm and r.get("review", {}).get("approved") is False
                    for r in records
                ),
            }
            for arm in ("natural", "injected_stress")
        },
        "independent_final_test": False,
        "human_review_confirmed": 0,
        "limits": [
            "Injected errors are not natural model error rate",
            "Approval is not proof of semantic correctness",
            "Development only; no promotion",
        ],
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
