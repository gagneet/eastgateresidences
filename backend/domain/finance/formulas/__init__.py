"""backend/domain/finance/formulas — pure, DB-free financial formula functions.

Each function here takes typed facts (Cents, Decimal, plain scalars) and
returns a result — no database import, no I/O, no float. See
backend/domain/finance/__init__.py for the layer's rules.
"""
