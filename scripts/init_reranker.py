"""从官方模型仓库下载固定版本的重排模型。"""

import sys
import os
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from huggingface_hub import snapshot_download
from business.rag_retrieval import RERANKER_MODEL, RERANKER_REVISION

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-enabled", action="store_true")
    args = parser.parse_args()
    if args.if_enabled and os.getenv("SUPPORTPILOT_RETRIEVAL_STRATEGY", "vector") != "hybrid_rerank":
        print("当前检索策略无需重排模型。")
        sys.exit(0)
    options = dict(repo_id=RERANKER_MODEL, revision=RERANKER_REVISION,
                   allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"],
                   cache_dir=str(ROOT / "work/huggingface/hub"), max_workers=3)
    try:
        path = snapshot_download(**options, local_files_only=True)
        if not all((Path(path) / name).is_file() for name in ["config.json", "model.safetensors", "tokenizer.json"]):
            raise FileNotFoundError("重排模型缓存不完整")
    except (OSError, FileNotFoundError):
        path = snapshot_download(**options)
    print(f"重排模型已就绪：{path}")
