from alembic import context

from app.config import get_settings
from app.db.base import Base
from app.db.registry import import_all_models
from app.db.session import make_engine

import_all_models()
target_metadata = Base.metadata


def _url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("url") or get_settings().effective_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
