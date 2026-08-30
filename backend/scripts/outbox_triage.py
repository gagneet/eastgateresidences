#!/usr/bin/env python3
# @featuretrace:financial_core — Outbox dead-letter triage / replay / write-off helper.
# Layer: script (cutover housekeeping)
# Data flow: core.outbox (dead-lettered rows) -> outbox_relay._dispatch_event ->
#            MongoDB events_archive (the SAME delivery path the live relay uses).
# Related: backend/workers/outbox_relay.py (the relay this replays through)
#          backend/scripts/audits/cutover_readiness_snapshot.py (§6 reports the backlog)
#          docs/architecture/go_live_readiness_2026-09-01.md §6 / §7 (P3 outbox drain)
"""Triage, replay, or write-off the dead-lettered `core.outbox` backlog.

WHY THIS EXISTS
---------------
The go-live readiness doc §6 records ~15,256 dead-lettered outbox events (the
PG->Mongo relay had a building-id resolution bug, GAP-FIN-061, since fixed). Those
rows are `published_at IS NULL AND (attempts >= 5 OR last_error LIKE '[DEAD-LETTER]%')`,
so the live relay skips them forever. This helper lets an operator:

  * `triage`    — (default, READ-ONLY) break the backlog down by event_type, error
                  signature, age, and — crucially — how many are *replayable* now
                  (their scheme_id resolves to a building) vs *unresolvable*
                  (no scheme_id / no scheme match), which is the replay-vs-write-off
                  decision the readiness doc P3 asks for.
  * `replay`    — re-deliver selected dead-letter rows through the EXACT same path
                  the live relay uses (`outbox_relay._dispatch_event` -> Mongo
                  `events_archive`, idempotent on duplicate _id). NOT a bespoke
                  re-implementation. Successful rows get `published_at`; still-failing
                  rows keep a fresh `[DEAD-LETTER]` reason.
  * `write-off` — mark genuinely-undeliverable rows with an auditable
                  `[WRITTEN-OFF] <reason>` marker (optionally also setting
                  published_at so they leave the §6 backlog metric — off by default,
                  because that field means "archived to Mongo", which they were not).

SAFETY
------
- `triage` never writes. `replay` and `write-off` are DRY-RUN unless `--apply`.
- RLS: `core.outbox` (migration 0081) has a bypass clause for the zero-UUID
  sentinel; the relay is deliberately cross-tenant, so this uses the same bypass
  via `SET LOCAL app.tenant_id` inside each transaction, exactly like the relay.
- Replay reuses the relay's own dispatch, so the fixed building-id resolution and
  idempotent duplicate-_id handling apply automatically.

USAGE (on the live server, from the repo root)
----------------------------------------------
    cd backend
    # 1. See what's in the backlog and how much is replayable (no writes):
    venv/bin/python3 scripts/outbox_triage.py triage
    # 2. Replay a bounded first batch (dry-run, then --apply):
    venv/bin/python3 scripts/outbox_triage.py replay --limit 200
    venv/bin/python3 scripts/outbox_triage.py replay --limit 200 --apply
    # 3. Write off the genuinely unresolvable remainder, with a reason:
    venv/bin/python3 scripts/outbox_triage.py write-off --unresolvable \
        --reason "No scheme_id; pre-GAP-FIN-061 orphan events" --apply

Reads DATABASE_URL / MONGO_URL / DB_NAME from backend/.env.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"
DEAD_LETTER_PREFIX = "[DEAD-LETTER]"
WRITTEN_OFF_PREFIX = "[WRITTEN-OFF]"
MAX_RETRIES = 5

# Predicate for "stuck, needs a human decision" rows: unpublished and either
# past the retry ceiling or explicitly dead-lettered — but NOT already written off.
_DEAD_LETTER_WHERE = (
    "published_at IS NULL "
    "AND (COALESCE(attempts, 0) >= :max_retries "
    "     OR COALESCE(last_error, '') LIKE :dl_prefix) "
    "AND COALESCE(last_error, '') NOT LIKE :wo_prefix"
)
_PARAMS = {
    "max_retries": MAX_RETRIES,
    "dl_prefix": f"{DEAD_LETTER_PREFIX}%",
    "wo_prefix": f"{WRITTEN_OFF_PREFIX}%",
}


async def _set_bypass(session) -> None:
    from sqlalchemy import text
    await session.execute(text(f"SET LOCAL app.tenant_id = '{BYPASS_TENANT}'"))


async def _has_column(session, table: str, column: str) -> bool:
    from sqlalchemy import text
    r = await session.execute(
        text("SELECT 1 FROM information_schema.columns "
             "WHERE table_schema='core' AND table_name=:t AND column_name=:c"),
        {"t": table, "c": column},
    )
    return r.fetchone() is not None


def _narrowing(args) -> tuple[str, dict]:
    """Build an optional WHERE fragment + params from --event-type / --scheme-id."""
    frag, params = "", {}
    if getattr(args, "event_type", None):
        frag += " AND event_type = :event_type"
        params["event_type"] = args.event_type
    if getattr(args, "scheme_id", None):
        frag += " AND scheme_id = CAST(:scheme_id AS uuid)"
        params["scheme_id"] = args.scheme_id
    return frag, params


async def action_triage(session, args) -> None:
    from sqlalchemy import text
    await _set_bypass(session)

    total = (await session.execute(
        text(f"SELECT COUNT(*) FROM core.outbox WHERE {_DEAD_LETTER_WHERE}"), _PARAMS
    )).scalar_one()
    written_off = (await session.execute(
        text("SELECT COUNT(*) FROM core.outbox WHERE published_at IS NULL "
             "AND COALESCE(last_error,'') LIKE :wo_prefix"), {"wo_prefix": f"{WRITTEN_OFF_PREFIX}%"}
    )).scalar_one()

    print("Outbox dead-letter triage  (READ-ONLY)")
    print("=" * 74)
    print(f"  Dead-lettered (needs decision): {total}")
    print(f"  Already written-off:            {written_off}")
    if total == 0:
        print("  Nothing to triage.")
        return

    # -- by event_type --
    print("\n  By event_type (top 15):")
    rows = await session.execute(
        text(f"SELECT event_type, COUNT(*) n FROM core.outbox WHERE {_DEAD_LETTER_WHERE} "
             "GROUP BY event_type ORDER BY n DESC LIMIT 15"), _PARAMS)
    for r in rows:
        print(f"    {str(r.event_type):<44} {r.n}")

    # -- by error signature (first 70 chars) --
    print("\n  By error signature (top 10):")
    rows = await session.execute(
        text(f"SELECT LEFT(COALESCE(last_error,'(none)'), 70) sig, COUNT(*) n "
             f"FROM core.outbox WHERE {_DEAD_LETTER_WHERE} "
             "GROUP BY sig ORDER BY n DESC LIMIT 10"), _PARAMS)
    for r in rows:
        print(f"    {r.n:<7} {r.sig}")

    # -- age --
    r = (await session.execute(
        text(f"SELECT MIN(created_at) oldest, MAX(created_at) newest "
             f"FROM core.outbox WHERE {_DEAD_LETTER_WHERE}"), _PARAMS)).fetchone()
    print(f"\n  Age span: oldest={r.oldest}  newest={r.newest}")

    # -- replayability: resolve each distinct scheme_id -> building_id once --
    print("\n  Replayability (can we resolve a building for delivery?):")
    null_scheme = (await session.execute(
        text(f"SELECT COUNT(*) FROM core.outbox WHERE {_DEAD_LETTER_WHERE} AND scheme_id IS NULL"),
        _PARAMS)).scalar_one()
    scheme_rows = await session.execute(
        text(f"SELECT scheme_id::text sid, COUNT(*) n FROM core.outbox "
             f"WHERE {_DEAD_LETTER_WHERE} AND scheme_id IS NOT NULL GROUP BY scheme_id"), _PARAMS)
    scheme_counts = {r.sid: r.n for r in scheme_rows}

    resolvable_rows = 0
    unresolvable_scheme_rows = 0
    from db_postgres.repos.identity_repo import get_scheme_by_id
    for sid, n in scheme_counts.items():
        try:
            scheme = await get_scheme_by_id(sid)
        except Exception:
            scheme = None
        if scheme and scheme.get("scheme_number"):
            resolvable_rows += n
        else:
            unresolvable_scheme_rows += n

    print(f"    replayable (scheme_id resolves to a building):  {resolvable_rows}")
    print(f"    unresolvable — scheme_id set but no scheme row:  {unresolvable_scheme_rows}")
    print(f"    unresolvable — no scheme_id at all:              {null_scheme}")
    print("\n  Recommendation:")
    print(f"    → replay the {resolvable_rows} replayable rows:  "
          "scripts/outbox_triage.py replay --limit <N> --apply")
    if unresolvable_scheme_rows or null_scheme:
        print(f"    → write off the {unresolvable_scheme_rows + null_scheme} unresolvable rows "
              "(a payload.mongo_building_id, if any writer set one, still lets them replay):")
        print("      scripts/outbox_triage.py write-off --unresolvable --reason \"...\" --apply")


async def action_replay(session, args) -> None:
    from sqlalchemy import text
    from datetime import datetime, timezone
    from workers.outbox_relay import _dispatch_event  # SAME delivery path as the live relay

    frag, extra = _narrowing(args)
    params = {**_PARAMS, **extra}

    await _set_bypass(session)
    # Count the candidate set first.
    candidate = (await session.execute(
        text(f"SELECT COUNT(*) FROM core.outbox WHERE {_DEAD_LETTER_WHERE}{frag}"), params
    )).scalar_one()
    print(f"Replay candidates (dead-lettered{' + filters' if frag else ''}): {candidate}")
    if candidate == 0:
        print("  Nothing to replay.")
        return
    if not args.apply:
        print(f"  DRY-RUN — would replay up to {args.limit} of them through the relay's "
              "_dispatch_event.\n  Re-run with --apply to deliver.")
        return

    # Lock a bounded batch so a concurrently-running live relay can't double-process.
    rows = (await session.execute(
        text(
            "SELECT id, tenant_id, scheme_id, event_type, aggregate_id, aggregate_type, payload "
            f"FROM core.outbox WHERE {_DEAD_LETTER_WHERE}{frag} "
            "ORDER BY created_at ASC LIMIT :lim FOR UPDATE SKIP LOCKED"),
        {**params, "lim": args.limit},
    )).fetchall()

    ok = fail = 0
    for row in rows:
        try:
            await _dispatch_event(
                event_id=row.id,
                event_type=row.event_type,
                aggregate_id=row.aggregate_id,
                aggregate_type=row.aggregate_type,
                payload=row.payload,
                scheme_id=str(row.scheme_id) if row.scheme_id else None,
            )
        except Exception as exc:
            fail += 1
            fresh = f"{DEAD_LETTER_PREFIX} replay: {str(exc)[:900]}"
            await session.execute(
                text("UPDATE core.outbox SET last_error = :e, last_attempt_at = :now WHERE id = :id"),
                {"e": fresh, "now": datetime.now(tz=timezone.utc), "id": row.id},
            )
            continue
        ok += 1
        await session.execute(
            text("UPDATE core.outbox SET published_at = :now, last_error = NULL WHERE id = :id"),
            {"now": datetime.now(tz=timezone.utc), "id": row.id},
        )
    await session.commit()
    print(f"\n✓ Replayed batch of {len(rows)}: delivered={ok}, still-failing={fail}")
    print(f"  Remaining candidates after this batch: ~{candidate - ok}")
    if candidate - ok > 0:
        print("  Re-run the same command to drain the next batch.")


async def action_write_off(session, args) -> None:
    from sqlalchemy import text
    from datetime import datetime, timezone

    if not args.reason:
        print("! write-off requires --reason (written to last_error for the audit trail).")
        sys.exit(2)

    frag, extra = _narrowing(args)
    if args.unresolvable:
        # scheme_id is NULL OR its scheme row doesn't exist — resolved in SQL via NOT EXISTS.
        frag += (" AND (scheme_id IS NULL OR NOT EXISTS "
                 "(SELECT 1 FROM core.schemes s WHERE s.scheme_id = core.outbox.scheme_id))")
    elif not frag:
        print("! Refusing to write off the ENTIRE backlog. Narrow it with --unresolvable, "
              "--event-type, or --scheme-id.")
        sys.exit(2)

    params = {**_PARAMS, **extra}
    await _set_bypass(session)
    n = (await session.execute(
        text(f"SELECT COUNT(*) FROM core.outbox WHERE {_DEAD_LETTER_WHERE}{frag}"), params
    )).scalar_one()
    print(f"Write-off candidates: {n}  reason={args.reason!r}  "
          f"mark_published={args.mark_published}")
    if n == 0:
        print("  Nothing to write off.")
        return
    if not args.apply:
        print("  DRY-RUN — re-run with --apply to mark them written-off.")
        return

    marker = f"{WRITTEN_OFF_PREFIX} {args.reason}"[:1000]
    set_clause = "last_error = :marker, last_attempt_at = :now"
    if args.mark_published:
        # Removes them from the §6 backlog metric. Honest caveat: they were NOT
        # delivered to Mongo — the marker records that they were consciously discarded.
        set_clause += ", published_at = :now"
    if await _has_column(session, "outbox", "dead_lettered_at"):
        set_clause += ", dead_lettered_at = COALESCE(dead_lettered_at, :now)"

    result = await session.execute(
        text(f"UPDATE core.outbox SET {set_clause} WHERE {_DEAD_LETTER_WHERE}{frag}"),
        {**params, "marker": marker, "now": datetime.now(tz=timezone.utc)},
    )
    await session.commit()
    print(f"\n✓ Wrote off {result.rowcount} row(s) with marker {marker!r}")


ACTIONS = {"triage": action_triage, "replay": action_replay, "write-off": action_write_off}


async def run(args) -> None:
    from db_postgres.session import make_session_factory
    session_factory = make_session_factory()
    async with session_factory() as session:
        await ACTIONS[args.action](session, args)


def main() -> None:
    ap = argparse.ArgumentParser(description="Triage / replay / write-off the core.outbox dead-letter backlog.")
    sub = ap.add_subparsers(dest="action", required=True)

    p_tri = sub.add_parser("triage", help="Read-only backlog breakdown + replayability.")
    p_tri.set_defaults(action="triage")

    p_rep = sub.add_parser("replay", help="Re-deliver dead-letter rows through the relay (dry-run default).")
    p_rep.add_argument("--limit", type=int, default=200, help="Max rows per batch (default 200).")
    p_rep.add_argument("--event-type", default=None, help="Restrict to one event_type.")
    p_rep.add_argument("--scheme-id", default=None, help="Restrict to one scheme_id (UUID).")
    p_rep.add_argument("--apply", action="store_true", help="Actually deliver (default: dry-run).")
    p_rep.set_defaults(action="replay")

    p_wo = sub.add_parser("write-off", help="Mark undeliverable rows written-off (dry-run default).")
    p_wo.add_argument("--reason", default="", help="Required: audit reason.")
    p_wo.add_argument("--unresolvable", action="store_true",
                      help="Target rows with no resolvable building (scheme_id NULL or no scheme row).")
    p_wo.add_argument("--event-type", default=None, help="Restrict to one event_type.")
    p_wo.add_argument("--scheme-id", default=None, help="Restrict to one scheme_id (UUID).")
    p_wo.add_argument("--mark-published", action="store_true",
                      help="Also set published_at so they leave the backlog metric (they were NOT "
                           "delivered; the marker records the conscious discard).")
    p_wo.add_argument("--apply", action="store_true", help="Actually write (default: dry-run).")
    p_wo.set_defaults(action="write-off")

    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
