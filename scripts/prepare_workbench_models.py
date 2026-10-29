"""Explicit preparation of the two pinned public retrieval models."""

import argparse

from huggingface_hub import snapshot_download

from business.rag_retrieval import RERANKER_MODEL, RERANKER_REVISION
from workbench.config import ROOT
from workbench.knowledge_index import EMBEDDING_MODEL, EMBEDDING_REVISION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download", action="store_true", help="Allow downloading pinned model files"
    )
    args = parser.parse_args()
    cache = ROOT / "work/huggingface/hub"
    for model, revision in (
        (EMBEDDING_MODEL, EMBEDDING_REVISION),
        (RERANKER_MODEL, RERANKER_REVISION),
    ):
        snapshot_download(
            model,
            revision=revision,
            cache_dir=cache,
            local_files_only=not args.download,
            allow_patterns=[
                "*.json",
                "*.txt",
                "*.safetensors",
                "*.model",
                "modules.json",
                "1_Pooling/*",
            ],
        )
        print(model + " " + revision + " cache ready")


if __name__ == "__main__":
    main()
