"""Create a new authored development fixture once; refuse overwriting."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "data/evaluation/quality_return_v1"
    if target.exists():
        raise ValueError("Use a new dataset version")
    clauses = {
        "scope": "本演示仅适用于商品 DEMO-QC，不适用于其他商品或真实平台。",
        "no_reason_window": "无理由分支：实际签收7天内申请；预计到达不能代替实际签收。",
        "no_reason_condition": "无理由分支：商品必须未使用；已使用不能按本分支申请。",
        "quality_window": "质量问题分支：实际签收30天内申请；未提供实际签收时需核实。",
        "quality_condition": "质量问题分支：已使用不自动排除，但必须有同商品行同数量的明确缺陷确认；仅质检未通过、用户自述或照片不能代替。",
        "quality_exception": "上述例外仅适用于质量问题分支，不延长无理由7天窗口，不代表退款已执行。",
    }
    corpus = [
        {
            "id": "merchant-return",
            "text": "# 演示分支规则\n\n"
            + "\n\n".join("## " + name + "\n" + text for name, text in clauses.items()),
        },
        {
            "id": "shipping",
            "text": "# 发货规则\n\n商品预计三天送达；预计送达不是签收，也没有退款金额。",
        },
        {
            "id": "coupon",
            "text": "# 运费券规则\n\n运费券用于配送费用，不自动返还；本券不决定商品退货资格。",
        },
    ]
    bindings = {
        "no_reason": {
            "version_id": "merchant-return",
            "clauses": [clauses[k] for k in ("scope", "no_reason_window", "no_reason_condition")],
        },
        "quality_issue": {
            "version_id": "merchant-return",
            "clauses": [
                clauses[k]
                for k in ("scope", "quality_window", "quality_condition", "quality_exception")
            ],
        },
    }
    groups = [
        (
            "used",
            "quality_issue",
            [
                "用过两次，发现问题，质量问题如何申请？",
                "商品已经使用，能否按质量问题分支处理？",
                "质量问题已确认缺陷，用过影响申请吗？",
                "不是无理由退货，是用过后发现质量问题。",
            ],
        ),
        (
            "window",
            "no_reason",
            [
                "签收8天，未使用，按无理由规则能退吗？",
                "超过7天，能否套用质量问题30天作为无理由期限？",
                "未使用，实际签收第10天，想无理由退货。",
                "无理由分支期限是7天还是30天？",
            ],
        ),
        (
            "finding",
            "quality_issue",
            [
                "质检没通过就一定有缺陷吗？",
                "用户说坏了，可以跳过明确缺陷记录吗？",
                "只有照片，没有质检缺陷结论，可以申请吗？",
                "确认没有缺陷后还满足质量问题分支吗？",
            ],
        ),
        (
            "normal",
            "no_reason",
            [
                "签收两天，未使用，申请无理由退货。",
                "无理由退货需要商品未使用吗？",
                "实际签收不到7天，商品没用过，按哪条规则？",
                "普通无理由申请需要满足哪些条件？",
            ],
        ),
    ]
    cases = []
    for family, basis, questions in groups:
        for question in questions:
            cases.append(
                {
                    "id": f"qr-dev-{len(cases) + 1:03}",
                    "family": family,
                    "question": question,
                    "latest_binding": basis,
                    "answerable": True,
                    "required_quotes": bindings[basis]["clauses"],
                    "wrong_branch_quotes": bindings[
                        "quality_issue" if basis == "no_reason" else "no_reason"
                    ]["clauses"][1:],
                }
            )
    for basis, question in [
        ("quality_issue", "更正，不是无理由，是质量问题，已使用且签收10天。"),
        ("no_reason", "更正为无理由退货，不再按质量问题分支。"),
        ("quality_issue", "刚才说没用过不准确，用过并有缺陷，改为质量问题。"),
        ("no_reason", "收回质量问题表述，按未使用无理由分支核对。"),
    ]:
        cases.append(
            {
                "id": f"qr-dev-{len(cases) + 1:03}",
                "family": "correction",
                "question": question,
                "latest_binding": basis,
                "previous_binding": "quality_issue" if basis == "no_reason" else "no_reason",
                "answerable": True,
                "required_quotes": bindings[basis]["clauses"],
                "wrong_branch_quotes": bindings[
                    "quality_issue" if basis == "no_reason" else "no_reason"
                ]["clauses"][1:],
            }
        )
    for question in [
        "实际退款金额是多少？",
        "银行退款到账了吗？",
        "只查设备激活时间。",
        "能查询这次退款优惠分摊到分吗？",
    ]:
        cases.append(
            {
                "id": f"qr-dev-{len(cases) + 1:03}",
                "family": "unknown_fact",
                "question": question,
                "latest_binding": "quality_issue",
                "answerable": False,
                "required_quotes": [],
                "wrong_branch_quotes": [],
            }
        )
    target.mkdir(parents=True)
    hashes = {}
    for name, value in {
        "corpus.json": corpus,
        "bindings.json": bindings,
        "cases.json": cases,
    }.items():
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        (target / name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    manifest = {
        "cases": 24,
        "split": "authored_development",
        "hashes": hashes,
        "protocol_sha256": hashlib.sha256(
            (ROOT / "docs/fullstack/quality-return-protocol.md").read_bytes()
        ).hexdigest(),
        "paid_calls": 0,
        "holdout_read": False,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Frozen 24 authored development questions, 3 synthetic documents and operator bindings")


if __name__ == "__main__":
    main()
