from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from workbench.config import ROOT, WorkbenchSettings


class Database:
    def __init__(self, settings: WorkbenchSettings):
        self.settings = settings
        url = make_url(settings.database_url.get_secret_value())
        options = {"pool_pre_ping": True, "hide_parameters": True}
        if url.drivername == "sqlite":
            if url.database and url.database != ":memory:":
                path = Path(url.database)
                if not path.is_absolute():
                    path = ROOT / path
                path.parent.mkdir(parents=True, exist_ok=True)
                url = url.set(database=str(path))
            options["connect_args"] = {"check_same_thread": False, "timeout": 20}
        else:
            options["pool_recycle"] = 1800
            options["connect_args"] = {"connect_timeout": 5}
        self.engine = create_engine(url, **options)
        if url.drivername == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def check_schema(self):
        with self.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0007_return_applications":
                raise RuntimeError("Business schema is not current; run alembic upgrade head")

    def close(self):
        self.engine.dispose()
