# StrataOS Strata Management – Backend

FastAPI application for Strata Management.

## Directory Structure

- `models/`: Pydantic models for data validation and API responses.
- `routers/`: FastAPI routers for different API endpoints.
- `utils/`: Helper functions, permission logic, and shared utilities.
- `seeds/`: Data seeding scripts and default values.
- `cron/`: Background tasks and scheduled scripts.
- `server.py`: Main entry point for the FastAPI application and API definitions.
- `config.py`: Application configuration and environment variable loading.
- `database.py`: MongoDB connection and database initialization.
- `requirements.txt`: Python dependencies.

## Setup and Development

See the root `README.md` for full installation and setup instructions.

### Running the Server

```bash
cd backend
# With uvicorn
uvicorn server:app --reload
```

### Reorganization Note

Utility scripts and tests previously in this directory have been moved to the root `/scripts/backend/` and
`/tests/backend/` directories to keep the core application code clean and focused on feature functionality.
