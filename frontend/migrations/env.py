"""Offline-only Alembic environment for the inactive account foundation."""

from alembic import context

from cuevion_db.metadata import metadata
import cuevion_db.account_schema  # noqa: F401 -- registers the seven tables


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        compare_type=True,
        version_table="cuevion_account_alembic_version",
        version_table_schema="public",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    raise RuntimeError("online database migrations are not activated")


try:
    _active_alembic_configuration = context.config
except (AttributeError, NameError):
    _active_alembic_configuration = None

if _active_alembic_configuration is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()


del _active_alembic_configuration
