"""
Shadow read monitoring dashboard — reports on parity validation progress.

Usage:
    # Check divergence report
    python -m backend.utils.shadow_read_monitor --report
    
    # Check parity status for a building
    python -m backend.utils.shadow_read_monitor --status building_id
    
    # Clear old divergences (keep last 7 days)
    python -m backend.utils.shadow_read_monitor --cleanup
"""

import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import click

from db_postgres.engine import get_engine
from db_postgres.session import make_session_factory
from sqlalchemy import text


async def get_divergence_report(days: int = 7) -> dict:
    """Generate divergence report for the last N days."""
    engine = get_engine()
    session_factory = make_session_factory(engine)

    async with session_factory() as session:
        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=days)

        # Query divergence statistics
        result = await session.execute(
            text(
                """
                SELECT COUNT(*)                                    as total_divergences,
                       COUNT(DISTINCT query_type)                  as unique_query_types,
                       COUNT(DISTINCT tenant_id)                   as unique_tenants,
                       COUNT(DISTINCT scheme_id)                   as unique_schemes,
                       MIN(detected_at)                            as earliest_divergence,
                       MAX(detected_at)                            as latest_divergence,
                       COUNT(CASE WHEN resolved = true THEN 1 END) as resolved_count
                FROM core.shadow_read_divergences
                WHERE detected_at >= :cutoff
                """
            ),
            {"cutoff": cutoff_date},
        )

        row = result.first()

        # Query divergences by query type
        result2 = await session.execute(
            text(
                """
                SELECT query_type,
                       COUNT(*) as count
                FROM core.shadow_read_divergences
                WHERE detected_at >= :cutoff
                GROUP BY query_type
                ORDER BY count DESC
                """
            ),
            {"cutoff": cutoff_date},
        )

        divergences_by_type = {r[0]: r[1] for r in result2.fetchall()}

        return {
            "period_days": days,
            "cutoff_date": cutoff_date.isoformat(),
            "total_divergences": row[0] or 0,
            "unique_query_types": row[1] or 0,
            "unique_tenants": row[2] or 0,
            "unique_schemes": row[3] or 0,
            "earliest_divergence": row[4].isoformat() if row[4] else None,
            "latest_divergence": row[5].isoformat() if row[5] else None,
            "resolved_count": row[6] or 0,
            "divergences_by_type": divergences_by_type,
        }


async def get_parity_status(scheme_id: str) -> dict:
    """Get parity status for a specific scheme."""
    engine = get_engine()
    session_factory = make_session_factory(engine)

    async with session_factory() as session:
        # Get divergences for this scheme in last 7 days
        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=7)

        result = await session.execute(
            text(
                """
                SELECT COUNT(*)                                     as divergence_count,
                       COUNT(CASE WHEN resolved = true THEN 1 END)  as resolved_count,
                       COUNT(CASE WHEN resolved = false THEN 1 END) as unresolved_count
                FROM core.shadow_read_divergences
                WHERE scheme_id = CAST(:scheme_id as UUID)
                  AND detected_at >= :cutoff
                """
            ),
            {"scheme_id": scheme_id, "cutoff": cutoff_date},
        )

        row = result.first()

        parity_status = "READY" if (row[0] or 0) == 0 else "VALIDATING"

        return {
            "scheme_id": scheme_id,
            "parity_status": parity_status,
            "divergence_count": row[0] or 0,
            "resolved_count": row[1] or 0,
            "unresolved_count": row[2] or 0,
            "validation_period_days": 7,
        }


async def cleanup_old_divergences(days_to_keep: int = 7) -> int:
    """Delete divergences older than N days."""
    engine = get_engine()
    session_factory = make_session_factory(engine)

    async with session_factory() as session:
        cutoff_date = datetime.now(tz=timezone.utc) - timedelta(days=days_to_keep)

        result = await session.execute(
            text(
                """
                DELETE
                FROM core.shadow_read_divergences
                WHERE detected_at < :cutoff RETURNING divergence_id
                """
            ),
            {"cutoff": cutoff_date},
        )

        deleted_count = result.rowcount
        await session.commit()

        return deleted_count


@click.group()
def cli():
    """Shadow read monitoring utilities."""
    pass


@cli.command()
@click.option("--days", default=7, help="Days of data to report on")
def report(days: int):
    """Generate divergence report."""
    click.echo(f"Generating divergence report for last {days} days...\n")

    result = asyncio.run(get_divergence_report(days))

    if result["total_divergences"] == 0:
        click.secho(
            f"✓ No divergences in last {days} days — Postgres shadow reads match MongoDB",
            fg="green",
        )
    else:
        click.secho(
            f"⚠ {result['total_divergences']} divergences found",
            fg="yellow",
        )

    click.echo(f"\nReport:")
    click.echo(f"  Period: {result['cutoff_date']} to now ({days} days)")
    click.echo(f"  Total divergences: {result['total_divergences']}")
    click.echo(f"  Resolved: {result['resolved_count']}")
    click.echo(f"  Unique query types: {result['unique_query_types']}")
    click.echo(f"  Unique schemes: {result['unique_schemes']}")
    click.echo(f"  Unique tenants: {result['unique_tenants']}")

    if result["divergences_by_type"]:
        click.echo(f"\n  Divergences by query type:")
        for qtype, count in sorted(result["divergences_by_type"].items(), key=lambda x: x[1], reverse=True):
            click.echo(f"    - {qtype}: {count}")

    if result["earliest_divergence"]:
        click.echo(f"\n  Timeline:")
        click.echo(f"    Earliest: {result['earliest_divergence']}")
        click.echo(f"    Latest: {result['latest_divergence']}")


@cli.command()
@click.argument("scheme_id")
def status(scheme_id: str):
    """Check parity status for a scheme."""
    click.echo(f"Checking parity status for scheme {scheme_id}...\n")

    result = asyncio.run(get_parity_status(scheme_id))

    if result["parity_status"] == "READY":
        click.secho("✓ READY FOR CUTOVER", fg="green", bold=True)
        click.echo(f"  No divergences in last 7 days")
    else:
        click.secho("⚠ VALIDATING", fg="yellow", bold=True)
        click.echo(f"  {result['divergence_count']} divergences found")
        click.echo(f"  {result['resolved_count']} resolved, {result['unresolved_count']} unresolved")


@cli.command()
@click.option("--days", default=7, help="Days of data to keep")
@click.confirmation_option(prompt="Are you sure you want to delete old divergences?")
def cleanup(days: int):
    """Clean up old divergence records."""
    click.echo(f"Cleaning up divergences older than {days} days...\n")

    deleted_count = asyncio.run(cleanup_old_divergences(days))

    click.secho(f"✓ Deleted {deleted_count} divergence records", fg="green")


if __name__ == "__main__":
    cli()
