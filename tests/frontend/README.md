# Playwright End-to-End Tests

## Overview

This directory contains Playwright tests for the East Gate Residences application. These tests ensure:

1. **No Console Errors** - Pages load without JavaScript errors
2. **Graceful Error Handling** - Missing data (404s) handled properly
3. **Build Quality** - No React Hook warnings, proper memoization
4. **Regression Prevention** - Previously fixed issues don't reoccur

## Test Suites

### 1. Console Errors Detection (`console-errors.spec.js`)

Tests that pages load without console errors:

- **Public Pages**: Homepage, Login, About, Blog, Marketplace
- **Authenticated Pages**: Dashboard, Documents, Chat, Compliance, Events, Finance
- **Filters Out**: Browser extensions, third-party scripts (Cloudflare, Stripe)

**Purpose**: Catch JavaScript errors that could break functionality

### 2. Financial Data Handling (`financial-data-handling.spec.js`)

Tests graceful handling of missing financial data:

- **200 OK**: Shows financial summary when data available
- **404 Not Found**: Shows user-friendly message instead of errors
- **500 Server Error**: Shows error state with instructions
- **UI Behavior**: No "Loading..." stuck states, appropriate messages

**Purpose**: Verify fix for `/api/owners-units/{unit}` 404 errors from Session 13

### 3. Build Validation (`build-validation.spec.js`)

Tests build quality and code patterns:

- **No React Hook Warnings**: Verifies Session 12 fix persists
- **Bundle Size**: Checks production bundle is reasonable
- **Code Patterns**: Validates useCallback, useMemo usage
- **Regression Prevention**: Checks for common anti-patterns

**Purpose**: Ensure build quality and prevent regression of React Hooks fix

### 4. Recent Bug Fix Regression Tests (`e2e/recent-fixes.spec.js`) _(Added 2026-02-18)_

Regression tests for all 7 recent fixes:

- **compliance_items** — optional fields handled, page loads without 500
- **listings** — null title guard, price as string for scraped listings
- **legal_pages** — GET/PUT endpoints work
- **budgets** — 3 financial years, no server errors
- **chat_groups** — groups load including General Chat
- **feature_toggle access summary** — UUID user ID, invalid ID handled
- **budget_categories deleted** — budget API still works after Phase 2 cleanup

**Purpose**: Prevent regression of all recent fixes after Phase 2 DB cleanup

### 5. Events Calendar Tests (`e2e/calendar.spec.js`) _(Added 2026-02-18)_

Tests for the calendar/events feature:

- **Calendar page loads** — authenticated user can view events
- **Events API** — returns list with correct structure
- **AGM event** — appears for chairman (with updated 18:00 time)
- **Navigation controls** — month navigation works
- **Add event button** — visible for admin/manager
- **No JS errors** — page renders without uncaught exceptions

**Purpose**: Verify calendar feature works end-to-end after recent backend fixes

### 9. Impersonation & Custom Levy Dates Tests (`e2e/impersonation-and-custom-dates.spec.js`) _(Added 2026-03-01 — PRs

#128 & #131)_

E2E tests for the Super Admin impersonation feature and custom levy due dates:

**Impersonation API Tests**:

- `POST /api/auth/impersonate` returns a JWT token for super_admin
- Non-super_admin (owner) gets 403 on impersonate endpoint
- Impersonation token masks PII in `/api/auth/me` response (email `j***@example.com`)
- Impersonating a super_admin account is rejected (400/403)

**ImpersonationBanner UI Tests**:

- Banner not visible on normal super_admin or owner login
- Amber banner with "Exit Impersonation" appears when impersonation token injected

**ImpersonateModal UI Tests**:

- super_admin sees "Impersonate User" option in account dropdown
- owner does NOT see "Impersonate User" option

**Custom Levy Due Dates Tests**:

- Settings page loads and shows Financials tab
- API accepts valid custom dates `{3: 31, 6: 30, 9: 30, 12: 31}`
- API rejects month key 13 → 422 Unprocessable Entity
- API rejects day value 32 → 422 Unprocessable Entity
- "last" day strategy returns valid ISO date strings in levy quarters

