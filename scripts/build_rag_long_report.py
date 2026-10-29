"""Build a transparent report from completed traces, not a new retrieval experiment."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--review", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        p.error("Output exists; keep previous report")
    summary = json.loads((a.input / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((a.input / "manifest.json").read_text(encoding="utf-8"))
    reviewed = json.loads(a.review.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (a.input / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if (
        hashlib.sha256((a.input / "traces.jsonl").read_bytes()).hexdigest()
        != reviewed["input_sha256"]
    ):
        p.error("Review is for different evidence")
    a.output.mkdir(parents=True)
    lines = [
        "# 长文档规模实验：原始评分与作者复核分开报告",
        "",
        f"完成记录：{summary['records']}/{summary['expected_records']}；完整矩阵完成：{summary['completed']}；生成模型调用：0。",
        "",
        "主评分是业务流程绑定的整套条款覆盖，不是每题答案正确率。复核仅核对5道问题的最小证据；照片题从无证据组更正为规则问题，原标签不覆盖。",
        "",
        "|原始文档档位|配置|主评分条款齐备|主评分均覆盖|其他文档片段比例|复核问题证据命中|p50毫秒|p95毫秒|引用触限题数|",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for result in summary["results"]:
        subset = [r for r in rows if r["tier"] == result["tier"] and r["arm"] == result["arm"]]
        total = sum(len(r["evidence"]) for r in subset)
        wrong = sum(r["metrics"]["wrong_document_chunks"] for r in subset)
        review = next(
            r
            for r in reviewed["results"]
            if r["tier"] == result["tier"] and r["arm"] == result["arm"]
        )
        lines.append(
            f"|{result['tier']}|{result['arm']}|{result['full_cases']}/{result['positive_cases']}|{result['mean_coverage']:.1%}|{wrong / total if total else 0:.1%}|{review['reviewed_question_evidence_hits']}/{review['reviewed_question_cases']}|{result['p50_ms']:.1f}|{result['p95_ms']:.1f}|{result['reference_limited_cases']}|"
        )
    lines += [
        "",
        "空检索会单独计数，不能把空结果的零错误片段解释为任务成功。p50/p95仅为本机该批查询样本，受缓存、索引顺序与并行工作影响。",
        "",
        "## 索引与实际参与检索的资料",
        "",
        "|原始文档数|切块|当前组织有效文档|全部片段|有效片段|本档新增片段|增量索引秒|累计源字符|",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for info in manifest["indexing"]:
        lines.append(
            f"|{info['tier_documents']}|{info['layout']}|{info['active_documents']}|{info['total_chunks']}|{info['active_chunks']}|{info['new_chunks']}|{info['incremental_index_ms'] / 1000:.2f}|{info['characters']}|"
        )
    lines += [
        "",
        f"模型初始化及预热：{manifest['model_startup_ms'] / 1000:.2f}秒；本地索引文件合计：{manifest['index_bytes']}字节。该磁盘数字不是内存占用，未测峰值内存。",
        "",
        "## 待作者进一步核对的实际证据对照",
        "",
        "以下只匿名化配置顺序，仍属于作者参与复核；没有生成回答，不能据此判断回答准确率。配置映射在review-key.json，不宣称独立盲审。",
    ]
    keys = []
    for identifier in [
        "long-dev-001",
        "long-dev-005",
        "long-dev-009",
        "long-dev-013",
        "long-dev-017",
        "long-dev-021",
        "long-dev-032",
    ]:
        pair = []
        for arm in ["scoped_struct600", "scoped_references"]:
            candidates = [r for r in rows if r["case_id"] == identifier and r["arm"] == arm]
            if candidates:
                pair.append(max(candidates, key=lambda row: row["tier"]))
        if len(pair) != 2:
            continue
        if int(hashlib.sha256(identifier.encode()).hexdigest(), 16) % 2:
            pair.reverse()
        lines += [
            "",
            f"### {identifier}：{pair[0]['question']}",
            "",
            "请分别核对：当前问题是否已有足够证据？整个流程缺什么？引用是否触及预算？尚未逐题人工评分。",
        ]
        for label, row in zip(["A", "B"], pair):
            keys.append(
                {"case_id": identifier, "label": label, "arm": row["arm"], "tier": row["tier"]}
            )
            lines += [
                "",
                f"**{label}** · {row['tier']}份资料档 · {row['context_chars']}字符",
                "",
                *[
                    f"> {source['content'].replace(chr(10), chr(10) + '> ')}\n> 来源：{source['id']}\n"
                    for source in row["evidence"]
                ],
            ]
    (a.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (a.output / "review-key.json").write_text(
        json.dumps(keys, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"report": str(a.output / "report.md"), "anonymous_pairs": len(keys) // 2}))


if __name__ == "__main__":
    main()
