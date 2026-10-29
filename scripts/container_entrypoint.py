"""Start the explicitly selected application; never silently switch backends."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    application = os.environ.get("COMMERCE_APP", "workbench")
    if application == "legacy":
        from business.order_repository import initialize_demo_orders

        initialize_demo_orders()
        for script, arguments in (
            ("init_knowledge.py", ["--if-missing"]),
            ("init_reranker.py", ["--if-enabled"]),
        ):
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT, check=True
            )
        target = "service:app"
    elif application == "workbench":
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
        target = "workbench.app:app"
    else:
        raise ValueError("COMMERCE_APP must be workbench or legacy")
    import uvicorn

    uvicorn.run(target, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
