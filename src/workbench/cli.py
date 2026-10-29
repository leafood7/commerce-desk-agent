"""Business database operations; does not import a model or call paid APIs."""

import argparse
import json
import os

from alembic import command
from alembic.config import Config

from workbench.config import ROOT, WorkbenchSettings
from workbench.database import Database
from workbench.seed import seed_demo


def migration_config(settings):
    config = Config(str(ROOT / "alembic.ini"))
    database = Database(settings)
    url = database.engine.url.render_as_string(hide_password=False)
    database.close()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "seed", "check"])
    parser.add_argument("--password-env", default="WORKBENCH_DEMO_PASSWORD")
    args = parser.parse_args()
    settings = WorkbenchSettings()
    if args.command == "migrate":
        command.upgrade(migration_config(settings), "head")
        return
    database = Database(settings)
    try:
        database.check_schema()
        if args.command == "seed":
            if settings.environment == "production":
                parser.error("Demo seeding is disabled in production")
            password = os.environ.get(args.password_env)
            if not password:
                parser.error("Set the named password environment variable (at least 12 characters)")
            print(
                json.dumps(
                    {"accounts": seed_demo(database, password), "existing_passwords_reset": False},
                    ensure_ascii=False,
                )
            )
        else:
            print(
                json.dumps(
                    {
                        "schema": "0007_return_applications",
                        "ready": True,
                        "dialect": database.engine.dialect.name,
                    }
                )
            )
    finally:
        database.close()


if __name__ == "__main__":
    main()
