#!/usr/bin/env node
// @featuretrace:dashboard-v2 — Lighthouse UI performance harness.
//
// k6 measures API throughput/latency; it cannot measure what a *browser* does
// (render, hydration, layout shift, bundle cost). This harness runs Lighthouse
// against a list of StrataOS pages and checks the real user-facing metrics —
// performance score, LCP, TBT, CLS, FCP, TTI, Speed Index, total byte weight —
// against per-page budgets, then writes a dated report bundle to
// docs/performance/ui_lighthouse_<date>/.
//
// It is the UI-side complement to tests/performance/*_benchmark.ts (API) and
// tests/performance/harness/cron_worker_timing.py (background jobs).
//
// Deps are isolated in this folder so they do not touch the frontend lockfile:
//     cd tests/performance/ui && npm install
//
// Usage:
//     node tests/performance/ui/lighthouse_runner.mjs --list        # no deps/Chrome needed
//     UI_BASE_URL=http://localhost:3000 node tests/performance/ui/lighthouse_runner.mjs
//     # authenticated pages need a session cookie copied from the browser:
//     UI_BASE_URL=http://localhost:3000 COOKIE='next-auth.session-token=…' \
//       node tests/performance/ui/lighthouse_runner.mjs
//
// Env:
//     UI_BASE_URL   base URL of the running frontend (default http://localhost:3000)
//     COOKIE        Cookie header for authenticated routes; auth routes skipped if unset
//     FORM_FACTOR   'desktop' (default) | 'mobile'
//     CHROME_PATH   explicit Chrome/Chromium binary (else Playwright chromium / auto-detect)
//     OUT_DIR       report output dir (default docs/performance/ui_lighthouse_<date>)
//
// Exit: 0 = all measured pages within budget; 1 = a budget breach; 2 = setup error.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..', '..', '..');

const UI_BASE_URL = (process.env.UI_BASE_URL || 'http://localhost:3000').replace(/\/$/, '');
const COOKIE = process.env.COOKIE || '';
const FORM_FACTOR = process.env.FORM_FACTOR === 'mobile' ? 'mobile' : 'desktop';
const LIST_ONLY = process.argv.includes('--list');

function loadJson(p, fallback) {
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return fallback; }
}

const routes = loadJson(join(HERE, 'routes.json'), []);
const budgetsFile = loadJson(join(HERE, 'budgets.json'), {});
const DEFAULT_BUDGET = budgetsFile.default || {
  performance_score: 0.85,   // 0..1
  lcp_ms: 2500,
  tbt_ms: 200,
  cls: 0.1,
  fcp_ms: 1800,
  tti_ms: 3800,
  speed_index_ms: 3400,
  total_byte_weight: 1_600_000,
};
function budgetFor(name) {
  return { ...DEFAULT_BUDGET, ...(budgetsFile[name] || {}) };
}

// Find a Chrome/Chromium binary: explicit env → Playwright chromium → auto-detect.
function resolveChromePath() {
  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const pwRoot = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    for (const entry of readdirSync(pwRoot)) {
      if (!entry.startsWith('chromium')) continue;
      for (const bin of ['chrome-linux/chrome', 'chrome-linux/headless_shell', 'chrome-mac/Chromium.app/Contents/MacOS/Chromium']) {
        const candidate = join(pwRoot, entry, bin);
        if (existsSync(candidate)) return candidate;
      }
    }
  } catch { /* pwRoot absent — fall through to auto-detect */ }
  return undefined; // let chrome-launcher auto-detect installed Chrome
}

function listRoutes() {
  console.log(`UI performance routes (base ${UI_BASE_URL}, form factor ${FORM_FACTOR}):`);
  for (const r of routes) {
    const b = budgetFor(r.name);
    const auth = r.auth ? ' [auth]' : '';
    console.log(`  ${r.name.padEnd(26)} ${r.path.padEnd(34)}${auth}  score≥${b.performance_score} LCP≤${b.lcp_ms}ms TBT≤${b.tbt_ms}ms`);
  }
  console.log(`\n${routes.length} routes. Set COOKIE to include the ${routes.filter(r => r.auth).length} [auth] routes.`);
}

