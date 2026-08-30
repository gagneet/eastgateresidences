# Lighthouse UI Performance Harness

k6 measures API throughput/latency; it cannot measure what a **browser** does —
render, hydration, layout shift, JS bundle cost. This harness runs
[Lighthouse](https://developer.chrome.com/docs/lighthouse) against a list of
StrataOS pages and checks the real user-facing metrics against per-page budgets.

It is the UI-side complement to `tests/performance/*_benchmark.ts` (API) and
`tests/performance/harness/cron_worker_timing.py` (background jobs).

## Install (isolated — does not touch the frontend lockfile)

```bash
cd tests/performance/ui
npm install
```

## Run

```bash
# Preview the route list + budgets — no deps or Chrome needed:
node tests/performance/ui/lighthouse_runner.mjs --list

# Public pages only (frontend running on :3000):
UI_BASE_URL=http://localhost:3000 node tests/performance/ui/lighthouse_runner.mjs

# Include authenticated pages — supply a session cookie copied from the browser
# (DevTools → Application → Cookies → the NextAuth session-token cookie):
UI_BASE_URL=http://localhost:3000 \
  COOKIE='next-auth.session-token=<value>' \
  node tests/performance/ui/lighthouse_runner.mjs
```

Auth pages are **skipped** (not failed) when `COOKIE` is unset, so the public
run always works. The frontend dev server is `:3000`; a production build serves
on `:3020` — set `UI_BASE_URL` accordingly.

## Environment

| Var | Purpose |
|---|---|
| `UI_BASE_URL` | Frontend base URL (default `http://localhost:3000`). |
| `COOKIE` | `Cookie` header for authenticated routes; auth routes skipped if unset. |
| `FORM_FACTOR` | `desktop` (default) or `mobile`. |
| `CHROME_PATH` | Explicit Chrome/Chromium binary. Otherwise the harness finds the Playwright chromium (`PLAYWRIGHT_BROWSERS_PATH`) or lets chrome-launcher auto-detect. |
| `OUT_DIR` | Report output dir (default `docs/performance/ui_lighthouse_<date>/`). |

## Configure

- **`routes.json`** — the pages to audit: `{ name, path, auth }`. `auth: true`
  pages need `COOKIE`.
- **`budgets.json`** — per-page budgets (`default` + overrides keyed by route
  `name`). Every metric is lower-is-better except `performance_score` (0..1,
  higher-is-better). **Tune these to your first clean baseline before gating in
  CI** — start loose, tighten over time.

## Output

Writes `docs/performance/ui_lighthouse_<date>/`:

```
ui_lighthouse_report_<date>.json   # machine-readable: per-page metrics + breaches
ui_lighthouse_report_<date>.md     # human summary table + breach list
<page>.lhr.json                    # raw Lighthouse result per page (full audit)
```

Exit code: `0` = every measured page within budget; `1` = a budget breach or a
page errored; `2` = setup error (missing deps, no routes). CI can gate on it.

## Metrics checked

Performance score, Largest Contentful Paint (LCP), Total Blocking Time (TBT),
Cumulative Layout Shift (CLS), First Contentful Paint (FCP), Time to Interactive
(TTI), Speed Index, and total byte weight.

## Notes

- **Auth via storageState (future):** cookie-string auth is the simplest path; a
  Playwright `storageState` login flow could automate token capture later.
- Run against a **production build** (`yarn build && yarn start`) for
  representative numbers — dev-server bundles are unminified and much heavier.
