# Backend Cron Jobs

This directory contains scripts intended to be run as scheduled cron jobs.

## Files

- `cron_expiration_check.py`: Checks for expired tenants and guests daily, sending automated warnings and deactivating
  accounts when necessary.
- `cron_notification_cleanup.py`: Cleans up old in-app notifications based on retention settings.
- `run_expiration_check.sh`: Shell wrapper created by `setup_cron.sh` to run the expiration check within the virtual
  environment.

## Setup

To set up these cron jobs on a server, use the setup script:

```bash
bash scripts/backend/deployment/setup_cron.sh
```

This will add the necessary entries to the system crontab.
