"""PostgreSQL async database package for the StrataOS financial core.

This package is the ONLY entry point for PostgreSQL access. All financial
writes must go through backend/services/financial_core, which calls these
helpers. No router or service is allowed to import SQLAlchemy models or
raw engine/session handles directly from this package.

MongoDB routes and all existing functionality are completely unaffected
until the financial_core_enabled feature flag is set to True per-scheme.
"""
