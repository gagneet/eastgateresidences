# @featuretrace:owner-transfers — A current owner-of-record must be ONE person per party row.
# Layer: test
# Data flow: core.ownership_periods (valid_to IS NULL) -> core.parties.legal_name (building-scoped).
# Related: backend/utils/name_utils.py (format_owner_names combines at DISPLAY time)
#          tasks/GAP-IDENTITY-002-joint-name-legacy-parties.md
"""Two owners means two party rows, not one row with two names in it.

The platform's model is one individual per `core.parties` row, combined only at display
time by `name_utils.format_owner_names(primary, secondary, separator=" & ")`. Storing
"Tejas Joshi & Salas Kodape" as a single legal_name breaks that in ways that are quiet
rather than loud:

  * a salutation cannot describe two people, so splitting the honorific out assigns the
    first person's title to the pair and strands the second's mid-string
  * one primary_email for two owners means an invitation to one is an invitation to both
  * per-owner arrears, ballots and correspondence cannot address one holder

East Gate carries 48 such rows from the original 2020-12-01 onboarding import, where a
title-deed line was taken verbatim. **None of them currently owns a lot** — every current
owner is already an individual party — so this asserts the invariant that matters rather
than the historical residue, which is tracked separately for cleanup.
"""

import re

import pytest

# " & " or " and " between two names. Deliberately not a bare "&": a legitimate legal
# name may contain one ("Smith & Sons Pty Ltd" is a company, not two people).
JOINT = re.compile(r"\s(&|and)\s", re.I)


class TestJointNameDetection:
    @pytest.mark.parametrize("name", [
        "Tejas Joshi & Salas Kodape",
        "Tin Leung and Jennifer Leung",
        "Dr Gunjan Pandey & Dr Rinku Pandey",
    ])
    def test_two_people_in_one_string_is_detected(self, name):
        assert JOINT.search(name)

    @pytest.mark.parametrize("name", [
        "Rachel Clarke",
        "Niran Poglobe Karaeni",
        "Sandra Anderson-Wright",
    ])
    def test_an_ordinary_individual_name_is_not_flagged(self, name):
        assert not JOINT.search(name)


@pytest.mark.asyncio
async def test_no_lot_is_currently_owned_by_a_joint_name_party():
    """The invariant. Skips when no live Postgres is configured.

    A regression here means an import or transfer wrote two people into one party row
    again, and every downstream per-owner feature silently degrades for that lot.
    """
    try:
        import os

        import asyncpg
        from dotenv import load_dotenv

        load_dotenv("backend/.env")
        conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    except Exception as exc:                      # pragma: no cover - env dependent
        pytest.skip(f"live Postgres unavailable: {exc}")

    try:
        await conn.execute("SET app.tenant_id = '9e9d75c2-bd92-4695-8487-1592018c3af9'")
        offenders = await conn.fetch("""
            SELECT l.unit_number, p.legal_name
              FROM core.lots l
              JOIN core.ownership_periods op
                    ON op.lot_id = l.lot_id AND op.valid_to IS NULL
              JOIN core.parties p ON p.party_id = op.owner_party_id
             WHERE p.legal_name ~ ' (&|and) '
        """)
    finally:
        await conn.close()

    assert not offenders, (
        "a current owner-of-record holds two names in one party row: "
        + ", ".join(f"{r['unit_number']}={r['legal_name']}" for r in offenders)
    )