**Analytics Security Tests (PR #131)**:

- `/api/analytics/expense-breakdown` returns 200 for super_admin / chairman
- All analytics endpoints require authentication (→ 401/403 for anonymous)
- Activities endpoint does not leak private titles for unauthenticated requests

**Document Security Tests**:

- Anonymous `/api/documents` returns only public documents
- Authenticated owner can list documents

**Frontend Build Integrity**:

- Dashboard loads without critical console errors for super_admin
- ImpersonationBanner not rendered for normal sessions
- Settings page accessible for super_admin

**Run:**

```bash
cd /home/gagneet/strata-management
npx playwright test tests/frontend/e2e/impersonation-and-custom-dates.spec.js --headed
```

**Purpose**: End-to-end coverage for the impersonation security feature (PII masking, token validation, UI banner/modal)
and custom levy due date configuration with Pydantic validation.

### 6. Rental Certificates Tests (`e2e/rental-certificates.spec.js`) _(Added 2026-02-27)_

### 10. Owner Hub API Tests (`owner_hub_api.spec.js`) _(Added 2026-03-12 — Session 61)_

Covers the production 405/404/422 bug fixes for the Owner Hub pages:

**Endpoint Existence Checks (unauthenticated)**:

- `POST /owner-hub/properties/{id}/tenancy` returns 401/403 (not 405) — endpoint now exists
- `GET /owner-hub/properties/{id}/ledger` returns 401/403 (not 404) — convenience ledger endpoint added
- `GET /owner-hub/inspections` returns 401/403 (not 404) — aggregate inspections endpoint added
- `POST /owner-hub/properties/{id}/tco` returns 401/403 (not 422) — model fields now optional

**Run:**

```bash
cd /home/gagneet/strata-management
npx playwright test tests/frontend/owner_hub_api.spec.js --headed
```

**Purpose**: Regression guard for Owner Hub routing bugs — ensures endpoints exist and are accessible (auth-gated)
without triggering incorrect HTTP status codes.

E2E tests for ACT s.119A Rental Certificates compliance feature:

**API Tests** (no browser):

- `GET /api/rental-certificates` returns 401 without auth
- `GET /api/rental-certificates` returns 200 for admin
- `GET /api/rental-certificates` returns 200 for owner (own unit filtered)
- `GET /api/rental-certificates` returns 403 for tenant
- `POST /api/rental-certificates` returns 403 for tenant
- `POST /api/rental-certificates` creates cert for owner (own unit)
- `GET /api/rental-certificates/unit/{unit}/current` works for admin

**UI Tests — Owner**:

- Page loads with correct title
- Compliance banner ("Mandatory since 9 January 2025") is visible
- "Request Certificate" button visible for owner
- Request dialog opens on button click
- Dialog requires tenant name (form validation)

**UI Tests — Admin**:

- "Manage Requests" tab visible for admin
- Stats cards show numeric values
- "Section 119A" badge visible on page

**Navigation Tests**:

- Rental Certificates link visible in sidebar (owner and admin)

**Run:**

```bash
cd /home/gagneet/strata-management
npx playwright test tests/frontend/e2e/rental-certificates.spec.js --headed
```

**Purpose**: Verify full ACT s.119A compliance workflow end-to-end — API permissions, UI access controls, navigation
integration

---

## Unit Tests (Jest + React Testing Library)

Located in `tests/frontend/unit/`. Run with `yarn test` from the `frontend/` directory.

### Intelligence Pages (`tests/frontend/unit/pages/dashboard/`)

#### `CapitalRiskPage.test.tsx` _(Added 2026-03-09 — Session 58)_

5 tests for the Capital Shock Risk analysis page:

- Loading skeleton renders while API is pending
- Capital risk data renders after load (MODERATE badge, CSI/SFAR cards)
- Reserve balance formatted with exactly 2 decimal places (`$250,000.00`)
- Recommendation text is displayed
- Error state shown when API fails

#### `IntelligenceAssetsPage.test.tsx` _(Added 2026-03-09 — Session 58)_

9 tests for the Asset Intelligence page:

- Loading skeleton renders
- Back navigation button present
- Page heading + description visible
- Maintenance anomaly table populated from API
- Repair cost formatted with exactly 2dp (`$8,500.50`) — verifies shared `fmtAUD()` used
- Asset Health Snapshot table populated
- "Alert"/"OK" status badges shown correctly
- Empty state shown when no anomalies
- API failure handled gracefully (no crash)

```bash
cd frontend
yarn test --testPathPatterns=../tests/frontend/unit/pages/dashboard/IntelligenceAssetsPage.test.tsx --watchAll=false
yarn test --testPathPatterns=../tests/frontend/unit/pages/dashboard/CapitalRiskPage.test.tsx --watchAll=false
```

## Running Tests

### Prerequisites

**IMPORTANT**: Frontend tests use Node.js and Playwright (NOT Python). Make sure you have Node.js 18+ installed.

```bash
# Navigate to project root
cd /home/gagneet/strata-management

# Install Playwright test dependencies
yarn add -D @playwright/test

# Install Chromium browser for Playwright
npx playwright install chromium

# Or install all browsers (chromium, firefox, webkit)
npx playwright install
```

**Note**: These commands should be run from the **project root**, not from the `tests/frontend/` directory.

### Run All Tests

**IMPORTANT**: All Playwright commands must be run from the **project root** (not from `tests/frontend/` directory).

```bash
# From project root
cd /home/gagneet/strata-management
npx playwright test

# With UI mode (recommended for debugging)
npx playwright test --ui

# Watch mode (re-run on file changes)
npx playwright test --watch

# Verbose output
npx playwright test --reporter=list
```

### Run Specific Test Suite

```bash
# Console errors only
npx playwright test console-errors

# Financial data handling only
npx playwright test financial-data-handling

# Build validation only
npx playwright test build-validation
```

### Run Tests in Headed Mode

```bash
# See browser during test execution
npx playwright test --headed

# Debug mode (step through tests)
npx playwright test --debug
```

### Generate Test Report

```bash
# Run tests and generate HTML report
npx playwright test --reporter=html

# View report
npx playwright show-report
```

## Test Configuration

Configuration is in `playwright.config.js` at project root:

- **Base URL**: `https://eastgateresidences.com.au` (or `BASE_URL` env var)
- **Browsers**: Chromium (can add Firefox, WebKit if needed)
- **Retries**: 0 locally, 2 on CI
- **Screenshots**: On failure only
- **Videos**: On failure only
- **Traces**: On first retry

### Environment Variables

```bash
# Test against local development
BASE_URL=http://localhost:3000 npx playwright test

# Test against production
BASE_URL=https://eastgateresidences.com.au npx playwright test
```

## CI/CD Integration

Add to your CI pipeline:

```yaml
# GitHub Actions example
- name: Install Playwright
  run: |
    yarn add -D @playwright/test
    npx playwright install --with-deps chromium

- name: Run Playwright tests
  run: npx playwright test
  env:
    BASE_URL: https://eastgateresidences.com.au

- name: Upload test results
  if: always()
  uses: actions/upload-artifact@v3
  with:
    name: playwright-report
    path: playwright-report/
```

## Writing New Tests

### Test Structure

```javascript
// @ts-check
const { test, expect } = require('@playwright/test');

test.describe('My Feature', () => {
  test.beforeEach(async ({ page }) => {
    // Setup (e.g., login)
    await page.goto('/login');
    // ...
  });

  test('should do something', async ({ page }) => {
    await page.goto('/my-page');

    // Arrange
    const button = page.locator('button:has-text("Click Me")');

    // Act
    await button.click();

    // Assert
    await expect(page.locator('.result')).toHaveText('Success');
  });
});
```

### Best Practices

1. **Use data-testid attributes** for stable selectors
2. **Wait for network idle** before assertions
3. **Mock API responses** for consistent test data
4. **Filter out third-party errors** (extensions, CDNs)
5. **Test user flows**, not implementation details

### Common Patterns

```javascript
// Wait for page load
await page.waitForLoadState('networkidle');

// Wait for specific element
await page.waitForSelector('[data-testid="my-element"]');

// Mock API response
await page.route('**/api/data', route => {
  route.fulfill({
    status: 200,
    body: JSON.stringify({ data: 'mock' })
  });
});

// Check console errors
const errors = [];
page.on('console', msg => {
  if (msg.type() === 'error') errors.push(msg.text());
});
// ... later
expect(errors).toHaveLength(0);

// Login helper
async function login(page, email, password) {
  await page.goto('/login');
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL('/dashboard');
}
```

## Debugging Failed Tests

### 1. View Screenshot

```bash
# Screenshots saved in test-results/ directory
ls test-results/
```

### 2. View Video Recording

Videos are saved on failure in `test-results/`

### 3. View Trace

```bash
# View trace for failed test
npx playwright show-trace test-results/.../trace.zip
```

### 4. Debug Mode

```bash
# Step through test line-by-line
npx playwright test --debug my-test.spec.js
```

### 5. Console Logs

Playwright captures all console output. Check test output:

```bash
npx playwright test --reporter=list
```

## Maintenance

### Update Snapshots

If visual tests fail due to intentional UI changes:

```bash
npx playwright test --update-snapshots
```

### Update Playwright

```bash
yarn upgrade @playwright/test
npx playwright install chromium
```

### Clean Test Artifacts

```bash
# Remove test results and reports
rm -rf test-results playwright-report
```

## Troubleshooting

### "npx: command not found"

**Solution**: Install Node.js 18+ from https://nodejs.org or use nvm:

```bash
# Using nvm (recommended)
nvm install 18
nvm use 18

# Verify installation
node --version  # Should show v18.x.x or higher
npm --version   # Should show 9.x.x or higher
```

### "Cannot find module '@playwright/test'"

**Solution**: Make sure you're in the project root and have installed dependencies:

```bash
cd /home/gagneet/strata-management
yarn install
yarn add -D @playwright/test
```

### "Executable doesn't exist" or Browser Not Found

**Solution**: Install Playwright browsers:

```bash
npx playwright install chromium
# Or install all browsers
npx playwright install
```

### Tests Fail Locally But Pass on CI

- **Browser versions**: Update Playwright browsers
- **Timing**: Increase timeout or add explicit waits
- **Screen size**: CI uses default, you might have different viewport

### "Target closed" Errors

- Page crashed or navigated before action completed
- Add `waitForLoadState('networkidle')` before actions
- Check for JavaScript errors causing page crash

### Flaky Tests

- Use `test.retry(2)` for flaky tests
- Add explicit waits instead of `setTimeout`
- Mock network requests for consistency

### Authentication Issues

- Check credentials in test match database
- Verify token expiration isn't causing issues
- Clear cookies between tests if needed

### Running Tests from Wrong Directory

**Error**: Tests don't run or can't find configuration file.

**Solution**: Always run Playwright commands from the **project root**:

```bash
# Wrong (from tests/frontend/)
cd tests/frontend
npx playwright test  # This will fail!

# Correct (from project root)
cd /home/gagneet/strata-management
npx playwright test  # This works!
```

## Resources

- [Playwright Documentation](https://playwright.dev)
- [Best Practices](https://playwright.dev/docs/best-practices)
- [Debugging Guide](https://playwright.dev/docs/debug)
- [CI/CD Integration](https://playwright.dev/docs/ci)

## Contributing

When adding features:

1. Add corresponding tests
2. Run tests locally before committing
3. Update this README if adding new patterns

### 7. Capital Works / Sinking Fund Tests _(Added 2026-02-28)_

API smoke tests for the sinking fund capital works endpoints:

- `GET /api/finance/sinking-fund-plan` returns 401 without auth
- `GET /api/finance/sinking-fund-plan` returns 200 for admin with 15 rows
- `GET /api/finance/sinking-fund-plan` includes `is_major_capital_year` flags for 2029/2032/2035
- `GET /api/finance/sinking-fund-projection` returns projection rows with annual_collection≈$106,763
- Capital Works tab visible on `/financials/intelligence` for chairman/admin

**Purpose**: Verify sinking fund plan seeding and projection model are accessible through the API

### 8. useApiData Hook Tests (`tests/frontend/unit/hooks/useApiData.test.ts`) _(Added 2026-02-28, Session 31)_

Jest unit tests for the `useApiData` hook — regression suite for the isMounted race-condition bug fix (Session 31).

**Coverage:**

- `isMounted` cleanup prevents state updates after unmount (regression guard)
- `data` initialises to `[]` (not `null`) so `.map()` is always safe
- `enabled: false` option skips the fetch entirely
- `enabled: true` (default) performs the fetch as expected
- Error handling: network errors surface as `error` state, `data` remains `[]`
- AGM fetch scenarios: attendance list, motion list, results endpoint
- Loading states: `loading=true` during fetch, `loading=false` on complete
- Refetch: calling `refetch()` re-triggers the API call

**Tests:** 24 test cases ✅

**Run:**

```bash
cd frontend && yarn test --testPathPatterns=useApiData --watchAll=false
```

---

## Test Coverage

Current coverage:

- ✅ Console error detection (5 public pages, 3 auth pages)
- ✅ Financial data error handling (404, 500, success cases)
- ✅ Build validation (React Hooks, bundle size)
- ✅ Dashboard navigation (7 pages)
- ✅ Rental certificates workflow (API + UI, 16 tests)
- ✅ Sinking fund capital works API (5 tests)
- ✅ `useApiData` hook — isMounted fix regression + enabled option (24 Jest unit tests)
- ✅ `ForecastChart` — null-safe transform regression (34 Jest unit tests)
- ✅ Payment flow — Stripe amount rounding, admin/sinking split, webhook fallback (31 backend tests in
  `test_payments.py`)
- ✅ Council rates ACT FY + quarterly schedule — `test_property_taxes.py` (72 backend tests)
- ✅ Water bill calendar quarters — `getCurrentWaterQuarter()` vs ACT rates quarter (covered in `test_property_taxes.py`)
- ⏳ AGM Voting UI flow (TODO — Playwright)
- ⏳ Form submissions (TODO)
- ⏳ File uploads (TODO)

---

## Sessions 47–49b — Finance Dashboard Coverage (2026-03-03/04)

### Backend tests verified during these sessions

| Test File                       | Tests | Coverage                                                                    |
|---------------------------------|-------|-----------------------------------------------------------------------------|
| `test_collection_rate.py`       | 103   | Collection rate formula, historical year proxy, per-unit arrears            |
| `test_arrears_board.py`         | 18    | Grace period obligation calc (`opening + periods_past_grace × period_levy`) |
| `test_finance_levy_status.py`   | 36    | Levy status, period due dates, opening arrears vs net_balance               |
| `test_levy_payment_workflow.py` | 28    | Verify/reject/delete, idempotency, asyncio.create_task patch                |

### Key page changes (Sessions 47–49)

The following frontend pages were updated. Playwright coverage is recommended:

| Page / Component                 | Change                                                                                               |
|----------------------------------|------------------------------------------------------------------------------------------------------|
| `CollectionRatePage.jsx`         | `getUnitType()` derives type from unit_number prefix when `property_type` blank                      |
| `CollectionRateDetailDialog.jsx` | Fetches levy ledger per year; sorts good-payer table by `total_paid` desc; "Total Paid" column added |
| `OwnersUnitsPage.jsx`            | Arrears/credit filter uses `opening_arrears` not `net_balance`; Balance Status badge column added    |
| `ManagementDashboard.tsx`        | Current Balance card (5-col grid); Arrears Total popup; Collection Rate → dialog                     |
| `ArrearsRecoveryPage.jsx`        | New page: full recovery board with YoY chart, sortable table, CSV export                             |
| `UnitFinanceDetailPage.jsx`      | New page: 5-tab per-unit view (Overview/Levy History/Payments/Council Rates/Water Bills)             |

### Critical asyncio.create_task pattern (Session 49b)

**Problem**: Tests that mock `routers.finance.db` but don't patch functions called via `asyncio.create_task()` cause
real MongoDB writes after the `@patch` context exits. With session-scoped event loops, tasks from one test run in the
next test's context.

**Impact**: TH017's `unit_levy_ledger.total_paid` was corrupted: $1,800 → $3,000.50.

**Rule for frontend test authors**: If a backend endpoint fires background tasks (`asyncio.create_task`), corresponding
Playwright tests that trigger that endpoint will cause real DB writes. Always verify DB state after Playwright tests
that trigger verify/confirm/payment endpoints.

See: `docs/fixes/session_49b_asyncio_task_leak_fix_2026-03-04.md`

---

**Last Updated**: 2026-03-04 (Session 49b — test consolidation + finance dashboard sessions)
**Test Framework**: Playwright 1.58.2+
**Node Version**: 18+
**Important**: Always run from project root, not from tests/frontend/
