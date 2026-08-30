## Strata Management — Developer Shortcuts
##
## Usage: make <target>
##
## Backend tests MUST run with the venv python (fastapi, motor, etc. are
## installed there). System python3 will fail with ModuleNotFoundError.

VENV_PY = backend/venv/bin/python3
FRONTEND_DIR = frontend

.PHONY: test test-backend test-backend-v test-frontend help

## Run all backend tests (uses venv python, from project root)
test-backend:
	$(VENV_PY) -m pytest

## Run all backend tests with verbose output
test-backend-v:
	$(VENV_PY) -m pytest -v

## Run frontend jest tests
test-frontend:
	cd $(FRONTEND_DIR) && yarn test --watchAll=false

## Run both backend and frontend tests
test: test-backend test-frontend

help:
	@echo "Targets:"
	@echo "  test-backend    Run all Python backend tests (uses venv)"
	@echo "  test-backend-v  Run backend tests (verbose)"
	@echo "  test-frontend   Run frontend Jest tests"
	@echo "  test            Run all tests"
