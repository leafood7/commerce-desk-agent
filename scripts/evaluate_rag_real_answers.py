"""Explicit 48-call/1 CNY source-summary answer experiment, no runtime config writes."""

import argparse
import hashlib
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PHASE = "rag-real-v1-semantics-48calls-1cny"
PROMPT = """你是电商政策问答助手。仅依据提供的证据摘要回答本次问题；摘要不是完整政策。核对商家、地区、渠道、条件及例外，用户最新更正优先。缺少实际订单、会员、结算或缺陷字段时明确未知。你没有外部办理工具，不得声称已查询或办理。不要把参考、上限、一般条件写成确定结论。证据不足时说明需要补查哪项，同时回答已有依据的部分。返回JSON，包含reply字符串、citations引用的实际证据id列表、unknowns待核实事项列表。不要输出分析过程。"""


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(dataset, traces, ids):
    from evaluate_rag_real_scale import load

    manifest, corpus, cases = load(dataset, ids)
    rows = [json.loads(line) for line in traces.read_text(encoding="utf-8").splitlines()]
    selected = []
    for number, case in enumerate(cases):
        arms = ["global_hybrid_rerank", "bound_hybrid_rerank"]
        if number % 2:
            arms.reverse()
        for arm in arms:
            matches = [r for r in rows if r["case_id"] == case["id"] and r["arm"] == arm]
            if len(matches) != 1:
                raise ValueError("Incomplete or duplicate retrieval trace")
            evidence = [
                {k: r[k] for k in ["id", "title", "content"]}
                for r in matches[0]["result"]["matches"]
            ]
            content = {
                "question": case["question"],
                "previous_user_conditions": case["history"],
                "evidence": evidence,
            }
            body = {
                "model": "deepseek-flash",
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": 512,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
                ],
            }
            # UTF-8 byte bound plus ample message framing; no truncation of gold or evidence.
            if len(json.dumps(body, ensure_ascii=False).encode()) + 1024 > 16384:
                raise ValueError("Request exceeds bounded input reservation")
            selected.append(
                {
                    "case_id": case["id"],
                    "arm": arm,
                    "body": body,
                    "evidence_ids": [e["id"] for e in evidence],
                }
            )
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["validate", "run"])
    p.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/rag_real_v1")
    p.add_argument("--traces", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--case-id", action="append")
    p.add_argument("--execute-authorized", action="store_true")
    a = p.parse_args()
    requests = prepare(a.dataset, a.traces, a.case_id)
    if a.command == "validate":
        print(
            json.dumps(
                {
                    "requests": len(requests),
                    "input_ceiling": 16384,
                    "output_ceiling": 512,
                    "no_calls": True,
                }
            )
        )
        return
    if not a.execute_authorized:
        p.error("Explicit authorized execution flag required")
    if a.output.exists():
        p.error("Retain old results; choose new output")
    import httpx
    from dotenv import dotenv_values

    from evaluation.answer_trust_budget import BudgetLedger, cost
    from evaluation.answer_trust_data import write_json

    key = dotenv_values(ROOT / ".env").get("COMPATIBLE_API_KEY")
    if not key:
        p.error("Existing official API key unavailable")
    ledger = BudgetLedger(ROOT / "work/answer-trust/budget.json")
    a.output.mkdir(parents=True)
    reserve = cost(16384, 512)
    write(
        a.output / "manifest.json",
        {
            "authorization": "User explicitly approved 48 official DeepSeek requests and <=1 CNY on 2026-10-29; public source summaries and synthetic questions only",
            "phase": PHASE,
            "max_calls": 48,
            "max_cny": 1,
            "input_limit": 16384,
            "output_limit": 512,
            "reserve_cny": reserve,
            "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "trace_sha256": hashlib.sha256(a.traces.read_bytes()).hexdigest(),
            "pricing_url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
            "pricing_checked": "2026-10-29",
            "input_cny_per_million": 2,
            "output_cny_per_million": 8,
            "pricing_basis": "peak uncached estimate, not invoice",
            "temperature": 0,
            "thinking": "disabled",
            "retries": 0,
        },
    )
    results = []
    with httpx.Client(
        trust_env=False, timeout=httpx.Timeout(60, connect=10), follow_redirects=False
    ) as client:
        for request in requests:
            token = uuid4().hex
            with ledger.transaction() as state:
                entries = [
                    e for e in state["entries"] if e.get("metadata", {}).get("phase") == PHASE
                ]
                if (
                    len(entries) >= 48
                    or sum(e["charged_cny"] for e in entries) + reserve > 1
                    or sum(e["charged_cny"] for e in state["entries"]) + reserve
                    > state["limit_cny"]
                ):
                    raise RuntimeError("Authorized request/cost ceiling reached")
                state["entries"].append(
                    {
                        "id": token,
                        "status": "reserved",
                        "reserved_cny": reserve,
                        "charged_cny": reserve,
                        "started_at": datetime.now(UTC).isoformat(),
                        "metadata": {
                            "phase": PHASE,
                            "case_id": request["case_id"],
                            "arm": request["arm"],
                            "authorization_cny": 1,
                            "input_limit": 16384,
                            "output_limit": 512,
                        },
                        "usage": None,
                    }
                )
                write_json(ledger.path, state)
            started = time.perf_counter()
            response = None
            error = None
            parsed = None
            valid = False
            try:
                r = client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json=request["body"],
                )
                if r.status_code != 200:
                    raise RuntimeError("Provider status " + str(r.status_code))
                response = r.json()
                choice = response["choices"][0]
                parsed = json.loads(choice["message"]["content"])
                valid = (
                    isinstance(parsed, dict)
                    and isinstance(parsed.get("reply"), str)
                    and isinstance(parsed.get("citations"), list)
                    and all(
                        isinstance(k, str) and k in request["evidence_ids"]
                        for k in parsed["citations"]
                    )
                    and isinstance(parsed.get("unknowns"), list)
                    and choice.get("finish_reason") == "stop"
                )
            except Exception as exc:
                error = type(exc).__name__
            usage = response.get("usage") if isinstance(response, dict) else None
            known = isinstance(usage, dict) and all(
                type(usage.get(k)) is int and usage[k] >= 0
                for k in ["prompt_tokens", "completion_tokens"]
            )
            elapsed = (time.perf_counter() - started) * 1000
            with ledger.transaction() as state:
                entry = next(e for e in state["entries"] if e["id"] == token)
                entry.update(
                    status="settled" if known else "unknown_usage",
                    charged_cny=cost(usage["prompt_tokens"], usage["completion_tokens"])
                    if known
                    else reserve,
                    usage={
                        "input_tokens": usage["prompt_tokens"],
                        "output_tokens": usage["completion_tokens"],
                    }
                    if known
                    else None,
                    error_type=error,
                    elapsed_ms=elapsed,
                    response_model=response.get("model") if response else None,
                )
                write_json(ledger.path, state)
            safe_response = None
            if response:
                safe_response = {k: response.get(k) for k in ["id", "model", "usage", "created"]}
                safe_response["choices"] = [
                    {
                        "finish_reason": c.get("finish_reason"),
                        "message": {
                            "role": "assistant",
                            "content": c.get("message", {}).get("content"),
                        },
                    }
                    for c in response.get("choices", [])
                ]
            row = {
                **request,
                "response": safe_response,
                "parsed": parsed,
                "structural_valid": valid,
                "error": error,
                "elapsed_ms": elapsed,
                "accounting": entry,
            }
            results.append(row)
            with (a.output / "answers.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(request["case_id"], request["arm"], "valid=" + str(valid), flush=True)
            if known and (usage["prompt_tokens"] > 16384 or usage["completion_tokens"] > 512):
                raise RuntimeError("Provider exceeded reservation; stop and audit")
            if error or not known:
                break
    rng = random.Random(20261029)
    blind = []
    mapping = []
    for case_id in dict.fromkeys(r["case_id"] for r in results):
        pair = [r for r in results if r["case_id"] == case_id]
        rng.shuffle(pair)
        for n, r in enumerate(pair):
            label = case_id + "-" + chr(65 + n)
            blind.append(
                {
                    "label": label,
                    "case_id": case_id,
                    "answer": r["parsed"],
                    "evidence": json.loads(r["body"]["messages"][1]["content"])["evidence"],
                    "structural_valid": r["structural_valid"],
                }
            )
            mapping.append({"label": label, "arm": r["arm"]})
    write(a.output / "anonymous-review.json", blind)
    write(a.output / "review-key.json", mapping)
    write(
        a.output / "summary.json",
        {
            "requests": len(results),
            "valid": sum(r["structural_valid"] for r in results),
            "errors": sum(bool(r["error"]) for r in results),
            "charged_cny": sum(r["accounting"]["charged_cny"] for r in results),
            "models": sorted(set(r["accounting"]["response_model"] or "unknown" for r in results)),
            "review_status": "awaiting_author_semantic_review",
            "independent_blind_review": False,
        },
    )


if __name__ == "__main__":
    main()
