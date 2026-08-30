// @featuretrace:outbound-message-queue — Operator console for held outgoing messages.
// Layer: frontend
// Data flow: OutboundQueuePage -> /outbound-messages/* -> outbound_messages + db.settings (building-scoped).
// Related: backend/routers/outbound_messages.py
//          backend/services/outbound_queue_service.py
//          tasks/GAP-COMMS-003-outbound-message-queue-and-activation.md

import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {useRouter} from 'next/navigation';
import {AlertTriangle, HelpCircle, Mail, PauseCircle, PlayCircle, RefreshCw, Send, Ban} from 'lucide-react';
import {toast} from 'sonner';

import {useAuth} from '@/contexts/AuthContext';
import {PageHeader} from '@/components/shared/PageHeader';
import {StatTile} from '@/components/shared/StatTile';
import {SortableTh, useTableSort} from '@/components/shared/SortableTableHeader';
import {getApiErrorDetail} from '@/lib/api-error';

const STATUS_TONE = {
    held: 'warning',
    sending: 'default',
    sent: 'good',
    cancelled: 'default',
    expired: 'default',
    failed: 'critical',
};

/**
 * Outbound message queue console.
 *
 * The queue holds every outgoing message for a review window before it is sent. This
 * page is the reason the queue can be switched on at all: without somewhere to see and
 * release held mail, enabling it would strand every message with nobody able to act.
 */
