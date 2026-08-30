/**
 * Navigation integrity: no duplicate menu entries, no links to routes that don't exist.
 *
 * DashboardLayout renders the sidebar two ways — a grouped view (`navGroups`,
 * which resolves items via `navItems.find(i => i.href === ...)`) and a flat view
 * (`navItems.filter(i => i.show)`). A second entry for the same href is dead
 * config in the grouped view but renders TWICE in the flat view, and one href
 * listed under two groups shows the same link twice to anyone both gates admit.
 * Both had really happened (`/admin/workflows` twice in `navItems`; the two
 * onboarding entries in Management *and* Platform Admin). This reads the source
 * rather than rendering, because the defect is in the config, not the render.
 */
import fs from 'fs';
import path from 'path';

const ROOT = path.resolve(__dirname, '../../../..');
const LAYOUT = path.join(ROOT, 'frontend/src/components/layout/DashboardLayout.tsx');
const APP_DIR = path.join(ROOT, 'frontend/src/app');

const src = fs.readFileSync(LAYOUT, 'utf8');

function sliceArray(name: string): string {
    const lines = src.split('\n');
    const start = lines.findIndex(l => l.trim().startsWith(`const ${name} = [`));
    if (start < 0) throw new Error(`${name} not found`);
    let depth = 0;
    for (let i = start; i < lines.length; i++) {
        depth += (lines[i].match(/\[/g) || []).length - (lines[i].match(/]/g) || []).length;
        if (depth === 0 && i > start) return lines.slice(start, i + 1).join('\n');
    }
    throw new Error(`unterminated ${name}`);
}

function appRoutes(): string[] {
    const out: string[] = [];
    const walk = (dir: string, segs: string[]) => {
        for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                const isRouteGroup = entry.name.startsWith('(') && entry.name.endsWith(')');
                walk(full, isRouteGroup ? segs : [...segs, entry.name]);
            } else if (/^page\.(tsx|jsx|ts|js)$/.test(entry.name)) {
                out.push('/' + segs.join('/'));
            }
        }
    };
    walk(APP_DIR, []);
    return out;
}

describe('DashboardLayout navigation integrity', () => {
    it('declares exactly one navItems entry per href', () => {
        const hrefs = [...sliceArray('navItems').matchAll(/href:\s*'([^']+)'/g)].map(m => m[1]);
        expect(hrefs.filter((h, i) => hrefs.indexOf(h) !== i)).toEqual([]);
    });

    it('does not list the same href under more than one nav group', () => {
        const byGroup: Record<string, string[]> = {};
        let current = '';
        for (const line of sliceArray('navGroups').split('\n')) {
            const id = line.match(/id:\s*'([a-z_]+)'/);
            if (id) {
                current = id[1];
                byGroup[current] = byGroup[current] || [];
            }
            if (!current) continue;
            const ref = line.match(/i\.href === '([^']+)'/);
            if (ref) byGroup[current].push(ref[1]);
            const inline = line.match(/^\s+href:\s*'([^']+)'/);
            if (inline) byGroup[current].push(inline[1]);
        }
        // The one deliberate exception: Rental Certificates appears in Governance
        // (owner + staff roles) and again in My Tenancy (tenant ONLY). The gates are
        // disjoint, so no single user ever sees it twice. Anything else is a bug.
        const ALLOWED_IN_TWO_GROUPS = new Set(['/requests/rental-certificates']);

        const all = Object.values(byGroup).flat();
        const dupes = [...new Set(all.filter((h, i) => all.indexOf(h) !== i))]
            .filter(h => !ALLOWED_IN_TWO_GROUPS.has(h));
        expect(dupes.map(h => `${h} -> ${Object.keys(byGroup).filter(g => byGroup[g].includes(h)).join(', ')}`))
            .toEqual([]);
    });

    it('points every internal nav href at a route that exists', () => {
        const routes = appRoutes();
        const dynamic = routes.filter(r => r.includes('['));
        const statics = new Set(routes.filter(r => !r.includes('[')));

        const resolves = (href: string) => {
            const clean = href.split('?')[0].split('#')[0].replace(/\/$/, '') || '/';
            if (statics.has(clean)) return true;
            const segs = clean.replace(/^\//, '').split('/');
            return dynamic.some(r => {
                const rs = r.replace(/^\//, '').split('/');
                return rs.length === segs.length && rs.every((s, i) => s.startsWith('[') || s === segs[i]);
            });
        };

        const broken = [...src.matchAll(/href:\s*'([^']+)'/g)]
            .map(m => m[1])
            .filter(h => h.startsWith('/')
                && !h.startsWith('/api')
                && !/\.(html|pdf|png|jpg|svg|ico|json|css|txt|xml)$/.test(h)
                && !resolves(h));
        expect(broken).toEqual([]);
    });
});
