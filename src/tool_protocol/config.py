"""只读工具进程配置；独立客户端和Agent使用相同默认值。"""
import os
from pathlib import Path
from dotenv import dotenv_values
ROOT = Path(__file__).resolve().parents[2]
READONLY_KEYS = (
    'SUPPORTPILOT_DB_PATH', 'SUPPORTPILOT_CHROMA_PATH', 'SUPPORTPILOT_RETRIEVAL_STRATEGY',
    'SUPPORTPILOT_RAG_POOL_SIZE', 'SUPPORTPILOT_RERANK_THRESHOLD', 'HF_HUB_OFFLINE',
    'TRANSFORMERS_OFFLINE', 'HF_HOME', 'HF_HUB_DISABLE_TELEMETRY', 'SUPPORTPILOT_MCP_TIMEOUT',
)


def readonly_environment():
    values = dotenv_values(ROOT / '.env')
    env = {key: os.environ.get(key, values.get(key)) for key in READONLY_KEYS if os.environ.get(key, values.get(key)) is not None}
    env.setdefault('HF_HUB_OFFLINE', '1')
    env.setdefault('TRANSFORMERS_OFFLINE', '1')
    env.update({'PYTHONPATH': str(ROOT / 'src'), 'PYTHONUTF8': '1'})
    return env
