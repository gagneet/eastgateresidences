// @featuretrace:security-ip-logging — Security & IP Logs page: stat cards, login activity, IP intelligence.
// Layer: frontend
// Data flow: /admin/security-ip-logs -> GET /security/stats + /security/login-attempts
//            -> login_audit_logs -> clickable cards + sortable, searchable table (global).
// Related: backend/routers/security.py
//          backend/utils/audit_search.py
//          frontend/src/components/shared/SortableTableHeader.jsx
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import {
    Activity,
    AlertTriangle,
    CheckCircle2,
    ChevronLeft,
    ChevronRight,
    Flag,
    Globe,
    HelpCircle,
    Map,
    Monitor,
    RefreshCw,
    Search,
    ShieldAlert,
    Smartphone,
    Tablet,
    Terminal,
    XCircle,
} from 'lucide-react';
import {SortableTh, useTableSort} from '../../../components/shared/SortableTableHeader';
import {
    Bar,
    CartesianGrid,
    Cell,
    ComposedChart,
    Legend,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { toast } from 'sonner';

const TABS = ['Overview', 'Login Activity', 'Suspicious Events', 'IP Intelligence'];

const STATUS_CONFIG = {
    success: {label: 'Success', color: 'bg-emerald-100 text-emerald-800', icon: CheckCircle2},
    failed: {label: 'Failed', color: 'bg-red-100 text-red-800', icon: XCircle},
    deactivated: {label: 'Deactivated', color: 'bg-amber-100 text-amber-800', icon: AlertTriangle},
};

const FLAG_LABELS = {
    new_country: 'New Country',
    new_device: 'New Device',
    new_ip: 'New IP',
    odd_time: 'Odd Hour',
    impossible_travel: 'Impossible Travel',
};

const DEVICE_ICONS = {
    mobile: Smartphone,
    tablet: Tablet,
    desktop: Monitor,
    // "api" and "unknown" are real, distinct outcomes — a python-requests login
    // is not an unknown desktop browser, and a missing User-Agent is not a
    // desktop either. Falling both back to Monitor is what made every script
    // login read as "Unknown" on a desktop icon.
    api: Terminal,
    unknown: HelpCircle,
};

// Shown when a row has no browser name. Distinguishes "we could not parse it"
// from "there was nothing to parse".
const DEVICE_LABELS = {
    api: 'Script / API client',
    unknown: 'No User-Agent sent',
};

/**
 * Render a login row's addresses as "public (local)".
 *
 * Falls back to whichever single address exists, and finally to the legacy
 * `ip_address` for rows written before migration 0094 split the pair. Never
 * shows the same value twice — a direct connection from a public address has
 * no separate local address worth repeating.
 */
function formatIp(row) {
    const publicIp = row?.public_ip;
    const localIp = row?.local_ip;
    if (publicIp && localIp && publicIp !== localIp) return `${publicIp} (${localIp})`;
    return publicIp || localIp || row?.ip_address || '—';
}

const PIE_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#84cc16', '#ec4899'];
/**
 * @generated FunctionHeader
 * Function: countryFlag
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function countryFlag(code) {
    if (!code || code.length !== 2) return '';
    return code.toUpperCase().replace(/./g, c =>
        String.fromCodePoint(c.charCodeAt(0) + 127397)
    );
}
/**
 * @generated FunctionHeader
 * Function: formatDatetime
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatDatetime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('en-AU', {
        day: 'numeric', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
}
/**
 * @generated FunctionHeader
 * Function: RiskBadge
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function RiskBadge({score, flags}) {
    if (!score || score === 0) return null;
    const color = score >= 75 ? 'bg-red-100 text-red-800' : score >= 50 ? 'bg-orange-100 text-orange-800' : 'bg-yellow-100 text-yellow-800';
    return (
        <div className="flex items-center gap-1">
      <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${color}`}>
        Risk {score}
      </span>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: StatCard
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatCard({title, value, icon: Icon, color, subtitle, onClick, actionLabel}) {
    // Every card is an entry point into the data it summarises. A card that
    // shows a number and does nothing teaches users the UI is unresponsive and
    // wastes the most prominent space on the page — so onClick is required in
    // practice, and the keyboard handlers make it a real control rather than a
    // div that happens to respond to a mouse.
    const interactive = typeof onClick === 'function';
    return (
        <Card
            className={`border-none shadow-md transition ${
                interactive
                    ? 'cursor-pointer hover:shadow-xl hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none active:scale-95'
                    : ''
            }`}
            role={interactive ? 'button' : undefined}
            tabIndex={interactive ? 0 : undefined}
            aria-label={interactive ? `${title}: ${value ?? 'no data'}. ${actionLabel || 'View details'}` : undefined}
            data-testid={`stat-card-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}
            onClick={onClick}
            onKeyDown={interactive ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onClick();
                }
            } : undefined}
        >
            <CardContent className="p-6">
                <div className="flex items-center justify-between mb-3">
                    <div className={`p-2.5 rounded-xl ${color}`}>
                        <Icon size={20} className="text-white"/>
                    </div>
                    {interactive && <ChevronRight size={16} className="text-slate-300"/>}
                </div>
                <p className="text-3xl font-black text-slate-900">{value ?? '—'}</p>
                <p className="text-sm font-medium text-slate-500 mt-1">{title}</p>
                {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
                {interactive && actionLabel && (
                    <p className="text-xs font-medium text-indigo-600 mt-2">{actionLabel} →</p>
                )}
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: Pagination
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Pagination({page, pages, onPage}) {
    if (pages <= 1) return null;
    return (
        <div className="flex items-center justify-between mt-4">
            <p className="text-sm text-slate-500">Page {page} of {pages}</p>
            <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
                    <ChevronLeft size={14}/>
                </Button>
                <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
                    <ChevronRight size={14}/>
                </Button>
            </div>
        </div>
    );
}
// ─────────────────────────────────────────────
// Overview Tab
// ─────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: OverviewTab
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function OverviewTab({stats, loading, onDrillDown}) {
    if (loading) return <div className="flex items-center justify-center h-64 text-slate-400">Loading stats…</div>;
    if (!stats) return null;

    return (
        <div className="space-y-6">
            {/* Stat Cards */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                {/* Every card drills into the Login Activity tab pre-filtered to
                    its own subset — the card is the entry point to the rows it
                    counts, not a decoration. */}
                <StatCard title="Logins (30d)" value={stats.total_logins_30d} icon={Activity} color="bg-indigo-500"
                          actionLabel="View all login activity"
                          onClick={() => onDrillDown({tab: 1, search: ''})}/>
                <StatCard title="Failed Attempts" value={stats.failed_attempts_30d} icon={XCircle} color="bg-red-500"
                          actionLabel="View failed attempts"
                          onClick={() => onDrillDown({tab: 1, search: 'status:failed'})}/>
                <StatCard title="Suspicious Events" value={stats.suspicious_events_30d} icon={AlertTriangle}
                          color="bg-orange-500"
                          actionLabel="Review suspicious events"
                          onClick={() => onDrillDown({tab: 2})}/>
                <StatCard title="Unique Countries" value={stats.unique_countries_30d} icon={Globe}
                          color="bg-emerald-500"
                          actionLabel="Break down by country"
                          onClick={() => onDrillDown({tab: 0, focus: 'countries'})}/>
                <StatCard title="Unique IPs" value={stats.unique_ips_30d} icon={Map} color="bg-purple-500"
                          actionLabel="View IP intelligence"
                          onClick={() => onDrillDown({tab: 3})}/>
            </div>

            {/* Daily Activity Chart */}
            <Card className="border-none shadow-md">
                <CardHeader>
                    <CardTitle className="text-base font-bold">Daily Login Activity (30 days)</CardTitle>
                </CardHeader>
                <CardContent>
                    <ResponsiveContainer width="100%" height={260}>
                        <ComposedChart data={stats.daily_activity}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                            <XAxis dataKey="date" tick={{fontSize: 11}} tickFormatter={d => d.slice(5)}/>
                            <YAxis tick={{fontSize: 11}}/>
                            <Tooltip/>
                            <Legend/>
                            <Bar dataKey="success" fill="#10b981" name="Successful" radius={[3, 3, 0, 0]}/>
                            <Bar dataKey="failed" fill="#ef4444" name="Failed" radius={[3, 3, 0, 0]}/>
                        </ComposedChart>
                    </ResponsiveContainer>
                </CardContent>
            </Card>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Country Distribution */}
                <Card className="border-none shadow-md">
                    <CardHeader>
                        <CardTitle className="text-base font-bold">Login Countries</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center gap-6">
                            <ResponsiveContainer width={160} height={160}>
                                <PieChart>
                                    <Pie data={stats.country_distribution} dataKey="count" cx="50%" cy="50%"
                                         outerRadius={70}>
                                        {stats.country_distribution.map((_, i) => (
                                            <Cell key={i} fill={PIE_COLORS[ i % PIE_COLORS.length ]}/>
                                        ))}
                                    </Pie>
                                    <Tooltip
                                        formatter={(v, n, p) => [v, p.payload.country_name || p.payload.country_code]}/>
                                </PieChart>
                            </ResponsiveContainer>
                            <div className="space-y-2 flex-1">
                                {stats.country_distribution.slice(0, 6).map((c, i) => (
                                    <div key={i} className="flex items-center gap-2">
                                        <span className="w-3 h-3 rounded-full flex-shrink-0"
                                              style={{background: PIE_COLORS[ i % PIE_COLORS.length ]}}/>
                                        <span
                                            className="text-xs text-slate-700 flex-1">{countryFlag(c.country_code)} {c.country_name || c.country_code}</span>
                                        <span className="text-xs font-bold text-slate-900">{c.count}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* Device Distribution */}
                <Card className="border-none shadow-md">
                    <CardHeader>
                        <CardTitle className="text-base font-bold">Device Types</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center gap-6">
                            <ResponsiveContainer width={160} height={160}>
                                <PieChart>
                                    <Pie data={stats.device_distribution} dataKey="count" cx="50%" cy="50%"
                                         outerRadius={70}>
                                        {stats.device_distribution.map((_, i) => (
                                            <Cell key={i} fill={PIE_COLORS[ i % PIE_COLORS.length ]}/>
                                        ))}
                                    </Pie>
                                    <Tooltip/>
                                </PieChart>
                            </ResponsiveContainer>
                            <div className="space-y-3 flex-1">
                                {stats.device_distribution.map((d, i) => {
                                    const Icon = DEVICE_ICONS[ d.device_type ] || Monitor;
                                    return (
                                        <div key={i} className="flex items-center gap-2">
                                            <Icon size={14} className="text-slate-500 flex-shrink-0"/>
                                            <span
                                                className="text-xs text-slate-700 flex-1 capitalize">{d.device_type}</span>
                                            <span className="text-xs font-bold text-slate-900">{d.count}</span>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Top Failed IPs */}
            {stats.top_failed_ips?.length > 0 && (
                <Card className="border-none shadow-md">
                    <CardHeader>
                        <CardTitle className="text-base font-bold">Top Failed Login IPs</CardTitle>
                        <CardDescription>IPs with highest failed attempt counts in the last 30 days</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                <tr className="border-b text-left text-slate-500 text-xs">
                                    <th className="pb-2 pr-4">IP Address</th>
                                    <th className="pb-2 pr-4">Failed</th>
                                    <th className="pb-2 pr-4">Country</th>
                                    <th className="pb-2">Last Seen</th>
                                </tr>
                                </thead>
                                <tbody>
                                {stats.top_failed_ips.map((ip, i) => (
                                    <tr key={i}
                                        className={`border-b last:border-0 ${ip.count > 10 ? 'bg-red-50' : ''}`}>
                                        <td className="py-2 pr-4 font-mono text-xs text-slate-900">{ip.ip}</td>
                                        <td className="py-2 pr-4">
                        <span
                            className={`px-2 py-0.5 rounded-full text-xs font-bold ${ip.count > 10 ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-700'}`}>
                          {ip.count}
                        </span>
                                        </td>
                                        <td className="py-2 pr-4 text-xs">{countryFlag(ip.country_code)} {ip.country_name || '—'}</td>
                                        <td className="py-2 text-xs text-slate-500">{formatDatetime(ip.last_seen)}</td>
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
// ─────────────────────────────────────────────
// Login Activity Table (shared by Activity + Suspicious tabs)
// ─────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: LoginTable
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LoginTable({items, total, page, pages, onPage, loading}) {
    // Sorts the CURRENT page. The audit log is server-paginated and already
    // arrives newest-first, so this reorders what the operator is looking at
    // rather than pretending to sort the whole collection — which would need a
    // server-side sort parameter and is a separate change.
    // Stable object, not a literal: useTableSort takes `accessors` as a memo
    // dependency, so a fresh object each render would re-sort the page on every
    // unrelated state change. useCallback on the function alone is not enough.
    const sortAccessors = React.useMemo(() => ({ip_sort: (row) => formatIp(row)}), []);
    const {sort, toggle, sorted} = useTableSort(
        items,
        {field: 'attempted_at', direction: 'desc'},
        sortAccessors,
    );

    if (loading) return <div className="flex items-center justify-center h-32 text-slate-400">Loading…</div>;

    return (
        <>
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                    {/* Click-to-sort on every column, via the shared primitive so
                        ordering, null handling and keyboard access behave the
                        same on every table in the app. */}
                    <tr className="border-b text-left text-slate-500 text-xs">
                        <SortableTh label="Date / Time" field="attempted_at" sort={sort} onSort={toggle}/>
                        <SortableTh label="User" field="email" sort={sort} onSort={toggle}/>
                        <SortableTh label="IP / Location" field="ip_sort" sort={sort} onSort={toggle}/>
                        <SortableTh label="Device" field="device_info.device_type" sort={sort} onSort={toggle}/>
                        <SortableTh label="Status" field="status" sort={sort} onSort={toggle}/>
                        <SortableTh label="Risk" field="risk_score" sort={sort} onSort={toggle}/>
                    </tr>
                    </thead>
                    <tbody>
                    {items.length === 0 && (
                        <tr>
                            <td colSpan={6} className="py-8 text-center text-slate-400">No records found</td>
                        </tr>
                    )}
                    {sorted.map((row, i) => {
                        const statusCfg = STATUS_CONFIG[ row.status ] || STATUS_CONFIG.failed;
                        const StatusIcon = statusCfg.icon;
                        const DeviceIcon = DEVICE_ICONS[ row.device_info?.device_type ] || Monitor;
                        return (
                            <tr key={i} className="border-b last:border-0 hover:bg-slate-50">
                                <td className="py-3 pr-4">
                                    <p className="text-xs font-medium text-slate-900">{formatDatetime(row.attempted_at)}</p>
                                    {row.cf_ray &&
                                        <p className="text-xs text-slate-400 font-mono">{row.cf_ray.slice(0, 16)}</p>}
                                </td>
                                <td className="py-3 pr-4">
                                    <p className="text-xs font-medium text-slate-900">{row.user_full_name || '—'}</p>
                                    <p className="text-xs text-slate-500">{row.email}</p>
                                </td>
                                <td className="py-3 pr-4">
                                    {/* Public first, local in brackets. Showing one
                                        conflated value made an internal address
                                        ambiguous — no forwarded header, an untrusted
                                        proxy, and a genuinely local caller all looked
                                        identical. */}
                                    <p className="text-xs font-mono text-slate-900">{formatIp(row)}</p>
                                    <p className="text-xs text-slate-500">
                                        {countryFlag(row.geo?.country_code)} {row.geo?.city !== 'Unknown' ? `${row.geo?.city}, ` : ''}{row.geo?.country_name}
                                        {row.is_hosting_provider && (
                                            <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] font-semibold text-amber-700"
                                                  title="Datacentre or VPN network — not a residential ISP">
                                                VPN/Hosting
                                            </span>
                                        )}
                                    </p>
                                </td>
                                <td className="py-3 pr-4">
                                    <div className="flex items-center gap-1.5">
                                        <DeviceIcon size={13} className="text-slate-500 flex-shrink-0"/>
                                        <span className="text-xs text-slate-700" title={row.user_agent || ''}>
                                            {row.device_info?.browser
                                                && row.device_info.browser !== 'Unknown'
                                                ? row.device_info.browser
                                                : (DEVICE_LABELS[row.device_info?.device_type] || '—')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-slate-500 ml-5">
                                        {row.device_info?.os && row.device_info.os !== 'Unknown'
                                            ? row.device_info.os
                                            : (row.signals?.ch_platform || row.device_info?.device_type || '')}
                                    </p>
                                </td>
                                <td className="py-3 pr-4">
                    <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusCfg.color}`}>
                      <StatusIcon size={11}/>
                        {statusCfg.label}
                    </span>
                                    {row.failure_reason && (
                                        <p className="text-xs text-slate-500 mt-0.5">{row.failure_reason.replace(/_/g, ' ')}</p>
                                    )}
                                </td>
                                <td className="py-3">
                                    {row.risk_score > 0 ? (
                                        <div className="space-y-1">
                                            <RiskBadge score={row.risk_score} flags={row.risk_flags}/>
                                            <div className="flex flex-wrap gap-1">
                                                {( row.risk_flags || [] ).map(f => (
                                                    <span key={f}
                                                          className="px-1.5 py-0.5 bg-orange-50 text-orange-700 text-[10px] rounded font-medium">
                              {FLAG_LABELS[ f ] || f}
                            </span>
                                                ))}
                                            </div>
                                        </div>
                                    ) : (
                                        <span className="text-xs text-slate-400">—</span>
                                    )}
                                </td>
                            </tr>
                        );
                    })}
                    </tbody>
                </table>
            </div>
            <Pagination page={page} pages={pages} onPage={onPage}/>
        </>
    );
}
// ─────────────────────────────────────────────
// IP Intelligence Tab
// ─────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: IPIntelligenceTab
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function IPIntelligenceTab({api}) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [flagging, setFlagging] = useState(null);

    const load = useCallback(async (p = 1) => {
        setLoading(true);
        try {
            const res = await api.get(`/security/ip-intelligence?page=${p}&per_page=25`);
            setData(res.data);
            setPage(p);
        } catch {
            toast.error('Failed to load IP intelligence');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        load(1);
    }, [load]);
    /**
     * @generated FunctionHeader
     * Function: handleFlag
     * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleFlag = async (ip) => {
        const reason = window.prompt(`Flag IP ${ip} as suspicious. Reason:`);
        if (!reason) return;
        setFlagging(ip);
        try {
            await api.post('/security/flag-ip', {ip_address: ip, reason, action: 'suspicious'});
            toast.success(`IP ${ip} flagged`);
            load(page);
        } catch {
            toast.error('Failed to flag IP');
        } finally {
            setFlagging(null);
        }
    };

    if (loading) return <div className="flex items-center justify-center h-32 text-slate-400">Loading…</div>;
    if (!data) return null;

    return (
        <>
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                    <tr className="border-b text-left text-slate-500 text-xs">
                        <th className="pb-3 pr-4">IP Address</th>
                        <th className="pb-3 pr-4">Last Seen</th>
                        <th className="pb-3 pr-4">Users</th>
                        <th className="pb-3 pr-4">Success</th>
                        <th className="pb-3 pr-4">Failed</th>
                        <th className="pb-3 pr-4">Location</th>
                        <th className="pb-3 pr-4">ISP</th>
                        <th className="pb-3">Status</th>
                    </tr>
                    </thead>
                    <tbody>
                    {data.items.length === 0 && (
                        <tr>
                            <td colSpan={8} className="py-8 text-center text-slate-400">No IP data yet</td>
                        </tr>
                    )}
                    {data.items.map((row, i) => (
                        <tr key={i}
                            className={`border-b last:border-0 hover:bg-slate-50 ${row.failed_count > 10 ? 'bg-red-50' : ''}`}>
                            <td className="py-3 pr-4">
                                <p className="font-mono text-xs text-slate-900">{row.ip_address}</p>
                            </td>
                            <td className="py-3 pr-4 text-xs text-slate-500">{formatDatetime(row.last_seen)}</td>
                            <td className="py-3 pr-4 text-xs font-bold">{row.unique_user_count}</td>
                            <td className="py-3 pr-4">
                                <span
                                    className="px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded-full text-xs font-medium">{row.success_count}</span>
                            </td>
                            <td className="py-3 pr-4">
                  <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${row.failed_count > 10 ? 'bg-red-200 text-red-900' : 'bg-red-100 text-red-800'}`}>
                    {row.failed_count}
                  </span>
                            </td>
                            <td className="py-3 pr-4 text-xs">
                                {countryFlag(row.country_code)} {row.city !== 'Unknown' ? `${row.city}, ` : ''}{row.country_name || '—'}
                            </td>
                            <td className="py-3 pr-4 text-xs text-slate-500">{row.isp || '—'}</td>
                            <td className="py-3">
                                {row.is_flagged ? (
                                    <span
                                        className="px-2 py-0.5 bg-red-100 text-red-800 rounded-full text-xs font-medium flex items-center gap-1">
                      <Flag size={10}/> {row.flag_action}
                    </span>
                                ) : (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 px-2 text-xs text-slate-500 hover:text-red-600"
                                        disabled={flagging === row.ip_address}
                                        onClick={() => handleFlag(row.ip_address)}
                                    >
                                        <Flag size={12} className="mr-1"/> Flag
                                    </Button>
                                )}
                            </td>
                        </tr>
                    ))}
                    </tbody>
                </table>
            </div>
            <Pagination page={page} pages={data.pages} onPage={load}/>
        </>
    );
}
// ─────────────────────────────────────────────
// Main Page Component
// ─────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: SecurityIPLogsPage
 * Path: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SecurityIPLogsPage() {
    const {api} = useAuth();
    const [activeTab, setActiveTab] = useState(0);
    const [stats, setStats] = useState(null);
    const [statsLoading, setStatsLoading] = useState(true);

    // Login Activity state
    const [activityData, setActivityData] = useState(null);
    const [activityLoading, setActivityLoading] = useState(false);
    const [activityPage, setActivityPage] = useState(1);
    const [activityStatus, setActivityStatus] = useState('');
    const [activitySearch, setActivitySearch] = useState('');
    const [showSearchHelp, setShowSearchHelp] = useState(false);

    // Overview cards drill into a pre-filtered view rather than opening a modal:
    // the rows behind the number are what the operator actually wants, and the
    // page already renders them. `focus` scrolls to the matching panel for the
    // cards whose detail lives on the Overview tab itself.
    const [overviewFocus, setOverviewFocus] = useState(null);
    const handleDrillDown = useCallback(({tab, search, focus}) => {
        if (typeof search === 'string') {
            setActivitySearch(search);
            setActivityPage(1);
        }
        setOverviewFocus(focus ?? null);
        setActiveTab(tab);
        if (focus) {
            // Defer so the panel exists before we scroll to it.
            requestAnimationFrame(() => {
                document.getElementById(`overview-${focus}`)?.scrollIntoView({behavior: 'smooth', block: 'start'});
            });
        }
    }, []);

    // Suspicious Events state
    const [suspiciousData, setSuspiciousData] = useState(null);
    const [suspiciousLoading, setSuspiciousLoading] = useState(false);
    const [suspiciousPage, setSuspiciousPage] = useState(1);

    const loadStats = useCallback(async () => {
        setStatsLoading(true);
        try {
            const res = await api.get('/security/stats');
            setStats(res.data);
        } catch {
            toast.error('Failed to load security stats');
        } finally {
            setStatsLoading(false);
        }
    }, [api]);

    const loadActivity = useCallback(async (page = 1) => {
        setActivityLoading(true);
        try {
            const params = new URLSearchParams({page, per_page: 25});
            if (activityStatus) params.append('status', activityStatus);
            if (activitySearch) params.append('search', activitySearch);
            const res = await api.get(`/security/login-attempts?${params}`);
            setActivityData(res.data);
            setActivityPage(page);
        } catch {
            toast.error('Failed to load login activity');
        } finally {
            setActivityLoading(false);
        }
    }, [api, activityStatus, activitySearch]);

    const loadSuspicious = useCallback(async (page = 1) => {
        setSuspiciousLoading(true);
        try {
            const res = await api.get(`/security/suspicious-events?page=${page}&per_page=25`);
            setSuspiciousData(res.data);
            setSuspiciousPage(page);
        } catch {
            toast.error('Failed to load suspicious events');
        } finally {
            setSuspiciousLoading(false);
        }
    }, [api]);

    useEffect(() => {
        loadStats();
    }, [loadStats]);

    useEffect(() => {
        if (activeTab === 1) loadActivity(1);
        if (activeTab === 2) loadSuspicious(1);
    }, [activeTab, loadActivity, loadSuspicious]);

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="p-2.5 rounded-xl bg-red-100">
                        <ShieldAlert size={24} className="text-red-600"/>
                    </div>
                    <div>
                        <h1 className="text-2xl font-black text-slate-900">Security & IP Logs</h1>
                        <p className="text-sm text-slate-500">Monitor login activity, detect threats, and review IP
                            intelligence</p>
                    </div>
                </div>
                <Button variant="outline" size="sm" onClick={loadStats} disabled={statsLoading}>
                    <RefreshCw size={14} className={`mr-2 ${statsLoading ? 'animate-spin' : ''}`}/> Refresh
                </Button>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 p-1 bg-slate-100 rounded-xl w-fit">
                {TABS.map((tab, i) => (
                    <button
                        key={i}
                        onClick={() => setActiveTab(i)}
                        className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
                            activeTab === i
                                ? 'bg-white text-slate-900 shadow-sm'
                                : 'text-slate-500 hover:text-slate-700'
                        }`}
                    >
                        {tab}
                    </button>
                ))}
            </div>

            {/* Tab Content */}
            {activeTab === 0 && (
                <OverviewTab stats={stats} loading={statsLoading} onDrillDown={handleDrillDown}/>
            )}

            {activeTab === 1 && (
                <Card className="border-none shadow-md">
                    <CardHeader>
                        <div className="flex flex-col md:flex-row gap-3">
                            <div className="relative flex-1">
                                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"/>
                                <Input
                                    placeholder="Search — try  ip!=192.0.2.1  or  -device:api"
                                    className="pl-8 pr-9"
                                    value={activitySearch}
                                    onChange={e => setActivitySearch(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && loadActivity(1)}
                                    data-testid="activity-search"
                                    aria-describedby="activity-search-help"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowSearchHelp(v => !v)}
                                    aria-expanded={showSearchHelp}
                                    aria-controls="activity-search-help"
                                    title="Search syntax"
                                    data-testid="activity-search-help-toggle"
                                    className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-600 text-xs font-bold"
                                >?</button>
                            </div>
                            {showSearchHelp && activityData?.search_help && (
                                <div
                                    id="activity-search-help"
                                    data-testid="activity-search-help"
                                    className="absolute z-20 mt-11 w-full md:w-[34rem] rounded-xl border border-slate-200 bg-white p-4 shadow-xl"
                                >
                                    <p className="text-xs font-semibold text-slate-900">
                                        {activityData.search_help.summary}
                                    </p>
                                    <div className="mt-3 grid gap-1">
                                        {activityData.search_help.examples.map((ex, i) => (
                                            <button
                                                key={i}
                                                type="button"
                                                onClick={() => {
                                                    setActivitySearch(ex.query);
                                                    setShowSearchHelp(false);
                                                    loadActivity(1);
                                                }}
                                                className="flex items-baseline gap-2 rounded px-2 py-1 text-left hover:bg-slate-50"
                                            >
                                                <code className="font-mono text-xs text-indigo-700 whitespace-nowrap">{ex.query}</code>
                                                <span className="text-xs text-slate-500">{ex.means}</span>
                                            </button>
                                        ))}
                                    </div>
                                    <p className="mt-3 text-[11px] text-slate-400">
                                        Fields: {activityData.search_help.fields.join(', ')}
                                    </p>
                                </div>
                            )}
                            <select
                                className="border rounded-lg px-3 py-2 text-sm text-slate-700"
                                value={activityStatus}
                                onChange={e => {
                                    setActivityStatus(e.target.value);
                                }}
                            >
                                <option value="">All Statuses</option>
                                <option value="success">Success</option>
                                <option value="failed">Failed</option>
                                <option value="deactivated">Deactivated</option>
                            </select>
                            <Button onClick={() => loadActivity(1)} size="sm">
                                <Search size={14} className="mr-2"/> Search
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {activityData?.unknown_search_fields?.length > 0 && (
                            <div role="alert" data-testid="unknown-search-fields"
                                 className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                                Unknown search field(s): <strong>{activityData.unknown_search_fields.join(', ')}</strong>.
                                Those terms were ignored — press ? for the list of valid fields.
                            </div>
                        )}
                        <LoginTable
                            items={activityData?.items || []}
                            total={activityData?.total || 0}
                            page={activityPage}
                            pages={activityData?.pages || 1}
                            onPage={loadActivity}
                            loading={activityLoading}
                        />
                    </CardContent>
                </Card>
            )}

            {activeTab === 2 && (
                <Card className="border-none shadow-md">
                    <CardHeader>
                        <CardTitle className="text-base font-bold flex items-center gap-2">
                            <AlertTriangle size={16} className="text-orange-500"/>
                            Suspicious Events
                        </CardTitle>
                        <CardDescription>Login events with risk score ≥ 50 (new country, new device, odd
                            hour)</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <LoginTable
                            items={suspiciousData?.items || []}
                            total={suspiciousData?.total || 0}
                            page={suspiciousPage}
                            pages={suspiciousData?.pages || 1}
                            onPage={loadSuspicious}
                            loading={suspiciousLoading}
                        />
                    </CardContent>
                </Card>
            )}

            {activeTab === 3 && (
                <Card className="border-none shadow-md">
                    <CardHeader>
                        <CardTitle className="text-base font-bold flex items-center gap-2">
                            <Globe size={16} className="text-indigo-500"/>
                            IP Intelligence
                        </CardTitle>
                        <CardDescription>Per-IP aggregated stats from the last 30 days. Red rows = 10+ failed
                            attempts.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <IPIntelligenceTab api={api}/>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
