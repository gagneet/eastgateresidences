from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/migrations/postgres/framework/checkpoints.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CheckpointStore:
    path: Path
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Generated function header.

        Function: CheckpointStore.__post_init__
        Path: backend/migrations/postgres/framework/checkpoints.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.payload = json.loads(self.path.read_text())
        else:
            self.payload = {"runs": []}

    def start_run(self, *, run_id: str, script: str, building_id: str, dry_run: bool) -> None:
        """Generated function header.

        Function: CheckpointStore.start_run
        Path: backend/migrations/postgres/framework/checkpoints.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.payload.setdefault("runs", []).append(
            {
                "run_id": run_id,
                "script": script,
                "building_id": building_id,
                "dry_run": dry_run,
                "started_at": _now_iso(),
                "phases": [],
            }
        )
        self._save()

    def mark_phase(
        self,
        *,
        run_id: str,
        phase: str,
        batch_index: int,
        status: str,
        last_source_token: str | None,
        stats: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Generated function header.

        Function: CheckpointStore.mark_phase
        Path: backend/migrations/postgres/framework/checkpoints.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        run = self._latest_run(run_id)
        run.setdefault("phases", []).append(
            {
                "phase": phase,
                "batch_index": batch_index,
                "status": status,
                "last_source_token": last_source_token,
                "stats": stats or {},
                "error": error,
                "recorded_at": _now_iso(),
            }
        )
        self._save()

    def finish_run(self, *, run_id: str, status: str) -> None:
        """Generated function header.

        Function: CheckpointStore.finish_run
        Path: backend/migrations/postgres/framework/checkpoints.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        run = self._latest_run(run_id)
        run["status"] = status
        run["finished_at"] = _now_iso()
        self._save()

    def _latest_run(self, run_id: str) -> dict[str, Any]:
        """Generated function header.

        Function: CheckpointStore._latest_run
        Path: backend/migrations/postgres/framework/checkpoints.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        for run in reversed(self.payload.get("runs", [])):
            if run.get("run_id") == run_id:
                return run
        raise KeyError(f"Unknown checkpoint run_id={run_id}")

    def _save(self) -> None:
        """Generated function header.

        Function: CheckpointStore._save
        Path: backend/migrations/postgres/framework/checkpoints.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.path.write_text(json.dumps(self.payload, indent=2, sort_keys=True) + "\n")
