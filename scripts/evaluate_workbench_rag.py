"""Frozen development-only retrieval comparison; no LLM calls or holdout access."""

import argparse
import hashlib
import json
import os
import statistics
from pathlib import Path
from time import perf_counter

from pydantic import SecretStr
from sqlalchemy import select

from business.knowledge import build_chunks
from business.rag_retrieval import RERANKER_MODEL, RERANKER_REVISION
from workbench.cli import migration_config
from workbench.config import ROOT, WorkbenchSettings
from workbench.database import Database
from workbench.ingestion import work_once
from workbench.knowledge_index import EMBEDDING_MODEL, EMBEDDING_REVISION, ChromaIndex
from workbench.knowledge_schemas import SearchInput, VersionOptions
from workbench.knowledge_service import create_version, publish
from workbench.models import User
from workbench.retrieval import KnowledgeRetriever
from workbench.seed import seed_demo

DIRECTORY = ROOT / "data/evaluation/workbench_rag"
VARIANTS = [
    "预售定金已付但尾款未付，能按已全款催发货吗？",
    "尾款截止日已经过去，是否就说明商家超过发货承诺了？",
    "页面写了单独的预售发货日期，还能要求按现货48小时发货吗？",
    "不买商品了，能把账户里的商品满减券提现吗？",
    "账户已经领券，但订单未满足门槛，领取成功就能用吗？",
    "活动未写可叠加，同一商品能同时用两张满减券吗？",
    "手机只是合理拆封检查，没激活也没明显使用，拆封本身会取消退货资格吗？",
    "我买的是实物耳机，不是下载内容，能直接按软件交付规则拒退吗？",
    "手机激活后贬值就一概拒退，是否还要查购买时是否明确提示并确认？",
    "我有黑金会员，但订单没有15天标识，这件商品自动延长到15天吗？",
    "客服排队给金卡会员优先，是否连售后退款审核也能跳过？",
    "我不使用黑金权益，能授权另一个账号代用吗？",
    "签收第31天，自助开票入口关闭，下一步只能认定不能开票吗？",
    "企业抬头正确，但缺少纳税人识别信息，这样的开票资料完整吗？",
    "开票资料还缺信息，能否仍承诺从首次申请起两工作日完成？",
    "页面显示退款申请已提交，能据此告诉客户已经收到钱吗？",
    "银行卡退款已发起三天，政策是否保证今天一定到账？",
    "订单用现金和商品券混合支付，取消后券部分也能按现金退吗？",
]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def freeze():
    if (DIRECTORY / "manifest.json").exists():
        raise ValueError(
            "Frozen dataset already exists; use a new version directory to change standards"
        )
    original = [
        json.loads(line)
        for line in (ROOT / "data/evaluation/rag_cases.jsonl")
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line.strip()
    ]
    cases = [row for row in original if row["split"] == "dev"]
    gold = {row["id"]: row["content"].split("\n\n", 1)[1] for row in build_chunks()}
    answerable = [row for row in cases if row["answerable"]]
    assert len(answerable) == len(VARIANTS) == 18
    for base, question in zip(answerable, VARIANTS):
        cases.append(
            {
                **base,
                "id": base["id"] + "-condition",
                "question": question,
                "parent_id": base["id"],
                "author_variant": True,
            }
        )
    for case in cases:
        case["required_text_groups"] = [
            [gold[key] for key in group] for group in case["evidence_groups"]
        ]
    corpus = []
    for path in sorted((ROOT / "data/knowledge").glob("*.txt")):
        paragraphs = [
            line.strip()
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        adapted = "# " + paragraphs[0] + "\n\n" + "\n\n".join(paragraphs[1:]) + "\n"
        corpus.append(
            {
                "filename": path.stem + ".md",
                "original": path.name,
                "original_sha256": digest(path.read_bytes()),
                "text": adapted,
                "adapted_sha256": digest(adapted.encode()),
            }
        )
    write(DIRECTORY / "cases.json", cases)
    write(DIRECTORY / "corpus.json", corpus)
    write(
        DIRECTORY / "manifest.json",
        {
            "version": "workbench-rag-dev-v1",
            "cases": 40,
            "families": "22 original dev + 18 paired condition variants; not independent tests",
            "corpus_files": len(corpus),
            "transform": "nonempty stripped lines; first line Markdown H1; remaining lines blank-separated",
            "cases_sha256": digest((DIRECTORY / "cases.json").read_bytes()),
            "corpus_sha256": digest((DIRECTORY / "corpus.json").read_bytes()),
            "chunk_chars": 600,
            "chunk_overlap": 80,
            "context_chars": 4000,
            "top_k": 3,
            "vector_max_distance": 0.65,
            "rerank_threshold": -2.0,
            "arms": [
                f"{chunk}/{retrieval}"
                for chunk in ("fixed", "structural")
                for retrieval in ("vector", "hybrid", "hybrid_rerank")
            ],
            "metrics": {
                "source_hit": "any returned source filename in a necessary-evidence group",
                "necessary_clause_coverage": "fraction of evidence groups with at least one complete normalized original clause present in combined returned text",
                "anchor_clause_coverage": "same metric using anchor chunks before adjacent expansion",
                "unsupported_retrieval": "any match for an unanswerable query",
                "latency": "warm sequential local retriever wall time including SQL and context validation; excludes model startup",
            },
            "limitations": "No answer generation, semantic correctness or independent blind review claim. Families correlated; scope filtering not part of this factorial comparison.",
            "embedding": {
                "model": EMBEDDING_MODEL,
                "revision": EMBEDDING_REVISION,
                "normalized": True,
                "distance": "cosine",
            },
            "reranker": {"model": RERANKER_MODEL, "revision": RERANKER_REVISION},
        },
    )
    print("Frozen 40 development queries and corpus before retrieval results.")


def validate():
    manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    for name in ("cases", "corpus"):
        if digest((DIRECTORY / (name + ".json")).read_bytes()) != manifest[name + "_sha256"]:
            raise ValueError("Frozen input hash mismatch")
    cases = json.loads((DIRECTORY / "cases.json").read_text(encoding="utf-8"))
    corpus = json.loads((DIRECTORY / "corpus.json").read_text(encoding="utf-8"))
    if len(cases) != 40 or len({row["id"] for row in cases}) != 40:
        raise ValueError("Invalid sample count or duplicate sample IDs")
    for case in cases:
        if case["split"] != "dev" or case["answerable"] != bool(case["required_text_groups"]):
            raise ValueError("Invalid development-only annotation")
        for group in case["required_text_groups"]:
            if not group or any(
                not any(clause in document["text"] for document in corpus) for clause in group
            ):
                raise ValueError("Necessary evidence not present in frozen corpus")
    print("Frozen development dataset, hashes and necessary clauses valid.")


def normalize(text):
    return "".join(text.split())


def metrics(case, matches, anchors=False):
    if anchors:
        matches = [row for row in matches if not row.get("context_of")]
    if not case["answerable"]:
        return {"unsupported_retrieval": bool(matches)}
    text_by_source = {}
    for row in matches:
        text_by_source.setdefault(row["filename"], []).append(row["content"])
    alltext = "".join(normalize("\n".join(parts)) for parts in text_by_source.values())
    groups = case["required_text_groups"]
    coverage = sum(any(normalize(clause) in alltext for clause in group) for group in groups) / len(
        groups
    )
    sources = {
        key.rsplit("-", 1)[0].replace(".txt", ".md")
        for group in case["evidence_groups"]
        for key in group
    }
    return {"source_hit": bool(sources & text_by_source.keys()), "clause_coverage": coverage}


def run(args):
    from alembic import command

    manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    for name in ("cases", "corpus"):
        if digest((DIRECTORY / (name + ".json")).read_bytes()) != manifest[name + "_sha256"]:
            raise ValueError("Frozen input hash mismatch")
    cases = json.loads((DIRECTORY / "cases.json").read_text(encoding="utf-8"))
    corpus = json.loads((DIRECTORY / "corpus.json").read_text(encoding="utf-8"))
    if args.ids:
        wanted = set(args.ids.split(","))
        if wanted - {case["id"] for case in cases}:
            raise ValueError("Unknown sample ID")
        cases = [case for case in cases if case["id"] in wanted]
    output = Path(args.output)
    if output.exists():
        raise ValueError("Output already exists; do not overwrite experiment traces")
    settings = WorkbenchSettings(
        database_url=SecretStr(os.environ["WORKBENCH_RAG_EVAL_MYSQL_URL"]),
        storage_root=str(ROOT / "work/fullstack/rag-experiment/files"),
    )
    database = Database(settings)
    if database.engine.url.database != "commercedesk_test_rag_eval":
        raise ValueError("Requires dedicated commercedesk_test_rag_eval database")
    command.upgrade(migration_config(settings), "head")
    # Credentials are disposable in the isolated experiment DB; never affect dev users.
    seed_demo(database, os.environ["WORKBENCH_RAG_EVAL_PASSWORD"])
    index = ChromaIndex(settings)
    index.warm()
    organizations = {}
    filenames = {}
    for chunk, slug in (("fixed", "demo-a"), ("structural", "demo-b")):
        with database.sessions() as session:
            from workbench.models import Organization

            organization = session.scalar(select(Organization).where(Organization.slug == slug))
            actor = session.scalar(
                select(User).where(User.organization_id == organization.id, User.role == "admin")
            )
            organizations[chunk] = organization.id
            for document in corpus:
                _, version, _ = create_version(
                    session,
                    actor,
                    settings,
                    document["filename"],
                    document["text"].encode(),
                    VersionOptions(chunk_strategy=chunk),
                    title=document["original"],
                )
                filenames[version.id] = document["filename"]
        while work_once(database, settings, index):
            pass
        from workbench.knowledge_models import DocumentVersion

        with database.sessions() as session:
            actor = session.scalar(
                select(User).where(User.organization_id == organization.id, User.role == "admin")
            )
            versions = session.scalars(
                select(DocumentVersion).where(DocumentVersion.organization_id == organization.id)
            ).all()
            for version in versions:
                if version.status != "ready":
                    raise RuntimeError("Experiment ingestion failed: " + str(version.error_code))
                publish(session, actor, version.id, False)
    retriever = KnowledgeRetriever(database, settings, index)
    arms = manifest["arms"]
    rows = []
    output.mkdir(parents=True)
    write(output / "manifest.json", manifest)
    with (output / "traces.jsonl").open("w", encoding="utf-8") as stream:
        for number, case in enumerate(cases):
            for arm in arms[number % 6 :] + arms[: number % 6]:
                chunk, strategy = arm.split("/")
                started = perf_counter()
                result = retriever.search(
                    organizations[chunk],
                    SearchInput(query=case["question"], strategy=strategy, top_k=3),
                )
                row = {
                    "case": case,
                    "arm": arm,
                    "elapsed_ms": (perf_counter() - started) * 1000,
                    "result": result,
                    "metrics": metrics(case, result["matches"]),
                    "anchor_metrics": metrics(case, result["matches"], True),
                }
                rows.append(row)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
            print(f"{number + 1}/{len(cases)} {case['id']}", flush=True)
    summaries = {}
    for arm in arms:
        selected = [row for row in rows if row["arm"] == arm]
        positive = [row for row in selected if row["case"]["answerable"]]
        negative = [row for row in selected if not row["case"]["answerable"]]
        times = sorted(row["elapsed_ms"] for row in selected)
        summaries[arm] = {
            "answerable": len(positive),
            "unanswerable": len(negative),
            "source_hit": statistics.mean(row["metrics"]["source_hit"] for row in positive)
            if positive
            else None,
            "necessary_clause_coverage": statistics.mean(
                row["metrics"]["clause_coverage"] for row in positive
            )
            if positive
            else None,
            "anchor_clause_coverage": statistics.mean(
                row["anchor_metrics"]["clause_coverage"] for row in positive
            )
            if positive
            else None,
            "unsupported_retrieval": statistics.mean(
                row["metrics"]["unsupported_retrieval"] for row in negative
            )
            if negative
            else None,
            "p50_ms": statistics.median(times),
            "p95_ms": times[min(len(times) - 1, int(len(times) * 0.95))],
        }
    write(
        output / "summary.json",
        {
            "manifest_sha256": digest((DIRECTORY / "manifest.json").read_bytes()),
            "paid_model_calls": 0,
            "arms": summaries,
        },
    )
    database.close()
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["freeze", "validate", "run"])
    parser.add_argument("--ids")
    parser.add_argument("--output", default="work/fullstack/rag-experiment/results-v1")
    args = parser.parse_args()
    if args.mode == "freeze":
        freeze()
    elif args.mode == "validate":
        validate()
    else:
        run(args)
