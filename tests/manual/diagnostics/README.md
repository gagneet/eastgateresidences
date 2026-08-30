# Manual Diagnostics

These scripts are intentionally outside `tests/` so pytest does not count them
as skipped automated tests. Run them only after the automated suite completes,
when a human can approve any live side effects and inspect the output.

Recommended post-suite checks:

```bash
backend/venv/bin/python3 manual/diagnostics/smtp_connection_diagnostic.py
backend/venv/bin/python3 manual/diagnostics/chairman_dashboard_diagnostic.py
```

`smtp_connection_diagnostic.py` connects to the configured SMTP provider and can
send a real email. `chairman_dashboard_diagnostic.py` reads live account and
dashboard data and can print sensitive operational details.
