from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import workbench.trace_models  # noqa: F401
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
        from time import perf_counter

        from workbench.tracing import active_span

        @event.listens_for(self.engine, "before_cursor_execute")
        def trace_sql_start(conn, cursor, statement, parameters, execution_context, executemany):
            execution_context._trace_started = perf_counter()

        @event.listens_for(self.engine, "after_cursor_execute")
        def trace_sql_end(conn, cursor, statement, parameters, execution_context, executemany):
            row = active_span.get()
            if row is not None:
                details = row["details"]
                details["db_calls"] = details.get("db_calls", 0) + 1
                details["db_ms"] = round(
                    details.get("db_ms", 0)
                    + (perf_counter() - execution_context._trace_started) * 1000,
                    2,
                )

        @event.listens_for(self.engine, "handle_error")
        def trace_sql_error(exception_context):
            row = active_span.get()
            if row is not None:
                row["details"]["db_errors"] = row["details"].get("db_errors", 0) + 1

    def check_schema(self):
        with self.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0012_request_traces":
                raise RuntimeError("Business schema is not current; run alembic upgrade head")

    def close(self):
        self.engine.dispose()
