from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/migrations/postgres/framework/reporting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MigrationReportWriter:
    script_name: str
    building_id: str
    run_id: str
    output_dir: Path
    dry_run: bool
    started_at: str = field(default_factory=_now_iso)
    table_counts: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    variances: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    unresolved_rows: list[dict[str, Any]] = field(default_factory=list)
    trace_path: Path | None = None
    report_path: Path | None = None

    def __post_init__(self) -> None:
        """Generated function header.

        Function: MigrationReportWriter.__post_init__
        Path: backend/migrations/postgres/framework/reporting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        reports_dir = self.output_dir / "reports"
        traces_dir = self.output_dir / "traces"
        reports_dir.mkdir(parents=True, exist_ok=True)
        traces_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = traces_dir / f"{self.run_id}.ndjson"
        self.report_path = reports_dir / f"{self.run_id}.json"
        self.trace_path.write_text("")

    def record_trace(
        self,
        *,
        status: str,
        target_table: str,
        target_id: str,
        source_collection: str,
        source_id: str,
        phase: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Generated function header.

        Function: MigrationReportWriter.record_trace
        Path: backend/migrations/postgres/framework/reporting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.table_counts[status][target_table] += 1
        payload = {
            "run_id": self.run_id,
            "script": self.script_name,
            "building_id": self.building_id,
            "status": status,
            "phase": phase,
            "source_collection": source_collection,
            "source_id": source_id,
            "target_table": target_table,
            "target_id": target_id,
            "metadata": metadata or {},
            "recorded_at": _now_iso(),
        }
        assert self.trace_path is not None
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def add_variance(
        self,
        *,
        name: str,
        expected_cents: int,
        actual_cents: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Generated function header.

        Function: MigrationReportWriter.add_variance
        Path: backend/migrations/postgres/framework/reporting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.variances.append(
            {
                "name": name,
                "expected_cents": expected_cents,
                "actual_cents": actual_cents,
                "variance_cents": actual_cents - expected_cents,
                "metadata": metadata or {},
            }
        )

    def add_failure(
        self,
        *,
        phase: str,
        source_collection: str,
        source_id: str,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Generated function header.

        Function: MigrationReportWriter.add_failure
        Path: backend/migrations/postgres/framework/reporting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.failures.append(
            {
                "phase": phase,
                "source_collection": source_collection,
                "source_id": source_id,
                "error": error,
                "metadata": metadata or {},
            }
        )
        self.table_counts["failed"][source_collection] += 1

    def add_unresolved(
        self,
        *,
        phase: str,
        source_collection: str,
        source_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Generated function header.

        Function: MigrationReportWriter.add_unresolved
        Path: backend/migrations/postgres/framework/reporting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.unresolved_rows.append(
            {
                "phase": phase,
                "source_collection": source_collection,
                "source_id": source_id,
                "reason": reason,
                "metadata": metadata or {},
            }
        )

    def finalize(self, *, status: str) -> dict[str, Any]:
        """Generated function header.

        Function: MigrationReportWriter.finalize
        Path: backend/migrations/postgres/framework/reporting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        payload = {
            "run_id": self.run_id,
            "script": self.script_name,
            "building_id": self.building_id,
            "dry_run": self.dry_run,
            "status": status,
            "started_at": self.started_at,
            "finished_at": _now_iso(),
            "table_counts": {bucket: dict(values) for bucket, values in self.table_counts.items()},
            "variances": self.variances,
            "failures": self.failures,
            "unresolved_rows": self.unresolved_rows,
            "trace_file": str(self.trace_path),
        }
        assert self.report_path is not None
        self.report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return payload