async function loadLighthouse() {
  try {
    const [{ default: lighthouse }, chromeLauncher] = await Promise.all([
      import('lighthouse'),
      import('chrome-launcher'),
    ]);
    return { lighthouse, chromeLauncher };
  } catch (e) {
    console.error(
      'ERROR: lighthouse / chrome-launcher not installed.\n' +
      '  cd tests/performance/ui && npm install\n' +
      'then re-run. Use --list to preview routes without them.\n' + `(${e.message})`,
    );
    process.exit(2);
  }
}

// True when Lighthouse ended up on the page we asked for. Compares pathname only:
// the app canonicalises to its public hostname, so host differences are expected and
// harmless, whereas a different PATH means we measured something else entirely.
// Lighthouse echoes the full run configuration into every report, including any
// `extraHeaders` — which for an authenticated run is a live session cookie wrapping
// a super_admin JWT. These reports get committed, so the credential would be
// committed with them (it was, on 2026-08-24, across 26 files). Strip it before the
// report is ever written; nothing downstream reads these fields.
function redactSecrets(lhr) {
  const cs = lhr?.configSettings;
  if (cs && cs.extraHeaders) {
    cs.extraHeaders = '[redacted]';
  }
  return lhr;
}

function samePage(requestedUrl, landedUrl) {
  if (!landedUrl) return false;
  const norm = (u) => {
    try { return new URL(u).pathname.replace(/\/+$/, '') || '/'; } catch { return u; }
  };
  return norm(requestedUrl) === norm(landedUrl);
}

function extract(lhr) {
  const a = lhr.audits;
  const num = (k) => (a[k] && typeof a[k].numericValue === 'number' ? a[k].numericValue : null);
  return {
    performance_score: lhr.categories?.performance?.score ?? null,
    lcp_ms: num('largest-contentful-paint'),
    tbt_ms: num('total-blocking-time'),
    cls: num('cumulative-layout-shift'),
    fcp_ms: num('first-contentful-paint'),
    tti_ms: num('interactive'),
    speed_index_ms: num('speed-index'),
    total_byte_weight: num('total-byte-weight'),
  };
}

// A metric passes if it is null (not measured) or within budget. Lower-is-better
// for every metric except performance_score (higher-is-better).
function evaluate(metrics, budget) {
  const breaches = [];
  const check = (key, ok, detail) => { if (!ok) breaches.push(`${key}: ${detail}`); };
  if (metrics.performance_score !== null)
    check('performance_score', metrics.performance_score >= budget.performance_score,
      `${metrics.performance_score.toFixed(2)} < ${budget.performance_score}`);
  const lower = [
    ['lcp_ms', 'lcp_ms'], ['tbt_ms', 'tbt_ms'], ['cls', 'cls'], ['fcp_ms', 'fcp_ms'],
    ['tti_ms', 'tti_ms'], ['speed_index_ms', 'speed_index_ms'], ['total_byte_weight', 'total_byte_weight'],
  ];
  for (const [mk, bk] of lower) {
    if (metrics[mk] !== null && budget[bk] != null)
      check(mk, metrics[mk] <= budget[bk], `${Math.round(metrics[mk])} > ${budget[bk]}`);
  }
  return breaches;
}

