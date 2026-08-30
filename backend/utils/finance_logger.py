"""
Finance Event Logger — structured JSON observability for financial intelligence operations.

Usage:
    from utils.finance_logger import finance_log, log_event, timed

    # Simple event
    log_event("forecast_generation", year="2026", count=30)

    # Timed block
    with timed("anomaly_scan", year="2026") as t:
        result = await detect_financial_anomalies(year)
        t.extra["count"] = len(result)

Performance warning thresholds (ms):
    /finance/health   > 100ms
    /finance/forecast > 200ms
    /finance/anomalies> 200ms
"""
import json
import time
from datetime import datetime, timezone

import logging
from typing import Any

# Named logger — configure in server.py or let basicConfig handle it
finance_log = logging.getLogger("finance_events")

# Performance warning thresholds in milliseconds
_WARN_THRESHOLDS_MS: dict[str, float] = {
    "health_compute": 100.0,
    "forecast_generation": 200.0,
    "anomaly_scan": 200.0,
    "report_generated": 5000.0,
}


def log_event(event_type: str, **kwargs: Any) -> None:
    """Emit a structured JSON finance event to the finance_events logger."""
    payload = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **kwargs,
    }
    finance_log.info(json.dumps(payload))


class timed:
    """
    Context manager that measures elapsed time and emits a structured log event on exit.

    Example::

        with timed("forecast_generation", year="2026") as t:
            forecasts = await generate_category_forecast(year)
            t.extra["count"] = len(forecasts)
    """

    def __init__(self, event: str, **ctx: Any) -> None:
        """Generated function header.

        Function: timed.__init__
        Path: backend/utils/finance_logger.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.event = event
        self.ctx = ctx
        self.extra: dict[str, Any] = {}
        self._start: float = 0.0

    def __enter__(self) -> "timed":
        """Generated function header.

        Function: timed.__enter__
        Path: backend/utils/finance_logger.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        """Generated function header.

        Function: timed.__exit__
        Path: backend/utils/finance_logger.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        payload: dict[str, Any] = {
            "duration_ms": round(elapsed_ms, 2),
            **self.ctx,
            **self.extra,
        }
        log_event(self.event, **payload)

        # Warn if threshold exceeded
        threshold = _WARN_THRESHOLDS_MS.get(self.event)
        if threshold and elapsed_ms > threshold:
            finance_log.warning(
                json.dumps({
                    "event": f"{self.event}_slow",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "duration_ms": round(elapsed_ms, 2),
                    "threshold_ms": threshold,
                    **self.ctx,
                })
            )
