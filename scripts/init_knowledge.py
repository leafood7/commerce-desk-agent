"""Build the independent BGE and Chroma policy index."""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / ".env")

from business.knowledge import index_path, initialize_knowledge, is_index_ready


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化政策知识库")
    parser.add_argument("--if-missing", action="store_true", help="已有可用索引时跳过重建")
    args = parser.parse_args()
    if args.if_missing and is_index_ready():
        print(f"政策索引已就绪：{index_path()}")
    else:
        result = initialize_knowledge()
        print(f"已索引 {result['sources']} 份政策，{result['chunks']} 个片段。")
        print(f"向量模型：{result['model']}")
        print(f"索引目录：{result['path']}")
