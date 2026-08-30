"""
User Seed Data — DEPRECATED (Mongo identity removed 2026-05-02).

This module produced Mongo user documents. Identity has moved to Postgres
(``core.users``); see ``backend/seeds/super_admins.py`` for the canonical
PG seed. The function below is no longer called from anywhere in the tree
and is kept only to preserve the historical fixture list (real-owner emails,
roles, unit numbers) that future onboarding tooling may reference.

Do NOT call ``get_users_seed_data()`` and write the result into Mongo —
``db.users`` and ``db.memberships`` were dropped in the same change.
"""
import os
import uuid
from datetime import datetime, timezone

import bcrypt


def _seed_password() -> str:
    """The password every seeded fixture account gets.

    From SEED_TEST_USER_PASSWORD, with NO DEFAULT. This module previously carried
    nine plaintext passwords, hashed inline at the call site, each attached to
    the real names and email addresses of East Gate owners and committee members.
    None of those accounts exists in the live stores today (verified 2026-08-26), so
    they were not live credentials, but they were committed alongside the people
    they belonged to, which is worse than an anonymous test password.

    Refusing beats defaulting: a default is what turns "seeded once for a demo" into
    "the production password", which is exactly what happened to the super-admin
    account this same seed family created (see seeds/super_admins.py).
    """
    password = os.environ.get("SEED_TEST_USER_PASSWORD")
    if not password:
        raise SystemExit(
            "SEED_TEST_USER_PASSWORD is not set. This seed will not invent a password.\n"
            "Supply one for the run only; do not add it to .env."
        )
    if len(password) < 12:
        raise SystemExit("SEED_TEST_USER_PASSWORD must be at least 12 characters.")
    return password


def get_users_seed_data():
    """
    Returns list of users to seed into the database.

    Default Admin:
        Email: administrator@eastgateresidences.com.au
        Password: the value of SEED_TEST_USER_PASSWORD (no default; see _seed_password)

    Real Test Users (Based on Actual Owners/EC Members):
        Chairman: anthony@eastgateresidences.com.au / SEED_TEST_USER_PASSWORD! (Anthony McDonald, UA063)
        EC Member: marcelo.dasilva@eastgateresidences.com.au / SEED_TEST_USER_PASSWORD! (Marcelo Ramos da Silva, UA046)
        Owner: avneet@eastgateresidences.com.au / SEED_TEST_USER_PASSWORD! (Avneet Rooprai, TH087)
        Tenant: tenant@eastgateresidences.com.au / SEED_TEST_USER_PASSWORD! (Emma Wilson, TH077)
    """

    def hash_pw(password):
        """Generated function header.

        Function: hash_pw
        Path: backend/seeds/users.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    now = datetime.now(timezone.utc).isoformat()

    def _base(extra: dict) -> dict:
        """Common fields required on every user document."""
        return {
            'building_id': '13195',
            'status': 'active',
            'is_active': True,
            'is_approved': True,
            'profile_image': None,
            'custom_permissions': {},
            'terms_accepted': True,
            'terms_accepted_at': now,
            'by_laws_acknowledged': False,
            'by_laws_acknowledgment_date': None,
            'is_name_flagged': False,
            'flag_reason': None,
            'totp_enabled': False,
            'totp_verified_at': None,
            'last_login_at': None,
            'last_login_ip': None,
            'created_at': now,
            'updated_at': now,
            **extra,
        }

    users = [
        _base({
            'id': str(uuid.uuid4()),
            'email': 'administrator@eastgateresidences.com.au',
            'mail_username': 'administrator@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'System Administrator',
            'role': 'super_admin',
            'unit_number': None,
            'phone': '+61 2 6123 4567',
            'phone_home': '+61 2 6123 4567',
            'phone_mobile': '+61 4 0175 0765',
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'anthony@eastgateresidences.com.au',
            'mail_username': 'anthony@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Anthony McDonald',
            'role': 'ec_member',
            'ec_position': 'CHAIRMAN',
            'unit_number': 'UA063',
            'phone': '+61 412 345 678',
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'buildingadmin@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Building Admin Test',
            'role': 'strata_admin',
            'phone': '+61 400 000 001',
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'marcelo.dasilva@eastgateresidences.com.au',
            'mail_username': 'marcelo.silva@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Marcelo Ramos da Silva',
            'role': 'ec_member',
            'unit_number': 'UA046',
            'phone': '+61 423 456 789',
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'avneet@eastgateresidences.com.au',
            'mail_username': 'avneet@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Avneet Rooprai',
            'role': 'owner',
            'unit_number': 'TH087',
            'phone': '+61 434 567 890',
            'by_laws_acknowledged': True,
            'by_laws_acknowledgment_date': now,
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'tenant@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Emma Wilson',
            'role': 'tenant',
            'unit_number': 'TH077',
            'phone': '+61 445 678 901',
            'by_laws_acknowledged': True,
            'by_laws_acknowledgment_date': now,
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'reception@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Admin Staff',
            'role': 'admin_staff',
            'unit_number': None,
            'phone': '+61 2 6285 0325',
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'agent@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Test Real Estate Agent',
            'role': 'real_estate_agent',
            'unit_number': None,
            'phone': '+61 456 789 012',
        }),
        _base({
            'id': str(uuid.uuid4()),
            'email': 'contractor@eastgateresidences.com.au',
            'password_hash': hash_pw(_seed_password()),
            'full_name': 'Test Contractor',
            'role': 'service_provider',
            'unit_number': None,
            'phone': '+61 467 890 123',
        }),
    ]

    return users


# User credentials for reference
SEED_USER_CREDENTIALS = """
=== Seed User Credentials ===

Every account below is seeded with the value of SEED_TEST_USER_PASSWORD.
The passwords are no longer listed here — this string was committed, and a
committed credential is a credential.

  administrator@eastgateresidences.com.au   super_admin
  anthony@eastgateresidences.com.au         ec_member (chairman)
  admin@eastgateresidences.com.au           strata_admin
  ec@eastgateresidences.com.au              ec_member
  gagneet@eastgateresidences.com.au         owner
  tenant@eastgateresidences.com.au          tenant
  reception@eastgateresidences.com.au       admin_staff
  agent@eastgateresidences.com.au           real_estate_agent
  contractor@eastgateresidences.com.au      service_provider
"""
