"""Frozen local retrieval comparison. Algorithm never receives gold clauses."""

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data/evaluation/return_agent_v1"


def load_dataset(directory, identifiers=None):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["hashes"].items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen fixture hash changed: " + name)
    if (
        hashlib.sha256(
            (ROOT / "docs/fullstack/return-agent-stage-protocol.md").read_bytes()
        ).hexdigest()
        != manifest["protocol_sha256"]
    ):
        raise ValueError("Frozen protocol changed")
    values = {
        name[:-5]: json.loads((directory / name).read_text(encoding="utf-8"))
        for name in manifest["hashes"]
    }
    cases = values["cases"]
    ids = {case["id"] for case in cases}
    if len(ids) != len(cases) or len(cases) != manifest["cases"]:
        raise ValueError("Invalid case identities")
    if identifiers and (len(set(identifiers)) != len(identifiers) or set(identifiers) - ids):
        raise ValueError("Invalid or duplicate case IDs")
    values["cases"] = [c for c in cases if not identifiers or c["id"] in identifiers]
    return manifest, values


def metrics(case, selected):
    text = "".join("".join(row["content"].split()) for row in selected)
    groups = case["required_text_groups"]
    return {
        "necessary_clause_coverage": sum(
            any("".join(q.split()) in text for q in group) for group in groups
        )
        / len(groups)
        if groups
        else None,
        "source_hit": any(row["version_id"] in case["allowed_document_ids"] for row in selected),
        "inapplicable_fraction": sum(
            row["version_id"] not in case["allowed_document_ids"] for row in selected
        )
        / len(selected)
        if selected
        else 0,
        "unsupported_retrieval": bool(selected) if not case["answerable"] else None,
    }


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def run(output, manifest, data, arms, baseline_report=None):
    # Validation and overwrite checks happen before imports/model/index initialization.
    if output.exists():
        raise ValueError("Output already exists; retain every experiment")
    import chromadb

    from business.rag_retrieval import (
        RERANKER_MODEL,
        RERANKER_REVISION,
        BM25Index,
        reciprocal_rank_fusion,
        rerank,
        reranker,
    )
    from workbench.document_parsing import chunks_from_blocks, text_blocks
    from workbench.evidence_context import expand_context
    from workbench.knowledge_index import EMBEDDING_MODEL, EMBEDDING_REVISION, embedding_function

    output.mkdir(parents=True)
    config = data["config"]
    embedding = embedding_function()
    reranker()
    index = chromadb.PersistentClient(path=str(output / "index"))
    collection = index.get_or_create_collection(
        "return_dev", embedding_function=embedding, metadata={"hnsw:space": "cosine"}
    )
    docs = []
    for document in data["corpus"]:
        for chunk in chunks_from_blocks(
            text_blocks(document["text"]),
            strategy=config["chunk"],
            size=config["size"],
            overlap=config["overlap"],
        ):
            docs.append(
                {
                    **chunk,
                    "id": document["id"] + "-" + str(chunk["position"]),
                    "version_id": document["id"],
                    "organization_id": "isolated-return-dev",
                }
            )
    collection.upsert(
        ids=[d["id"] for d in docs],
        documents=[d["content"] for d in docs],
        metadatas=[{"version_id": d["version_id"]} for d in docs],
    )
    sparse = {None: BM25Index(docs)}
    for document in data["corpus"]:
        sparse[document["id"]] = BM25Index([d for d in docs if d["version_id"] == document["id"]])
    by_id = {d["id"]: d for d in docs}
    run_manifest = {
        **manifest,
        "config": config,
        "embedding": [EMBEDDING_MODEL, EMBEDDING_REVISION],
        "reranker": [RERANKER_MODEL, RERANKER_REVISION],
        "chunks": len(docs),
        "arms": arms,
        "baseline_report": baseline_report,
        "paid_calls": 0,
        "algorithm_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
    rows = []
    for number, case in enumerate(data["cases"]):
        for arm in arms if number % 2 == 0 else list(reversed(arms)):
            # Binding is supplied by the business context, never the gold document list.
            version = (
                data["bindings"].get(case["latest_binding"]) if arm == "policy_scoped" else None
            )
            candidates = [d for d in docs if not version or d["version_id"] == version]
            started = time.perf_counter()
            kwargs = {"where": {"version_id": version}} if version else {}
            dense_result = collection.query(
                query_texts=[case["question"]],
                n_results=min(config["pool"], len(candidates)),
                **kwargs,
            )
            dense = [
                {**by_id[key], "distance": distance}
                for key, distance in zip(dense_result["ids"][0], dense_result["distances"][0])
                if distance <= config["distance"]
            ]
            sparse_rows = sparse[version].search(case["question"], config["pool"])
            fusion = reciprocal_rank_fusion([dense, sparse_rows], config["pool"])
            ranked = [
                d
                for d in rerank(case["question"], fusion)
                if d["rerank_score"] >= config["rerank_threshold"]
            ]
            anchors = ranked[: config["top_k"]]
            selected = expand_context(anchors, candidates, config["context_chars"], "neighbors")
            elapsed = (time.perf_counter() - started) * 1000
            row = {
                "case_id": case["id"],
                "family": case["family"],
                "arm": arm,
                "question": case["question"],
                "business_binding": case["latest_binding"],
                "applied_version_filter": version,
                "answerable": case["answerable"],
                "metrics": metrics(case, selected),
                "elapsed_ms": elapsed,
                "context_chars": sum(len(d["content"]) for d in selected),
                "trace": {
                    "dense": [d["id"] for d in dense],
                    "sparse": [d["id"] for d in sparse_rows],
                    "fusion": [d["id"] for d in fusion],
                    "reranked": [d["id"] for d in ranked],
                    "anchors": [d["id"] for d in anchors],
                },
                "evidence": selected,
            }
            rows.append(row)
            with (output / "traces.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{number + 1}/{len(data['cases'])}", flush=True)
    summary = {
        "development_only": True,
        "cases": len(data["cases"]),
        "arms": {},
        "paid_model_calls": 0,
        "limitations": [
            "Author-written related development families, not independent blind evaluation.",
            "Source/clause coverage is not generated answer correctness.",
            "Synthetic merchant bindings restrict scope; no claim of automatic policy applicability inference.",
        ],
    }
    for arm in arms:
        selected = [r for r in rows if r["arm"] == arm]
        positive = [r for r in selected if r["answerable"]]
        negative = [r for r in selected if not r["answerable"]]
        times = [r["elapsed_ms"] for r in selected]
        summary["arms"][arm] = {
            "necessary_clause_coverage": statistics.mean(
                r["metrics"]["necessary_clause_coverage"] for r in positive
            ),
            "full_clause_cases": sum(
                r["metrics"]["necessary_clause_coverage"] == 1 for r in positive
            ),
            "positive_cases": len(positive),
            "negative_cases": len(negative),
            "unsupported_retrieval_cases": sum(
                r["metrics"]["unsupported_retrieval"] for r in negative
            ),
            "mean_inapplicable_fraction": statistics.mean(
                r["metrics"]["inapplicable_fraction"] for r in selected
            ),
            "mean_context_chars": statistics.mean(r["context_chars"] for r in selected),
            "p50_ms": percentile(times, 0.5),
            "p95_ms": percentile(times, 0.95),
        }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "run"])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--version", choices=["baseline", "policy_scoped", "all"], default="all")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "work/return-agent-v1/rag")
    parser.add_argument("--case-id", action="append")
    args = parser.parse_args()
    try:
        manifest, data = load_dataset(args.dataset, args.case_id)
        if args.baseline and not args.baseline.is_file():
            raise ValueError("Baseline report does not exist")
        if args.command == "validate":
            print(json.dumps({"cases": len(data["cases"]), "frozen": True}))
            return
        run(
            args.output,
            manifest,
            data,
            ["baseline", "policy_scoped"] if args.version == "all" else [args.version],
            str(args.baseline) if args.baseline else None,
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
