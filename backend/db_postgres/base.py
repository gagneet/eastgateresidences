"""SQLAlchemy 2.x declarative base with enforced naming conventions.

All SQLAlchemy ORM models for the PostgreSQL financial core must inherit
from ``Base`` defined here to get consistent constraint and index names
across Alembic migrations.
"""
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Consistent, predictable names for every auto-generated constraint and index.
# These patterns match Alembic's autogenerate expectations.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Shared declarative base for PostgreSQL ORM models."""

    metadata = metadata
