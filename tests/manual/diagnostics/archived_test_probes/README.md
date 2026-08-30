# Archived Test Probes

These files were previously named like automated tests under `scripts/`, but they are live-service diagnostics rather than pytest suites. Several of them print tokens, query local production-style Mongo ports, or depend on operator-specific state.

Keep them out of pytest discovery by leaving them outside the top-level `tests/` tree. If one becomes required automated coverage, move the behavior into `tests/scripts/` with mocks or explicit skip guards for live dependencies.
