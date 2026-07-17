"""One inert SQLAlchemy Core metadata registry for account schema one."""

from sqlalchemy import MetaData


metadata = MetaData(
    schema="cuevion_account",
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    },
)


__all__ = ("metadata",)