async function run() {
  if (routes.length === 0) { console.error('No routes in routes.json'); process.exit(2); }
  const { lighthouse, chromeLauncher } = await loadLighthouse();

  const active = routes.filter((r) => !r.auth || COOKIE);
  const skipped = routes.filter((r) => r.auth && !COOKIE);
  if (skipped.length) console.log(`Skipping ${skipped.length} auth route(s) — set COOKIE to include them.`);

  // Chrome refuses to carry a `Cookie` header supplied via
  // Network.setExtraHTTPHeaders, so passing the session that way silently produced
  // an UNAUTHENTICATED page load — every auth route quietly measured the login
  // screen instead (caught 2026-08-24). The session is installed as a real cookie
  // in the browser profile below, which Chrome then sends normally.
  const { default: puppeteer } = await import('puppeteer-core');

  const chromePath = resolveChromePath();
  const chrome = await chromeLauncher.launch({
    chromePath,
    chromeFlags: ['--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });

  // Install the session cookie into the launched browser, once, before any run.
  if (COOKIE) {
    const browser = await puppeteer.connect({
      browserURL: `http://127.0.0.1:${chrome.port}`,
      defaultViewport: null,
    });
    const origin = new URL(UI_BASE_URL);
    const cookies = COOKIE.split(/;\s*/).filter(Boolean).map((pair) => {
      const idx = pair.indexOf('=');
      const name = pair.slice(0, idx).trim();
      const value = pair.slice(idx + 1).trim();
      return {
        name,
        value,
        domain: origin.hostname,
        path: '/',
        httpOnly: true,
        // Auth.js names the cookie `__Secure-…` whenever AUTH_URL is https, and
        // Chrome rejects that prefix unless the cookie is marked Secure. Chrome
        // treats localhost as a secure context, so this is accepted over plain
        // http there — which is what makes measuring a local build possible.
        secure: name.startsWith('__Secure-') || origin.protocol === 'https:',
      };
    });
    await browser.setCookie(...cookies);
    browser.disconnect();
  }

  const screenEmulation = FORM_FACTOR === 'desktop'
    ? { mobile: false, width: 1350, height: 940, deviceScaleFactor: 1, disabled: false }
    : { mobile: true, width: 412, height: 823, deviceScaleFactor: 1.75, disabled: false };

  const results = [];
  try {
    for (const r of active) {
      const url = `${UI_BASE_URL}${r.path}`;
      process.stdout.write(`• ${r.name} (${r.path}) … `);
      let metrics = null; let breaches = ['lighthouse run failed']; let error = null; let lhr = null;
      try {
        const headers = COOKIE && r.auth ? { Cookie: COOKIE } : undefined;
        // extraHeaders must reach Lighthouse's SETTINGS. Passing it only as a
        // top-level flag is silently ignored by Lighthouse 12, which is how the
        // authenticated runs below used to end up measuring the login page.
        const runnerResult = await lighthouse(
          url,
          {
            port: chrome.port,
            onlyCategories: ['performance'],
            formFactor: FORM_FACTOR,
            screenEmulation,
            extraHeaders: headers,
          },
          {
            extends: 'lighthouse:default',
            settings: {
              onlyCategories: ['performance'],
              formFactor: FORM_FACTOR,
              screenEmulation,
              extraHeaders: headers,
            },
          },
        );
        lhr = runnerResult.lhr;
        // A page that failed to load still yields an `lhr` — with every metric null.
        // `evaluate()` treats a null metric as "not measured, so not a breach", so
        // such a run used to be reported as PASS. That is the worst possible outcome
        // for a perf gate: a 500 shows up as green. Measured 2026-08-24: nine routes
        // returned `ERRORED_DOCUMENT_REQUEST (Status code: 500)` and all nine were
        // reported as passing. Fail loudly instead.
        if (lhr.runtimeError) {
          throw new Error(`${lhr.runtimeError.code}: ${lhr.runtimeError.message}`);
        }
        metrics = extract(lhr);
        if (metrics.performance_score === null) {
          throw new Error('Lighthouse returned no performance score — nothing was measured');
        }
        // An authenticated page that bounced to a login screen still produces a
        // complete, plausible-looking Lighthouse report — for the WRONG page. Treat
        // that as an error rather than publishing numbers under the route's name.
        const landed = lhr.finalDisplayedUrl || lhr.finalUrl || '';
        if (!samePage(url, landed)) {
          const toLogin = /\/login(\?|$)/.test(landed);
          throw new Error(
            toLogin
              ? `bounced to ${landed} — session cookie was not accepted`
              : `ended on ${landed}, not the requested path — measured a different page`,
          );
        }
        breaches = evaluate(metrics, budgetFor(r.name));
      } catch (e) { error = e.message; }
      const status = error ? 'error' : (breaches.length === 0 ? 'pass' : 'budget-fail');
      console.log(error ? `ERROR ${error}` :
        `${status} (score ${metrics.performance_score ?? '—'}, LCP ${metrics.lcp_ms ? Math.round(metrics.lcp_ms) + 'ms' : '—'})`);
      results.push({ name: r.name, path: r.path, url, auth: !!r.auth, status, metrics, breaches, error, budget: budgetFor(r.name) });
      if (lhr) rawLhr[r.name] = redactSecrets(lhr);
    }
  } finally {
    await chrome.kill();
  }
  return { results, skipped };
}

const rawLhr = {};

function writeReports(results, skipped) {
  const date = new Date().toISOString().slice(0, 10);
  const outDir = process.env.OUT_DIR || join(REPO_ROOT, 'docs', 'performance', `ui_lighthouse_${date}`);
  mkdirSync(outDir, { recursive: true });
  const counts = {
    measured: results.length,
    pass: results.filter((r) => r.status === 'pass').length,
    budget_fail: results.filter((r) => r.status === 'budget-fail').length,
    error: results.filter((r) => r.status === 'error').length,
    skipped: skipped.length,
  };
  const report = {
    generated_at: new Date().toISOString(),
    base_url: UI_BASE_URL, form_factor: FORM_FACTOR,
    counts, results,
    skipped: skipped.map((r) => ({ name: r.name, path: r.path })),
  };
  writeFileSync(join(outDir, `ui_lighthouse_report_${date}.json`), JSON.stringify(report, null, 2) + '\n');
  for (const [name, lhr] of Object.entries(rawLhr)) {
    writeFileSync(join(outDir, `${name}.lhr.json`), JSON.stringify(lhr));
  }
  // Markdown summary
  const md = [];
  md.push(`# UI Lighthouse Report — ${date}`, '');
  md.push(`Base URL: \`${UI_BASE_URL}\` · form factor: **${FORM_FACTOR}**  `);
  md.push(`${counts.pass} pass · ${counts.budget_fail} budget-fail · ${counts.error} error · ${counts.skipped} skipped (no COOKIE)`, '');
  md.push('| Page | Path | Status | Score | LCP ms | TBT ms | CLS | Bytes |');
  md.push('|---|---|---|---|---|---|---|---|');
  for (const r of results) {
    const m = r.metrics || {};
    const f = (v, d = 0) => (typeof v === 'number' ? v.toFixed(d) : '—');
    md.push(`| ${r.name} | \`${r.path}\` | ${r.status} | ${m.performance_score != null ? m.performance_score.toFixed(2) : '—'} | ${f(m.lcp_ms)} | ${f(m.tbt_ms)} | ${f(m.cls, 3)} | ${f(m.total_byte_weight)} |`);
  }
  md.push('');
  const failing = results.filter((r) => r.breaches && r.breaches.length && r.status !== 'error');
  if (failing.length) {
    md.push('## Budget breaches', '');
    for (const r of failing) md.push(`- **${r.name}** (\`${r.path}\`): ${r.breaches.join('; ')}`);
    md.push('');
  }
  if (skipped.length) md.push(`## Skipped (no COOKIE)`, '', skipped.map((r) => `- ${r.name} \`${r.path}\``).join('\n'), '');
  writeFileSync(join(outDir, `ui_lighthouse_report_${date}.md`), md.join('\n') + '\n');
  return { outDir, counts };
}

async function main() {
  if (LIST_ONLY) { listRoutes(); return 0; }
  const { results, skipped } = await run();
  const { outDir, counts } = writeReports(results, skipped);
  console.log(`\n${counts.pass} pass, ${counts.budget_fail} budget-fail, ${counts.error} error, ${counts.skipped} skipped.`);
  console.log(`Report: ${outDir}`);
  return counts.budget_fail + counts.error === 0 ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((e) => { console.error(e); process.exit(2); });
