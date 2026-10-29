import os

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr

from workbench.app import create_app
from workbench.cli import migration_config
from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.models import Base
from workbench.seed import seed_demo

PASSWORD = "test-only-strong-password-2026"


@pytest.fixture
def workbench(tmp_path):
    # Real MySQL is opt-in; use a dedicated disposable database, never application data.
    url = os.environ.get("WORKBENCH_TEST_MYSQL_URL")
    settings = WorkbenchSettings(
        environment="test",
        agent_router_mode="rules",
        storage_root=str(tmp_path / "files"),
        database_url=SecretStr(
            url or ("sqlite:///" + str(tmp_path / "business.sqlite3").replace("\\", "/"))
        ),
    )
    database = Database(settings)
    if url:
        if not database.engine.url.database.startswith("commercedesk_test"):
            raise RuntimeError("MySQL tests require an isolated commercedesk_test* database")
        Base.metadata.drop_all(database.engine)
        with database.engine.begin() as connection:
            from sqlalchemy import text

            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    command.upgrade(migration_config(settings), "head")
    seed_demo(database, PASSWORD)
    from helpers import LocalTestGateway

    app = create_app(settings, gateway=LocalTestGateway(database, settings))
    with TestClient(app) as client:
        yield client, app, settings
    database.close()


@pytest.fixture
def logged_in(workbench):
    client, app, settings = workbench
    tokens = {}

    def headers(role="support", organization="demo-a"):
        key = (organization, role)
        if key not in tokens:
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "organization": organization,
                    "email": role + "@demo.test",
                    "password": PASSWORD,
                },
            )
            assert response.status_code == 200, response.text
            tokens[key] = response.json()["access_token"]
        return {"Authorization": "Bearer " + tokens[key]}

    return client, app, headers