export default function OutboundQueuePage() {
    const {api, isManager, loading: authLoading} = useAuth();
    const router = useRouter();

    const [rows, setRows] = useState([]);
    const [summary, setSummary] = useState(null);
    const [settings, setSettings] = useState(null);
    const [searchHelp, setSearchHelp] = useState(null);
    const [unknownFields, setUnknownFields] = useState([]);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('held');
    const [selected, setSelected] = useState(() => new Set());
    const [loading, setLoading] = useState(true);
    const [showHelp, setShowHelp] = useState(false);

    // Auth guard must check `loading` first, or it fires before the session resolves
    // and bounces every user — including the managers this page exists for.
    useEffect(() => {
        if (authLoading) return;
        if (!isManager()) router.replace('/dashboard');
    }, [authLoading, isManager, router]);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const params = {limit: 200};
            if (statusFilter) params.status = statusFilter;
            if (search.trim()) params.search = search.trim();

            const [list, sum] = await Promise.all([
                api.get('/outbound-messages', {params}),
                api.get('/outbound-messages/summary'),
            ]);
            setRows(list.data?.messages || []);
            setSettings(list.data?.queue_settings || null);
            setSearchHelp(list.data?.search_help || null);
            setUnknownFields(list.data?.unknown_fields || []);
            setSummary(sum.data || null);
            setSelected(new Set());
        } catch (err) {
            toast.error(getApiErrorDetail(err).message || 'Could not load the outbound queue.');
        } finally {
            setLoading(false);
        }
    }, [api, search, statusFilter]);

    useEffect(() => {
        if (!authLoading && isManager()) load();
    }, [authLoading, isManager, load]);

    // Stable accessors object — a literal here would re-sort on every render.
    const accessors = useMemo(() => ({
        to_email: (r) => (r.to_email || '').toLowerCase(),
        subject: (r) => (r.subject || '').toLowerCase(),
        created_at: (r) => r.created_at || '',
        hold_until: (r) => r.hold_until || '',
    }), []);
    const {sort, toggle, sorted} = useTableSort(rows, {field: 'created_at', direction: 'desc'}, accessors);

    const act = async (fn, okMsg) => {
        try {
            const res = await fn();
            // The API reports honestly when a release cannot actually send yet; echo
            // that rather than claiming success the operator did not get.
            if (res?.data?.will_send_next_tick === false && res?.data?.hold_reason) {
                toast(`Hold cleared, but still held: ${res.data.hold_reason}`, {icon: '⏸️'});
            } else {
                toast.success(okMsg);
            }
            await load();
        } catch (err) {
            const {message, metadata} = getApiErrorDetail(err);
            toast.error(message || 'That action could not be completed.');
            if (metadata?.status_now) await load();
        }
    };

    const cancelOne = (id) => act(
        () => api.post(`/outbound-messages/${id}/cancel`, {reason: 'Dropped from the console'}),
        'Message dropped — it will not be sent.');

    const releaseOne = (id) => act(
        () => api.post(`/outbound-messages/${id}/release`),
        'Hold cleared — it sends on the next tick.');

    const bulkCancel = () => act(
        () => api.post('/outbound-messages/bulk-cancel',
            {message_ids: [...selected], reason: 'Bulk drop from the console'}),
        `${selected.size} message(s) dropped.`);

    const toggleQueue = () => act(
        () => api.put('/outbound-messages/settings/queue', {enabled: !settings?.enabled}),
        settings?.enabled ? 'Queue paused — messages will be held.' : 'Queue enabled — held messages will go out.');

    const counts = summary?.counts || {};
    const queueEnabled = settings?.enabled !== false;

    if (authLoading || !isManager()) return null;

    return (
        <div className="space-y-6 p-4 sm:p-6" data-testid="outbound-queue-page">
            <PageHeader
                title="Outgoing Messages"
                description="Every email waits here for a review window before it is sent. Drop anything that should not go out."
                icon={<Mail className="h-6 w-6"/>}
                badges={
                    <span
                        data-testid="queue-state-badge"
                        className={`rounded-full px-3 py-1 text-xs font-semibold ${
                            queueEnabled ? 'bg-green-100 text-green-900' : 'bg-amber-100 text-amber-900'}`}>
                        {queueEnabled ? 'Queue enabled' : 'Queue paused — mail is being held'}
                    </span>
                }
                actions={
                    <div className="flex flex-wrap gap-2">
                        <button onClick={load} data-testid="refresh-queue"
                                className="inline-flex items-center gap-1 rounded-full border px-4 py-1.5 text-xs font-semibold active:scale-95">
                            <RefreshCw className="h-3.5 w-3.5"/> Refresh
                        </button>
                        <button onClick={toggleQueue} data-testid="toggle-queue"
                                className="inline-flex items-center gap-1 rounded-full bg-primary px-4 py-1.5 text-xs font-semibold text-white active:scale-95">
                            {queueEnabled ? <PauseCircle className="h-3.5 w-3.5"/> : <PlayCircle className="h-3.5 w-3.5"/>}
                            {queueEnabled ? 'Pause sending' : 'Enable sending'}
                        </button>
                    </div>
                }
            />

            {!queueEnabled && (
                <div role="alert" className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0"/>
                    <span>
                        Sending is paused for this building. Messages are being held, not discarded —
                        anything still inside its {settings?.expiry_hours ?? 48}-hour window goes out
                        as soon as you enable sending again.
                    </span>
                </div>
            )}

            {/* Every tile filters the table — a figure you cannot act on is decoration. */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {['held', 'sent', 'cancelled', 'failed'].map((key) => (
                    <StatTile
                        key={key}
                        label={key === 'held' ? 'Waiting to send' : key[0].toUpperCase() + key.slice(1)}
                        value={summary ? (counts[key] ?? 0) : '—'}
                        tone={STATUS_TONE[key] || 'default'}
                        loading={loading && !summary}
                        hint={key === 'held' ? `${settings?.hold_seconds ?? 30}s review window` : undefined}
                        onClick={() => setStatusFilter(statusFilter === key ? '' : key)}
                    />
                ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[220px]">
                    <input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && load()}
                        placeholder='Search — try status:held or -category:automated'
                        data-testid="queue-search"
                        className="w-full rounded-full border px-4 py-2 pr-10 text-sm"
                    />
                    <button type="button" onClick={() => setShowHelp((v) => !v)}
                            aria-label="Search syntax help" data-testid="search-help-toggle"
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                        <HelpCircle className="h-4 w-4"/>
                    </button>
                </div>
                {selected.size > 0 && (
                    <button onClick={bulkCancel} data-testid="bulk-cancel"
                            className="inline-flex items-center gap-1 rounded-full bg-red-600 px-4 py-2 text-xs font-semibold text-white active:scale-95">
                        <Ban className="h-3.5 w-3.5"/> Drop {selected.size} selected
                    </button>
                )}
            </div>

            {/* Help is rendered from the parser's own SEARCH_HELP so the documented
                syntax cannot drift from what the backend actually accepts. */}
            {showHelp && searchHelp && (
                <div className="rounded-lg border bg-muted/40 p-4 text-sm" data-testid="search-help-panel">
                    <ul className="space-y-1">
                        {searchHelp.syntax.map((s) => (
                            <li key={s.example} className="flex flex-wrap gap-2">
                                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">{s.example}</code>
                                <span className="text-muted-foreground">{s.means}</span>
                            </li>
                        ))}
                    </ul>
                    <p className="mt-2 text-xs text-muted-foreground">Fields: {searchHelp.fields.join(', ')}</p>
                </div>
            )}

            {unknownFields.length > 0 && (
                <div role="alert" data-testid="unknown-fields-warning"
                     className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    Unknown search field{unknownFields.length > 1 ? 's' : ''}: <strong>{unknownFields.join(', ')}</strong>.
                    Those terms were ignored — the list below is not filtered by them.
                </div>
            )}

            <div className="overflow-x-auto rounded-lg border">
                <table className="w-full min-w-[820px] text-sm">
                    <thead className="bg-muted/50">
                    <tr>
                        <th className="w-10 px-3 py-2"/>
                        <SortableTh label="Recipient" field="to_email" sort={sort} onSort={toggle}/>
                        <SortableTh label="Subject" field="subject" sort={sort} onSort={toggle}/>
                        <SortableTh label="Status" field="status" sort={sort} onSort={toggle}/>
                        <SortableTh label="Queued" field="created_at" sort={sort} onSort={toggle}/>
                        <th className="px-3 py-2 text-right font-medium">Actions</th>
                    </tr>
                    </thead>
                    <tbody>
                    {sorted.map((m) => (
                        <tr key={m.id} className="border-t" data-testid={`queue-row-${m.id}`}>
                            <td className="px-3 py-2">
                                {m.status === 'held' && (
                                    <input type="checkbox" aria-label={`Select message to ${m.to_email}`}
                                           checked={selected.has(m.id)}
                                           onChange={(e) => setSelected((prev) => {
                                               const next = new Set(prev);
                                               e.target.checked ? next.add(m.id) : next.delete(m.id);
                                               return next;
                                           })}/>
                                )}
                            </td>
                            <td className="px-3 py-2">{m.to_email}</td>
                            <td className="px-3 py-2">
                                <span className="block max-w-[280px] truncate">{m.subject || '—'}</span>
                                {m.context && <span className="text-xs text-muted-foreground">{m.context}</span>}
                            </td>
                            <td className="px-3 py-2">
                                <span className="font-medium">{m.status}</span>
                                {/* Say WHICH gate holds it — "pending" alone gives an
                                    operator nothing to act on. */}
                                {m.status === 'held' && m.hold_reason && (
                                    <span className="block text-xs text-muted-foreground">{m.hold_reason}</span>
                                )}
                            </td>
                            <td className="px-3 py-2 text-xs text-muted-foreground">
                                {m.created_at ? new Date(m.created_at).toLocaleString() : '—'}
                            </td>
                            <td className="px-3 py-2 text-right">
                                {m.status === 'held' ? (
                                    <div className="flex justify-end gap-2">
                                        <button onClick={() => releaseOne(m.id)} data-testid={`release-${m.id}`}
                                                className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs active:scale-95">
                                            <Send className="h-3 w-3"/> Send now
                                        </button>
                                        <button onClick={() => cancelOne(m.id)} data-testid={`cancel-${m.id}`}
                                                className="inline-flex items-center gap-1 rounded-full border border-red-300 px-3 py-1 text-xs text-red-700 active:scale-95">
                                            <Ban className="h-3 w-3"/> Drop
                                        </button>
                                    </div>
                                ) : (
                                    <span className="text-xs text-muted-foreground">No action available</span>
                                )}
                            </td>
                        </tr>
                    ))}
                    </tbody>
                </table>

                {/* Missing and zero are different states: say which one this is. */}
                {!loading && sorted.length === 0 && (
                    <p className="p-6 text-center text-sm text-muted-foreground" data-testid="queue-empty">
                        {statusFilter || search
                            ? 'No messages match these filters.'
                            : 'Nothing is queued for this building.'}
                    </p>
                )}
                {loading && (
                    <p className="p-6 text-center text-sm text-muted-foreground">Loading…</p>
                )}
            </div>
        </div>
    );
}
