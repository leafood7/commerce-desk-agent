"""Freeze bounded synthetic long-document development data once."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "data/evaluation/rag_long_scale_v2"
    if target.exists():
        raise ValueError("Frozen fixture exists; create a new version")
    clauses = {
        "scope": "本政策仅适用于本商家的商品 DEMO-LONG-A，不适用于相似型号 DEMO-LONG-B。",
        "ordinary": "无理由退货限实际签收7天内且商品未使用；不能套用质量问题的30天窗口。",
        "entry": "质量问题处理允许已使用商品；受理前须逐项核对，参见第12条、 第16条、 第20条。",
        "window": "质量问题申请限实际签收30天内；预计送达不作为实际签收，缺少签收字段需核实。",
        "finding": "须有同一订单商品行及数量的明确缺陷确认；质检未通过、用户自述或照片本身不等于确认缺陷。",
        "exclusion": "排除人为损坏及不适用商品；质量问题例外不延长无理由7天窗口，不代表退款或运费已结算。",
        "receipt": "没有实际签收记录时暂缓资格判断，不用预计送达、发货时间或客服历史猜测补齐。",
    }
    subjects = [
        "工单登记",
        "商品核对",
        "包装记录",
        "客服协作",
        "状态说明",
        "证据留存",
        "人工复核",
        "渠道沟通",
        "数量管理",
        "操作撤销",
        "资料归档",
        "查询授权",
        "账单说明",
        "送检安排",
        "版本发布",
        "处理回访",
    ]
    corpus = []
    for index in range(6):
        sections = []
        for article in range(1, 25):
            title = f"第{article:02}条 {subjects[(article + index) % len(subjects)]}"
            special = {
                2: clauses["scope"],
                3: clauses["ordinary"],
                4: clauses["entry"],
                12: clauses["window"],
                16: clauses["finding"],
                20: clauses["exclusion"],
                22: clauses["receipt"],
            }.get(article)
            paragraphs = []
            if index == 0 and special:
                paragraphs.append(special)
            elif index == 1 and special:
                paragraphs.append(
                    special.replace("DEMO-LONG-A", "DEMO-LONG-B")
                    .replace("30天", "15天")
                    .replace("7天", "3天")
                )
            else:
                paragraphs.append(
                    f"{subjects[(article + index) % len(subjects)]}记录服务过程；文档{index}的这一章不授予其他商品退货资格，具体须核对原文范围。"
                )
            for part in range(3):
                paragraphs.append(
                    f"{subjects[(article + part) % len(subjects)]}的第{part + 1}项记录要求：登记订单商品行、数量、来源和最新处理状态。仅保留业务系统已确认字段，未知项标注待核实。处理人员须记录修改理由，保留先前状态供追溯，不从预计到达、用户猜测、历史回复或照片推断结论。批次{index}条款{article}的这一操作说明不改变申请期限，不代替实际质检，不承诺退款、补券或银行到账。实际办理仍须当前版本政策、资格预览和独立审批；发现资料不一致时先核实来源。"
                )
            sections.append("## " + title + "\n\n" + "\n\n".join(paragraphs))
        corpus.append(
            {
                "id": f"long-{index}",
                "organization_id": "merchant-a" if index < 4 else "merchant-b",
                "published": index != 3,
                "effective": index != 2,
                "title": f"隔离演示长文档{index}",
                "text": "# 演示政策及操作手册\n\n" + "\n\n".join(sections),
            }
        )
    for index in range(174):
        family = subjects[index % len(subjects)]
        paragraphs = [
            f"本演示资料{index}适用商品 DEMO-DIST-{index % 31}，不适用于 DEMO-LONG-A。有关质量问题、无理由退货和运费券须核对独立政策。",
            f"{family}处理窗口为{[3, 7, 15, 30][index % 4]}天，不得自动套用于其他商品。已使用、预售、送检和部分退货的条件依适用文件而定。用户自述不是质检结论。",
            f"查询应保留订单行和数量；{family}记录需要实际签收和付款状态，不从预计送达推断签收，不从商品参考金额推断退款。",
            f"{family}仅记录当前步骤。未知的激活、银行到账或优惠分摊维持未知，实际写入须明确确认和授权。",
            f"本资料不承诺自动返券、退款或运费承担；关于其他型号和过期活动的结论不能复用。索引编号{index}仅供隔离实验复查。",
        ]
        corpus.append(
            {
                "id": f"dist-{index:03}",
                "organization_id": "merchant-b" if index % 7 == 0 else "merchant-a",
                "published": index % 17 != 0,
                "effective": index % 19 != 0,
                "title": f"{family}演示手册{index}",
                "text": "# " + family + "\n\n" + "\n\n".join(paragraphs),
            }
        )
    bindings = {"no_reason": {"version_id": "long-0"}, "quality_issue": {"version_id": "long-0"}}
    families = [
        (
            "scope",
            "quality_issue",
            [
                "DEMO-LONG-A质量问题需要核对哪些适用范围？",
                "相似型号B能套用A的质量问题规则吗？",
                "本商家质量问题分支适用于什么商品？",
                "退货前如何核对商品是否在政策范围内？",
            ],
            ["scope", "entry", "window", "finding", "exclusion"],
        ),
        (
            "window",
            "quality_issue",
            [
                "签收第10天，质量问题明确缺陷，核对期限和例外。",
                "质量问题30天窗口是否需要实际签收？",
                "预计到达能代替实际签收计算质量问题期限吗？",
                "签收31天还能直接套质量问题30天吗？",
            ],
            ["entry", "window", "finding", "exclusion"],
        ),
        (
            "usage",
            "quality_issue",
            [
                "用过的商品发现问题，质量分支有哪些条件和排除？",
                "已使用但确认缺陷，能按质量问题规则处理吗？",
                "质量问题允许使用过，是否就不用查其他条件？",
                "使用例外是否也延长无理由窗口？",
            ],
            ["entry", "window", "finding", "exclusion"],
        ),
        (
            "finding",
            "quality_issue",
            [
                "质检未通过就等于确认缺陷吗？",
                "只有照片没有独立缺陷结论，质量问题如何核对？",
                "用户自述坏了，是否满足全部质量问题条件？",
                "同商品行不同数量的缺陷记录可以直接复用吗？",
            ],
            ["entry", "window", "finding", "exclusion"],
        ),
        (
            "exclusion",
            "quality_issue",
            [
                "人为损坏是否满足质量问题分支？",
                "质量问题例外有哪些排除条件？",
                "确认缺陷后是否不需要再核对排除条件？",
                "质量问题规则是否已代表退款和运费结算？",
            ],
            ["entry", "window", "finding", "exclusion"],
        ),
        (
            "correction",
            "no_reason",
            [
                "更正为无理由退货，原先说质量问题不准确。",
                "收回质量问题描述，按未使用无理由分支核对。",
                "更正商品未使用，签收8天，要无理由退货。",
                "只按普通无理由分支，不沿用质量问题30天。",
            ],
            ["scope", "ordinary", "receipt"],
        ),
        (
            "unknown_fact",
            "quality_issue",
            [
                "实际退款精确金额是多少？",
                "这笔钱银行到账了吗？",
                "具体设备何时激活？",
                "当前订单实际签收时间字段是什么？",
            ],
            [],
        ),
        (
            "offtopic",
            "no_reason",
            [
                "帮我预订明天的火车票。",
                "今天月球表面天气如何？",
                "能否替我发送外部邮件？",
                "上传照片就能自动证明缺陷吗？",
            ],
            [],
        ),
    ]
    cases = []
    for family, basis, questions, required in families:
        for question in questions:
            cases.append(
                {
                    "id": f"long-dev-{len(cases) + 1:03}",
                    "family": family,
                    "question": question,
                    "organization_id": "merchant-a",
                    "latest_binding": basis,
                    "answerable": bool(required),
                    "required_quotes": [clauses[k] for k in required],
                    "wrong_branch_quotes": [clauses["ordinary"]]
                    if basis == "quality_issue"
                    else [clauses["window"], clauses["finding"]],
                    "previous_binding": "quality_issue" if family == "correction" else None,
                }
            )
    target.mkdir(parents=True)
    values = {
        "corpus.json": corpus,
        "cases.json": cases,
        "bindings.json": bindings,
        "config.json": {
            "tiers": [12, 60, 180],
            "seed": 20261029,
            "budget": 2400,
            "distance": 0.65,
            "rerank_threshold": -2.0,
            "arms": {
                "global_struct600": {
                    "chunk": "structural",
                    "size": 600,
                    "pool": 12,
                    "top_k": 3,
                    "rerank": True,
                    "scoped": False,
                    "context": "neighbors",
                },
                "global_fixed600": {
                    "chunk": "fixed",
                    "size": 600,
                    "pool": 12,
                    "top_k": 3,
                    "rerank": True,
                    "scoped": False,
                    "context": "neighbors",
                },
                "global_struct300": {
                    "chunk": "structural",
                    "size": 300,
                    "pool": 12,
                    "top_k": 3,
                    "rerank": True,
                    "scoped": False,
                    "context": "neighbors",
                },
                "global_pool24": {
                    "chunk": "structural",
                    "size": 600,
                    "pool": 24,
                    "top_k": 3,
                    "rerank": True,
                    "scoped": False,
                    "context": "neighbors",
                },
                "global_top5": {
                    "chunk": "structural",
                    "size": 600,
                    "pool": 12,
                    "top_k": 5,
                    "rerank": True,
                    "scoped": False,
                    "context": "neighbors",
                },
                "global_no_rerank": {
                    "chunk": "structural",
                    "size": 600,
                    "pool": 12,
                    "top_k": 3,
                    "rerank": False,
                    "scoped": False,
                    "context": "neighbors",
                },
                "scoped_struct600": {
                    "chunk": "structural",
                    "size": 600,
                    "pool": 12,
                    "top_k": 3,
                    "rerank": True,
                    "scoped": True,
                    "context": "neighbors",
                },
                "scoped_references": {
                    "chunk": "structural",
                    "size": 600,
                    "pool": 12,
                    "top_k": 3,
                    "rerank": True,
                    "scoped": True,
                    "context": "references",
                },
            },
        },
    }
    hashes = {}
    for name, data in values.items():
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        (target / name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    manifest = {
        "split": "authored_development_only",
        "cases": 32,
        "documents": 180,
        "hashes": hashes,
        "protocol_sha256": hashlib.sha256(
            (ROOT / "docs/fullstack/rag-long-scale-protocol.md").read_bytes()
        ).hexdigest(),
        "long_document_chars": [len(d["text"]) for d in corpus[:6]],
        "paid_calls": 0,
        "holdout_read": False,
        "template_corpus": True,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "frozen": True,
                "cases": 32,
                "docs": 180,
                "long_chars": manifest["long_document_chars"],
            }
        )
    )


if __name__ == "__main__":
    main()
