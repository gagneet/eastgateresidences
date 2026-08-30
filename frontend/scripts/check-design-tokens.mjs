#!/usr/bin/env node
/**
 * Design-token ratchet (GAP-UI-001).
 *
 * The app carries a large legacy surface of raw Tailwind palette classes
 * (`bg-white`, `text-slate-500`, `border-gray-200`, …) that bypass the design
 * tokens in globals.css. A blunt lint rule would fire thousands of times and be
 * turned off within a day, so this enforces a RATCHET instead:
 *
 *   - a file may never INCREASE its raw-palette count
 *   - a new file may not introduce raw-palette classes at all
 *   - counts that drop are rewritten into the baseline, locking the win in
 *
 * NOT ENFORCED IN CI (as of 2026-08-25). No workflow under .github/workflows runs
 * this, so a stale baseline goes unnoticed until someone runs it by hand — which is
 * exactly what happened after GAP-UI-001 Phase 2: eleven components were rewritten,
 * four of them improved, and the baseline sat stale on main until an audit caught it.
 * If you are wiring CI: run `yarn check:design-tokens` in the frontend job. It exits
 * non-zero on BOTH a regression and an un-baselined improvement, which is intentional
 * (see the note above the improvements block) but means it needs the baseline
 * committed alongside any change that touches className strings.
 *
 * Usage:
 *   node scripts/check-design-tokens.mjs           # check (CI)
 *   node scripts/check-design-tokens.mjs --update  # re-baseline after a cleanup
 *
 * Deliberately not an ESLint rule: Tailwind classes live inside string
 * literals, template literals, clsx()/cn() calls and cva() variants, so a
 * whole-file textual count is both simpler and harder to sidestep.
 */
import {readFileSync, writeFileSync, existsSync} from "node:fs";
import {readdirSync, statSync} from "node:fs";
import {join, relative, dirname} from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(ROOT, "src");
const BASELINE = join(ROOT, "scripts", "design-token-baseline.json");

/**
 * Raw Tailwind palette utilities that should be design tokens instead.
 *
 * Two families are watched:
 *   NEUTRAL    — gray/slate/zinc/neutral/stone: these are always a token
 *                (bg-card, text-foreground, text-muted-foreground, border-border).
 *   DECORATIVE — indigo/violet/purple/fuchsia/pink/cyan/sky/blue/teal: these have
 *                no semantic role in this product, so a page reaching for one is
 *                inventing a brand colour. Chart colours come from lib/chartTheme.
 *
 * Deliberately NOT watched: red / orange / amber / yellow / lime / green /
 * emerald / rose. Those encode STATE (arrears severity, compliance RAG, SLA
 * breach) and flattening them to `text-foreground` would delete meaning, not
 * duplication. They are still expected to route through CHART_STATUS where a
 * chart is involved — the ratchet just cannot tell a status use from a
 * decorative one by regex, so it does not guess.
 */
const NEUTRAL = "gray|slate|zinc|neutral|stone";
const DECORATIVE = "indigo|violet|purple|fuchsia|pink|cyan|sky|blue|teal";
const RAW = new RegExp(
    String.raw`\b(?:bg-white|text-black|(?:text|bg|border|ring|divide|from|to|via)-(?:${NEUTRAL}|${DECORATIVE})-\d{2,3})\b`,
    "g",
);

function walk(dir, out = []) {
    for (const e of readdirSync(dir)) {
        const p = join(dir, e);
        if (statSync(p).isDirectory()) walk(p, out);
        else if (/\.(tsx|jsx)$/.test(p)) out.push(p);
    }
    return out;
}

const counts = {};
for (const p of walk(SRC)) {
    const n = (readFileSync(p, "utf8").match(RAW) || []).length;
    if (n > 0) counts[relative(ROOT, p).replace(/\\/g, "/")] = n;
}

const update = process.argv.includes("--update");
if (update || !existsSync(BASELINE)) {
    writeFileSync(BASELINE, JSON.stringify(counts, null, 2) + "\n");
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    console.log(`design-token baseline written: ${Object.keys(counts).length} files, ${total} raw-palette usages`);
    process.exit(0);
}

const base = JSON.parse(readFileSync(BASELINE, "utf8"));
const regressions = [];
const improvements = [];

for (const [file, n] of Object.entries(counts)) {
    const was = base[file];
    if (was === undefined) regressions.push(`  NEW FILE  ${file}: ${n} raw-palette classes (use design tokens — see src/lib/chartTheme.ts and globals.css)`);
    else if (n > was) regressions.push(`  INCREASED ${file}: ${was} -> ${n}`);
    else if (n < was) improvements.push(`  improved  ${file}: ${was} -> ${n}`);
}
for (const file of Object.keys(base)) {
    if (counts[file] === undefined) improvements.push(`  cleared   ${file}: ${base[file]} -> 0`);
}

// Check mode NEVER writes. An earlier version rewrote the baseline here whenever
// it saw an improvement, which broke the tool in two ways: CI (a fresh checkout)
// silently produced a different baseline than the committed one and then threw it
// away, so the ratchet never actually tightened; and `--update` became
// meaningless, because any run could rewrite the file. Writing is now confined to
// the explicit `--update` path above, which makes a check run deterministic and
// side-effect free.
if (improvements.length) {
    console.log("Design-token improvements since baseline:");
    for (const i of improvements) console.log(i);
}

// An un-baselined improvement is a soft failure, not a pass. Exiting 0 here would
// let the baseline drift permanently behind reality, and a later regression back
// to the stale (looser) numbers would then go unnoticed — which is precisely the
// hole a ratchet exists to close. Fail, and say exactly how to fix it.
if (improvements.length && !regressions.length) {
    console.error(
        `\nDesign-token ratchet: ${improvements.length} file(s) improved but the baseline is stale.\n` +
        "Run `yarn check:design-tokens --update` and commit scripts/design-token-baseline.json\n" +
        "to lock these wins in.\n",
    );
    process.exit(1);
}

if (regressions.length) {
    console.error("\nDesign-token ratchet FAILED — raw Tailwind palette classes were added:\n");
    for (const r of regressions) console.error(r);
    console.error("\nUse the design tokens instead: bg-card, text-foreground, text-muted-foreground,");
    console.error("border-border, bg-muted, bg-background. Chart colours come from src/lib/chartTheme.ts.");
    console.error("Status colours (red/amber/green) are semantic and allowed — use CHART_STATUS.\n");
    process.exit(1);
}

const total = Object.values(counts).reduce((a, b) => a + b, 0);
console.log(`design-token ratchet OK — ${Object.keys(counts).length} files, ${total} raw-palette usages (never increasing)`);
