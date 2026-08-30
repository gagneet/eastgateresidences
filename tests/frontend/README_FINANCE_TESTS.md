# Frontend E2E Tests - Finance Year Dropdown

Playwright end-to-end tests for the Finance Page year dropdown feature.

## Test Files

### `test_finance_year_dropdown.spec.js`

Complete E2E test suite for finance page year selection.

**Coverage:**

- Year dropdown displays 3 years (2024-2025, 2025-2026, 2026-2027)
- Year selection loads correct data
- Levy calculator functionality for 2026-2027
- All 87 units display correctly
- Year switching without errors
- Budget breakdown display
- API endpoint validation

**Tests:** 17 test cases

## Running Tests

### Prerequisites

```bash
# Install Playwright if not already installed
npm install -D @playwright/test
npx playwright install
```

### Run Finance Tests

```bash
# From project root
npx playwright test tests/test_finance_year_dropdown.spec.js
```

### Run with UI (Headed Mode)

```bash
npx playwright test tests/test_finance_year_dropdown.spec.js --headed
```

### Run with Debug Mode

```bash
npx playwright test tests/test_finance_year_dropdown.spec.js --debug
```

### Run Specific Test

```bash
npx playwright test tests/test_finance_year_dropdown.spec.js -g "should display year dropdown with 3 years"
```

### Generate HTML Report

```bash
npx playwright test tests/test_finance_year_dropdown.spec.js --reporter=html
npx playwright show-report
```

## Test Configuration

### Environment Variables

Tests read from environment or use defaults:

- `REACT_APP_BACKEND_URL`: Backend API URL (default: https://eastgateresidences.com.au)

### Test User Credentials

Tests use admin account:

- Email: `admin@eastgate.com`
- Password: `$SEED_TEST_USER_PASSWORD`

Ensure this user exists in the database.

## Expected Results

All tests should pass:

```
✅ 17/17 tests passing
✅ Year dropdown shows 3 years
✅ Data loads for each year
✅ Levy calculator displays 87 units
✅ API endpoint returns correct data
```

## Test Scenarios

### 1. Year Dropdown Display

- Verifies 3 years are available
- Checks year format (YYYY-YYYY)
- Validates disabled years show "(No Data)"

### 2. Year Selection

- Selects 2026-2027 year
- Verifies data loads correctly
- Checks budget totals appear

### 3. Levy Calculator

- Clicks Levy Calculator tab
- Counts unit rows (should be 87)
- Verifies specific unit levies:
    - UA001: $902.77
    - TH004: $1,761.50

### 4. Budget Breakdown

- Shows admin fund: $340,870.20
- Shows sinking fund: $99,504.90
- Total: $440,375.10

### 5. Year Switching

- Switches between years
- No console errors
- Data updates correctly

### 6. API Validation

- Calls `/api/finance/years`
- Returns 3 years
- Authenticated with JWT token

## Troubleshooting

### Backend Not Running

```bash
# Check backend status
sudo systemctl status strataos-backend

# Restart if needed
sudo systemctl restart strataos-backend
```

### Frontend Build Missing

```bash
# Rebuild frontend
cd frontend
yarn build
```

### Test User Not Found

```bash
# Reseed database
cd backend
source venv/bin/activate
python3 seed_database.py
```

### Tests Timing Out

Increase timeout in test file:

```javascript
test.setTimeout(30000); // 30 seconds
```

### Hard Refresh Required

If tests fail due to cached data:

```bash
# Clear browser state
rm -rf tests/playwright/.auth/
```

## Debugging Tests

### Run with Trace

```bash
npx playwright test tests/test_finance_year_dropdown.spec.js --trace on
```

### View Trace

```bash
npx playwright show-trace trace.zip
```

### Screenshots

Failed tests automatically capture:

- Screenshot at failure point
- Full page snapshot
- Network logs

## Continuous Integration

Example CI/CD script:

```bash
#!/bin/bash
set -e

# Start backend
sudo systemctl start strataos-backend
sleep 5

# Run tests
npx playwright test tests/test_finance_year_dropdown.spec.js --reporter=html

# Generate report
npx playwright show-report
```

## Test Maintenance

### Updating Selectors

If UI changes, update selectors in test file:

```javascript
await page.click('[data-testid="year-selector"]'); // Good - testid
await page.click('button.dropdown'); // Avoid - CSS classes
```

### Adding New Tests

1. Add test case to `test_finance_year_dropdown.spec.js`
2. Follow existing patterns
3. Use descriptive test names
4. Update this README

### Test Data

Tests depend on:

- 2026-2027 budget in database
- 87 units with levy data
- Admin user account

Ensure database is seeded before running tests.

## Performance Benchmarks

Expected test duration:

- Single test: ~5-10 seconds
- Full suite: ~3-5 minutes
- With headed mode: ~5-8 minutes

## Related Documentation

- **Playwright Docs**: https://playwright.dev/
- **Finance Feature**: `docs/sessions/session_25_finance_2026_import/`
- **API Tests**: `backend/tests/test_finance_2026_import.py`

---

**Last Updated:** 2026-02-16
**Test Coverage:** Finance Page Year Dropdown (17 tests)
**Status:** ✅ Ready for CI/CD
