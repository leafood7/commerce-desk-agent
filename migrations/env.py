from alembic import context
from sqlalchemy import create_engine, pool

import workbench.flow_models  # noqa: F401
import workbench.job_models  # noqa: F401
import workbench.knowledge_models  # noqa: F401
import workbench.profile_models  # noqa: F401
import workbench.quota_models  # noqa: F401
import workbench.return_models  # noqa: F401
from workbench.config import WorkbenchSettings
from workbench.models import Base

config = context.config
url = (
    config.get_main_option("sqlalchemy.url") or WorkbenchSettings().database_url.get_secret_value()
)


def offline():
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def online():
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    offline()
else:
    online()
