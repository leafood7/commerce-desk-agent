"""Create the local demonstration orders without overwriting existing orders."""

import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from business.order_repository import get_database_path, initialize_demo_orders


if __name__ == "__main__":
    load_dotenv(PROJECT_ROOT / ".env")
    count = initialize_demo_orders()
    print(f"订单数据库：{get_database_path()}")
    print(f"初始化完成，当前共 {count} 条订单；已有订单保持不变。")
