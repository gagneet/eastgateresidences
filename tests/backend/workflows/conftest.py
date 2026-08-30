"""
conftest.py for workflows/ tests.

Temporal workflow tests need the Temporal time-skipping test server, which is
downloaded from https://temporal.download on first use. In offline CI/CD
environments this download fails.

Add TEMPORAL_AVAILABLE=1 to your environment when the test server binary is
pre-cached or network access is available.
"""
import os
import pytest

_TEMPORAL_AVAILABLE = os.getenv("TEMPORAL_AVAILABLE") == "1"


@pytest.fixture(autouse=True)
def skip_without_temporal():
    if not _TEMPORAL_AVAILABLE:
        pytest.skip(
            "Temporal test server not available — set TEMPORAL_AVAILABLE=1 when the binary is cached"
        )
