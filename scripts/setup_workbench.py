"""Explicit local bootstrap; writes demonstration credentials only to ignored work/."""

import json
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from alembic import command

    from workbench.cli import migration_config
    from workbench.config import WorkbenchSettings
    from workbench.database import Database
    from workbench.seed import seed_demo

    environment = ROOT / ".env"
    values = dotenv_values(environment)
    portable = ROOT / "work/fullstack/mysql-local/credentials.json"
    if not values.get("WORKBENCH_DATABASE_URL") and portable.exists():
        url = json.loads(portable.read_text(encoding="utf-8"))["development_url"]
        set_key(environment, "WORKBENCH_DATABASE_URL", url)
    settings = WorkbenchSettings()
    if settings.environment == "production":
        raise ValueError("Local demonstration bootstrap is disabled in production")
    private = ROOT / "work/fullstack/local-login.json"
    private.parent.mkdir(parents=True, exist_ok=True)
    password = (
        json.loads(private.read_text(encoding="utf-8"))["password"]
        if private.exists()
        else secrets.token_urlsafe(18)
    )
    command.upgrade(migration_config(settings), "head")
    database = Database(settings)
    try:
        accounts = seed_demo(database, password)
        private.write_text(
            json.dumps(
                {"password": password, "accounts": accounts, "existing_passwords_reset": False},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Business database ready: " + database.engine.dialect.name)
        print("Demo credentials saved privately to work/fullstack/local-login.json")
        print("Existing account passwords are never reset by this command.")
    finally:
        database.close()


if __name__ == "__main__":
    main()
