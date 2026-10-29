"""Author-created development fixtures. Never touches the sealed answer-trust set."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/evaluation/return_agent_v1"

POLICIES = [
    (
        "ordinary",
        "returns",
        ["STD-A"],
        [
            (
                "适用商品",
                "本演示普通实物规则仅适用于STD-A商品；定制商品、电子下载及会员专属商品须核对各自规则，不能混用。",
            ),
            (
                "申请期限",
                "STD-A无理由退货申请须在实际签收后7天内提出。预计送达、物流正在派送或用户猜测已到货，都不能替代订单实际签收记录。",
            ),
            (
                "商品状态",
                "STD-A申请需保持商品未使用、配件齐全。仅为合理查验而拆开外包装，不当然等于已经使用；应继续核实商品本体和配件状态。",
            ),
            (
                "其他原因",
                "超过本演示无理由窗口或存在使用痕迹时，不自动推断其他原因一概不能退货；质量问题需提供材料转人工审核。客服不能代替质检认定质量问题成立。",
            ),
            (
                "申请与退款",
                "提交申请只表示登记，审批通过只表示允许进入后续处理；本项目外部受理为模拟，不表示真实退货或退款已经完成。商品标价乘数量不能直接认定实际退款金额。",
            ),
        ],
    ),
    (
        "electronics",
        "returns",
        ["ELEC-A"],
        [
            (
                "适用商品",
                "本演示电子商品规则仅适用于ELEC-A，不能把电子下载交付规则用于这件实物商品。",
            ),
            (
                "合理查验",
                "ELEC-A仅拆封合理查验且未激活、没有明显使用痕迹时，不因拆封本身直接排除申请；仍需核对实际签收后7天窗口及配件。",
            ),
            (
                "激活例外",
                "ELEC-A若激活或试用后产生较大贬值，须核对购买时是否明确提示并由消费者确认相关条件；缺少提示与确认记录时转人工，不能一概承诺可退或不可退。",
            ),
            (
                "证据来源",
                "激活状态、购买时提示记录和消费者确认记录为不同字段。当前订单查询工具没有这些字段时，客服只能说明缺少证据，不能承诺通过订单号已经核实。",
            ),
            (
                "办理边界",
                "退款金额需要支付与优惠分摊事实，政策原文不替代订单字段；本地模拟申请不具有真实平台退款能力。",
            ),
        ],
    ),
    (
        "custom",
        "returns",
        ["CUSTOM-A"],
        [
            ("适用商品", "CUSTOM-A是按用户指定内容刻字的定制商品，本段只针对该商品的定制范围。"),
            (
                "无理由范围",
                "CUSTOM-A不适用本演示普通商品的无理由退货规则；不能仅凭普通商品7天条款承诺受理，也不能由此拒绝所有其他原因的售后。",
            ),
            (
                "质量问题例外",
                "CUSTOM-A若用户主张制作错误或质量问题，需要提交订单定制内容、实物材料并转人工核对；本条不保证审核通过，也不授权客服直接退款。",
            ),
            (
                "变更条件",
                "用户更正商品为普通STD-A后，应重新使用STD-A规则；历史中定制商品的结论不能继续作为新商品的资格依据。",
            ),
            (
                "材料与状态",
                "照片可以作为待审核材料，不等于质量事实已被确认；申请状态、审批状态和实际退款分别记录。",
            ),
        ],
    ),
    (
        "member",
        "returns",
        ["MEM-A"],
        [
            (
                "适用范围",
                "本演示MEM-A延长退货规则要求订单有15天权益标识，会员身份本身不使所有商品自动延长。",
            ),
            (
                "期限",
                "MEM-A订单具备15天权益标识时，可在实际签收后15天内提出本演示申请；没有标识时不能套用15天，应核对订单适用的普通规则。",
            ),
            (
                "条件保留",
                "延长到15天不免除商品状态、配件及适用范围条件；不能理解为用过也当然可以退。实际签收时间未知时需核实，预计到达日期不能顶替。",
            ),
            (
                "审批边界",
                "会员客服优先排队不等于退货免审批，模型不能使用会员身份代替审批员确认，也不能承诺立即到账。",
            ),
            (
                "未知字段",
                "查询工具没有15天权益标识时，不能承诺已经通过订单号查明该权益；应列为待核实条件或转人工。",
            ),
        ],
    ),
    (
        "freight",
        "freight_coupon",
        ["FREIGHT-TOPIC"],
        [
            (
                "运费券范围",
                "运费券仅抵扣符合活动条件的配送费用，不抵扣商品金额；商品满减券规则不能解释运费券的退回条件。",
            ),
            (
                "运费券退回",
                "运费券退回取决于配送费用是否实际撤销及该券活动规则；仅退部分商品不当然返还运费券，不能承诺立即补发。",
            ),
            (
                "相邻概念",
                "商品满减券在商品全部退货完成后按该券规则返还；只退部分商品不承诺立即返整券。本段不是运费券条款，不能作为运费券结论的依据。",
            ),
            (
                "用户更正",
                "用户明确更正为运费券后，应以当前券种及其条件核对，不沿用此前对商品满减券的回答。",
            ),
            (
                "未知活动",
                "本资料未提供任何具体运费券的补发金额、到账分钟数或领取额度。缺少活动规则时只能说明未知，不能编造数值。",
            ),
        ],
    ),
    (
        "refund",
        "general",
        ["REFUND-STATUS"],
        [
            (
                "状态区分",
                "退款申请已提交、审核通过、平台已发起退款与用户实际到账是不同状态；不能把申请已提交写成已经收到退款。",
            ),
            (
                "时效边界",
                "渠道退款时效取决于支付渠道和实际受理状态；本资料不保证今天一定到账，也不提供分钟级到账承诺。",
            ),
            (
                "金额边界",
                "实付现金、商品券、运费及优惠分摊必须按订单与对应规则核对，商品标价乘退货数量只是参考值，不等于实际现金退款金额。",
            ),
            (
                "工具能力",
                "本项目查单不提供银行实际到账查询，模拟退货受理也不会真实退款。不能承诺已经替用户查到银行入账。",
            ),
            (
                "更正优先",
                "用户更正说只是申请已提交时，应保留最新状态；不能引用历史助手说已到账作为业务事实。",
            ),
        ],
    ),
]

# family, question, necessary section indices, optional binding. None means no verified binding.
QUESTIONS = [
    ("ordinary", "STD-A实际签收第3天，未使用，普通申请期限是什么？", [0, 1], "STD-A"),
    ("ordinary", "STD-A只是拆开外包装检查，是否就算使用不能申请？", [2], "STD-A"),
    ("ordinary", "STD-A已超过7天但说有质量问题，是否所有退货都不能办？", [1, 3], "STD-A"),
    ("ordinary", "STD-A的实际退款金额精确到分是多少？", [], "STD-A"),
    ("electronics", "ELEC-A未激活只合理拆封，能否直接因拆封拒绝？", [1], "ELEC-A"),
    ("electronics", "ELEC-A已激活有贬值，购买提示和确认记录未知，能否一概拒退？", [2, 3], "ELEC-A"),
    ("electronics", "更正：是实物ELEC-A，不是下载内容，按什么范围核对？", [0, 1], "ELEC-A"),
    ("electronics", "请给出ELEC-A具体IMEI的真实激活时间。", [], "ELEC-A"),
    ("custom", "CUSTOM-A刻字商品能套普通商品7天无理由规则吗？", [0, 1], "CUSTOM-A"),
    ("custom", "CUSTOM-A定制内容做错，可以不看材料就直接认定不能退吗？", [1, 2], "CUSTOM-A"),
    ("custom", "用户更正不是CUSTOM-A，改为STD-A，之前定制结论还能沿用吗？", [0, 1], "STD-A"),
    ("custom", "请确认CUSTOM-A这张照片已由真实质检判为合格。", [], "CUSTOM-A"),
    ("member", "MEM-A有15天权益标识且签收第10天，延长规则是什么？", [0, 1, 2], "MEM-A"),
    ("member", "我是会员但订单没有标识，MEM-A一定有15天吗？", [0, 1], "MEM-A"),
    ("member", "MEM-A优先客服是不是可以跳过审批？", [3], "MEM-A"),
    ("member", "直接查明订单是否真的带15天权益，当前工具没有该字段。", [], "MEM-A"),
    ("freight", "我问运费券，只退部分商品能立即返券吗？", [0, 1], "FREIGHT-TOPIC"),
    ("freight", "更正为运费券，不是商品券，请按配送费用核对。", [0, 1, 3], "FREIGHT-TOPIC"),
    ("freight", "商品券全部退货后返还，能直接用于解释运费券吗？", [0, 1, 2], "FREIGHT-TOPIC"),
    ("freight", "告诉我这张未提供活动规则的运费券马上补发多少钱。", [], None),
    ("refund", "用户说只是提交退款申请，能写成钱已经到账吗？", [0, 4], "REFUND-STATUS"),
    ("refund", "退1件商品，标价123.45元，能认定实际退123.45现金吗？", [2], "REFUND-STATUS"),
    ("refund", "查不到银行入账，能承诺模拟受理就等于真实退款吗？", [0, 3], "REFUND-STATUS"),
    ("refund", "请查询这笔退款今天18点是否已经真实到账。", [], None),
]


def main():
    if DEST.exists():
        raise ValueError(
            "Frozen fixtures already exist; choose a new version instead of overwriting"
        )
    DEST.mkdir(parents=True)
    corpus, bindings, sections = [], {}, {}
    for family, scope, keys, paragraphs in POLICIES:
        text = (
            "# 演示政策 "
            + family
            + "\n\n"
            + "\n\n".join("## " + heading + "\n\n" + clause for heading, clause in paragraphs)
        )
        corpus.append(
            {
                "id": family + "-v1",
                "family": family,
                "policy_scope": scope,
                "text": text,
                "source": "author_demo_policy_not_law",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        sections[family] = [clause for _, clause in paragraphs]
        bindings.update({key: family + "-v1" for key in keys})
    cases = []
    for i, (family, query, indices, binding) in enumerate(QUESTIONS, 1):
        # The corrected STD-A case uses the latest known product's actual source.
        expected_family = "ordinary" if family == "custom" and binding == "STD-A" else family
        cases.append(
            {
                "id": f"return-dev-{i:03}",
                "family": family,
                "question": query,
                "latest_binding": binding,
                "answerable": bool(indices),
                "required_text_groups": [[sections[expected_family][j]] for j in indices],
                "allowed_document_ids": [expected_family + "-v1"],
                "review": "author_created_development_only",
            }
        )
    decision_cases = [
        {
            "id": "decision-dev-01",
            "stage": "initial",
            "allowed": ["read_order", "search_policy"],
            "latest_condition": "unknown",
        },
        {
            "id": "decision-dev-02",
            "stage": "order_read",
            "allowed": ["search_policy", "check_return"],
            "latest_condition": "unused",
        },
        {
            "id": "decision-dev-03",
            "stage": "need_condition",
            "allowed": ["ask_condition"],
            "latest_condition": "unknown",
        },
        {
            "id": "decision-dev-04",
            "stage": "corrected",
            "allowed": ["read_order", "search_policy"],
            "latest_condition": "used",
            "prior_condition": "unused",
        },
        {
            "id": "decision-dev-05",
            "stage": "missing_evidence",
            "allowed": ["supplement"],
            "latest_condition": "unused",
        },
        {
            "id": "decision-dev-06",
            "stage": "empty_after_supplement",
            "allowed": ["stop"],
            "latest_condition": "unused",
        },
        {
            "id": "decision-dev-07",
            "stage": "all_checked",
            "allowed": ["review"],
            "latest_condition": "unused",
        },
        {
            "id": "decision-dev-08",
            "stage": "missing_receipt",
            "allowed": ["stop"],
            "latest_condition": "unused",
        },
    ]
    config = {
        "chunk": "structural",
        "size": 300,
        "overlap": 40,
        "top_k": 3,
        "pool": 12,
        "context_chars": 1200,
        "distance": 0.65,
        "rerank_threshold": -2.0,
        "arms": ["baseline", "policy_scoped"],
        "note": "Both use latest query; only candidate narrows a verified business binding.",
    }
    values = {
        "corpus.json": corpus,
        "cases.json": cases,
        "bindings.json": bindings,
        "decision-cases.json": decision_cases,
        "config.json": config,
    }
    hashes = {}
    for name, value in values.items():
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        (DEST / name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    protocol = ROOT / "docs/fullstack/return-agent-stage-protocol.md"
    manifest = {
        "baseline_commit": "1e8f369",
        "split": "development_only",
        "cases": len(cases),
        "families": 6,
        "decision_cases": len(decision_cases),
        "hashes": hashes,
        "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "changes_after_results": "use_new_version_not_overwrite",
        "holdout_read": False,
    }
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
