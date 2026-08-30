// @featuretrace:by-law-breach-register — Dispute/by-law breach register UI.
// Layer: frontend
// Data flow: /by-law-breach/reports -> this page -> POST reports / status / notice
//            -> by_law_breach_reports -> community_dashboard open_disputes
//            -> Building Pulse dispute axis (building-scoped).
// Related: backend/routers/by_law_breach.py
//          backend/models/by_law_breach.py
//          backend/routers/community_dashboard.py
//          backend/utils/audit_search.py (BREACH_* vocabulary)
"use client";

import React, {useCallback, useEffect, useMemo, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {SortableTh, useTableSort} from "@/components/shared/SortableTableHeader";
import {getApiErrorDetail} from "@/lib/api-error";
import {AlertTriangle, Gavel, HelpCircle, Plus, Scale, ShieldCheck} from "lucide-react";

/** Statuses that count as a live dispute. Mirrors BreachStatus.UNRESOLVED server-side. */
const UNRESOLVED = [
    "reported", "acknowledged", "courtesy_notice_sent", "formal_notice_sent",
    "escalated", "tribunal_referred",
];

const STATUS_LABEL: Record<string, string> = {
    reported: "Reported",
    acknowledged: "Acknowledged",
    courtesy_notice_sent: "Courtesy notice",
    formal_notice_sent: "Formal notice",
    escalated: "Escalated",
    tribunal_referred: "Tribunal",
    resolved: "Resolved",
    withdrawn: "Withdrawn",
};

const STATUS_TONE: Record<string, string> = {
    reported: "bg-amber-50 text-amber-700 ring-amber-200",
    acknowledged: "bg-muted text-muted-foreground ring-border",
    courtesy_notice_sent: "bg-muted text-muted-foreground ring-border",
    formal_notice_sent: "bg-orange-50 text-orange-700 ring-orange-200",
    escalated: "bg-rose-50 text-rose-700 ring-rose-200",
    tribunal_referred: "bg-rose-100 text-rose-800 ring-rose-300",
    resolved: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    withdrawn: "bg-muted text-muted-foreground ring-border",
};

const SEVERITIES = ["minor", "moderate", "major"];

type Report = Record<string, any>;

function StatusPill({status}: {status: string}) {
    const tone = STATUS_TONE[status] || "bg-muted text-muted-foreground ring-border";
    return (
        <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-bold ring-1 ${tone}`}>
            {STATUS_LABEL[status] || status}
        </span>
    );
}

/**
 * A summary tile. Clicking it applies the search that produced the number, so the count
 * and the list can never disagree about what was counted — the tile is a shortcut into
 * the same query, not a second calculation of it.
 */
function SummaryTile({label, value, hint, icon: Icon, onClick, testId}: any) {
    return (
        <button
            type="button"
            onClick={onClick}
            data-testid={testId}
            className="text-left rounded-2xl bg-card ring-1 ring-border p-4 hover:ring-ring hover:shadow-sm transition active:scale-95"
        >
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-muted-foreground">
                <Icon size={13}/> {label}
            </div>
            <div className="text-2xl font-black text-foreground mt-1">
                {value == null ? "—" : value}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">{hint}</div>
        </button>
    );
}

export default function ByLawBreachPage() {
    const {api, user} = useAuth() as any;
    const [reports, setReports] = useState<Report[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [applied, setApplied] = useState("");
    const [unknownFields, setUnknownFields] = useState<string[]>([]);
    const [help, setHelp] = useState<any>(null);
    const [showHelp, setShowHelp] = useState(false);
    const [selected, setSelected] = useState<Report | null>(null);
    const [showCreate, setShowCreate] = useState(false);

    const isManager = ["super_admin", "strata_manager", "ec_member", "strata_admin"]
        .includes(user?.role || "");

    const load = useCallback(async (query: string) => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.get("/by-law-breach/reports", {
                params: query ? {search: query} : {},
            });
            setReports(Array.isArray(res.data) ? res.data : []);
            // The backend reports a mistyped field rather than silently matching
            // everything; surface it, or the user reads "no filter applied" as "no results".
            const hdr = res.headers?.["x-search-unknown-fields"];
            setUnknownFields(hdr ? String(hdr).split(",").filter(Boolean) : []);
        } catch (e: any) {
            setError(getApiErrorDetail(e).message || "Could not load the breach register.");
            setReports([]);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => { load(applied); }, [load, applied]);

    useEffect(() => {
        // Help comes from the parser via the API, so documented syntax cannot drift from
        // what the server actually accepts.
        api.get("/by-law-breach/search-help")
            .then((r: any) => setHelp(r.data))
            .catch(() => setHelp(null));
    }, [api]);

    const counts = useMemo(() => {
        const unresolved = reports.filter(r => UNRESOLVED.includes(r.status)).length;
        return {
            total: reports.length,
            unresolved,
            tribunal: reports.filter(r => r.status === "tribunal_referred").length,
            resolved: reports.filter(r => r.status === "resolved").length,
        };
    }, [reports]);

    const accessors = useMemo(() => ({
        created_at: (r: Report) => r.created_at || "",
        alleged_unit: (r: Report) => r.alleged_unit || "",
    }), []);
    const {sort, toggle, sorted} = useTableSort(reports, {field: "created_at", direction: "desc"}, accessors);

    const runSearch = (q: string) => { setSearch(q); setApplied(q); };

    return (
        <div className="p-6 space-y-5" data-testid="by-law-breach-page">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="text-[10px] font-black uppercase tracking-[0.22em] text-primary">
                        Community
                    </div>
                    <h1 className="text-2xl font-black text-foreground flex items-center gap-2">
                        <Gavel size={22}/> By-law Breaches &amp; Disputes
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Every matter recorded here builds the tribunal-ready evidence trail, and
                        feeds the dispute signal on Building Pulse.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => setShowCreate(true)}
                    data-testid="breach-report-new"
                    className="inline-flex items-center gap-2 rounded-full bg-primary text-primary-foreground px-4 py-2 text-sm font-bold active:scale-95"
                >
                    <Plus size={16}/> Report a breach
                </button>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <SummaryTile label="Unresolved" value={counts.unresolved} icon={AlertTriangle}
                             hint="Live matters, tribunal referrals included"
                             testId="breach-tile-unresolved"
                             onClick={() => runSearch("-status:resolved -status:withdrawn")}/>
                <SummaryTile label="At tribunal" value={counts.tribunal} icon={Scale}
                             hint="Referred to ACAT / NCAT"
                             testId="breach-tile-tribunal"
                             onClick={() => runSearch("status:tribunal_referred")}/>
                <SummaryTile label="Resolved" value={counts.resolved} icon={ShieldCheck}
                             hint="Closed without a tribunal"
                             testId="breach-tile-resolved"
                             onClick={() => runSearch("status:resolved")}/>
                <SummaryTile label="All records" value={counts.total} icon={Gavel}
                             hint="Everything on the register"
                             testId="breach-tile-all"
                             onClick={() => runSearch("")}/>
            </div>

            <div className="rounded-2xl bg-card ring-1 ring-border p-4">
                <div className="flex items-center gap-2">
                    <input
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter") setApplied(search); }}
                        placeholder='Search — try status:escalated or -status:resolved'
                        aria-describedby="breach-search-help"
                        data-testid="breach-search-input"
                        className="flex-1 rounded-full ring-1 ring-border px-4 py-2 text-sm focus:outline-none focus:ring-ring"
                    />
                    <button type="button" onClick={() => setApplied(search)}
                            data-testid="breach-search-submit"
                            className="rounded-full bg-muted px-4 py-2 text-sm font-bold active:scale-95">
                        Search
                    </button>
                    <button type="button" onClick={() => setShowHelp(v => !v)}
                            aria-expanded={showHelp} aria-controls="breach-search-help"
                            data-testid="breach-search-help-toggle"
                            className="rounded-full p-2 text-muted-foreground hover:bg-muted">
                        <HelpCircle size={18}/>
                    </button>
                </div>

                {unknownFields.length > 0 && (
                    <div data-testid="breach-search-unknown"
                         className="mt-3 rounded-xl bg-amber-50 ring-1 ring-amber-200 p-3 text-sm text-amber-800">
                        <strong>{unknownFields.join(", ")}</strong> {unknownFields.length === 1 ? "is not a" : "are not"} searchable
                        {unknownFields.length === 1 ? " field" : " fields"} — that part of your search was ignored, so these
                        results are wider than you asked for.
                    </div>
                )}

                {showHelp && (
                    <div id="breach-search-help" data-testid="breach-search-help"
                         className="mt-3 rounded-xl bg-muted ring-1 ring-border p-4 text-sm">
                        {!help ? <p className="text-muted-foreground">Search help is unavailable.</p> : (
                            <>
                                <p className="text-muted-foreground">{help.summary}</p>
                                <div className="grid md:grid-cols-2 gap-4 mt-3">
                                    <div>
                                        <div className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-1">Examples</div>
                                        <ul className="space-y-1">
                                            {(help.examples || []).map((ex: any) => (
                                                <li key={ex.query}>
                                                    <button type="button" onClick={() => runSearch(ex.query)}
                                                            className="font-mono text-xs bg-card ring-1 ring-border rounded px-1.5 py-0.5 hover:ring-ring">
                                                        {ex.query}
                                                    </button>
                                                    <span className="text-muted-foreground text-xs ml-2">{ex.means}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                    <div>
                                        <div className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-1">Fields</div>
                                        <p className="font-mono text-xs text-muted-foreground">{(help.fields || []).join(", ")}</p>
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>

            <div className="rounded-2xl bg-card ring-1 ring-border overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead className="bg-muted text-muted-foreground">
                        <tr>
                            <SortableTh label="Reported" field="created_at" sort={sort} onSort={toggle}/>
                            <SortableTh label="Unit" field="alleged_unit" sort={sort} onSort={toggle}/>
                            <SortableTh label="By-law" field="by_law_section" sort={sort} onSort={toggle}/>
                            <SortableTh label="Severity" field="severity" sort={sort} onSort={toggle}/>
                            <SortableTh label="Status" field="status" sort={sort} onSort={toggle}/>
                            {/* Free text — genuinely not sortable, so a plain header rather
                                than the sort primitive with its sort props left empty. */}
                            <th className="px-3 py-2 text-left font-bold">Description</th>
                        </tr>
                        </thead>
                        <tbody>
                        {loading && (
                            <tr><td colSpan={6} className="p-6 text-center text-muted-foreground">Loading…</td></tr>
                        )}
                        {!loading && error && (
                            <tr><td colSpan={6} className="p-6 text-center text-rose-600" data-testid="breach-error">{error}</td></tr>
                        )}
                        {!loading && !error && sorted.length === 0 && (
                            <tr>
                                <td colSpan={6} className="p-8 text-center text-muted-foreground" data-testid="breach-empty">
                                    {applied
                                        ? "Nothing on the register matches that search."
                                        : "Nothing on the register yet. Until a matter is recorded here, Building Pulse reports the dispute signal as unavailable rather than assuming there are none."}
                                </td>
                            </tr>
                        )}
                        {!loading && !error && sorted.map((r: Report) => (
                            <tr key={r.id}
                                onClick={() => setSelected(r)}
                                data-testid={`breach-row-${r.id}`}
                                className="border-t border-border hover:bg-muted cursor-pointer">
                                <td className="px-3 py-2 text-muted-foreground">{String(r.created_at || "").slice(0, 10) || "—"}</td>
                                <td className="px-3 py-2 font-bold text-foreground">{r.alleged_unit || "—"}</td>
                                <td className="px-3 py-2 text-muted-foreground">{r.by_law_section || "—"}</td>
                                <td className="px-3 py-2 capitalize text-muted-foreground">{r.severity || "—"}</td>
                                <td className="px-3 py-2"><StatusPill status={r.status}/></td>
                                <td className="px-3 py-2 text-muted-foreground max-w-md truncate">{r.description}</td>
                            </tr>
                        ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {selected && (
                <BreachDetailModal report={selected} isManager={isManager} api={api}
                                   onClose={() => setSelected(null)}
                                   onChanged={() => { setSelected(null); load(applied); }}/>
            )}
            {showCreate && (
                <BreachCreateModal api={api}
                                   onClose={() => setShowCreate(false)}
                                   onCreated={() => { setShowCreate(false); load(applied); }}/>
            )}
        </div>
    );
}

function Modal({title, onClose, children}: any) {
    return (
        <div className="fixed inset-0 z-50 bg-foreground/40 grid place-items-center p-4" role="dialog" aria-modal="true">
            <div className="bg-card rounded-2xl ring-1 ring-border max-w-xl w-full max-h-[85vh] overflow-y-auto">
                <div className="flex items-center justify-between p-4 border-b border-border">
                    <h2 className="font-black text-foreground">{title}</h2>
                    <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground px-2">✕</button>
                </div>
                <div className="p-4">{children}</div>
            </div>
        </div>
    );
}

function BreachDetailModal({report, isManager, api, onClose, onChanged}: any) {
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const [next, setNext] = useState("");
    const [notes, setNotes] = useState("");
    const [target, setTarget] = useState("ACAT");

    const advance = async () => {
        if (!next) return;
        setBusy(true); setErr(null);
        try {
            await api.post(`/by-law-breach/reports/${report.id}/status`, {
                new_status: next,
                notes: notes || undefined,
                // Only meaningful for ESCALATED; the server ignores it otherwise.
                escalation_target: next === "escalated" ? target : undefined,
            });
            onChanged();
        } catch (e: any) {
            setErr(getApiErrorDetail(e).message || "Could not update this matter.");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Modal title={`${report.alleged_unit} — ${STATUS_LABEL[report.status] || report.status}`} onClose={onClose}>
            <div className="space-y-3 text-sm" data-testid="breach-detail">
                <div className="grid grid-cols-2 gap-3">
                    <Field label="Alleged unit" value={report.alleged_unit}/>
                    <Field label="Reported by" value={report.reporter_unit || "—"}/>
                    <Field label="By-law section" value={report.by_law_section || "—"}/>
                    <Field label="Severity" value={report.severity}/>
                    <Field label="Incident date" value={report.incident_date || "—"}/>
                    <Field label="Repeat offence" value={report.is_repeat_offence ? "Yes" : "No"}/>
                </div>
                <div>
                    <div className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Description</div>
                    <p className="text-foreground mt-1">{report.description}</p>
                </div>
                {report.escalation_target && (
                    <Field label="Referred to" value={report.escalation_target}/>
                )}
                {report.resolution_outcome && (
                    <Field label="Outcome" value={report.resolution_outcome}/>
                )}
                {(report.notices || []).length > 0 && (
                    <div>
                        <div className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Notices issued</div>
                        <ul className="mt-1 space-y-1">
                            {report.notices.map((n: any, i: number) => (
                                <li key={i} className="text-muted-foreground">
                                    {n.notice_type} · {String(n.issued_at || "").slice(0, 10)} · {n.delivery_method}
                                </li>
                            ))}
                        </ul>
                    </div>
                )}

                {isManager && (
                    <div className="pt-3 border-t border-border space-y-2">
                        <div className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Advance this matter</div>
                        <select value={next} onChange={e => setNext(e.target.value)}
                                data-testid="breach-status-select"
                                className="w-full rounded-xl ring-1 ring-border px-3 py-2">
                            <option value="">Select a new status…</option>
                            {Object.keys(STATUS_LABEL)
                                .filter(s => s !== report.status)
                                .map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                        </select>
                        {next === "escalated" && (
                            <select value={target} onChange={e => setTarget(e.target.value)}
                                    data-testid="breach-tribunal-select"
                                    className="w-full rounded-xl ring-1 ring-border px-3 py-2">
                                <option value="ACAT">ACAT (ACT)</option>
                                <option value="NCAT">NCAT (NSW)</option>
                            </select>
                        )}
                        <textarea value={notes} onChange={e => setNotes(e.target.value)}
                                  placeholder="Notes for the evidence trail (optional)"
                                  data-testid="breach-status-notes"
                                  className="w-full rounded-xl ring-1 ring-border px-3 py-2" rows={2}/>
                        {err && <p className="text-rose-600" data-testid="breach-detail-error">{err}</p>}
                        <button type="button" onClick={advance} disabled={busy || !next}
                                data-testid="breach-status-submit"
                                className="rounded-full bg-primary text-primary-foreground px-4 py-2 text-sm font-bold disabled:opacity-40 active:scale-95">
                            {busy ? "Saving…" : "Update status"}
                        </button>
                    </div>
                )}
            </div>
        </Modal>
    );
}

function Field({label, value}: {label: string; value: any}) {
    return (
        <div>
            <div className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">{label}</div>
            <div className="text-foreground capitalize">{value ?? "—"}</div>
        </div>
    );
}

function BreachCreateModal({api, onClose, onCreated}: any) {
    const [form, setForm] = useState<Record<string, any>>({
        alleged_unit: "", by_law_section: "", description: "",
        incident_date: "", severity: "minor", is_repeat_offence: false,
    });
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);
    const set = (k: string, v: any) => setForm(f => ({...f, [k]: v}));

    const submit = async () => {
        setBusy(true); setErr(null);
        try {
            await api.post("/by-law-breach/reports", {
                ...form,
                by_law_section: form.by_law_section || undefined,
                incident_date: form.incident_date || undefined,
            });
            onCreated();
        } catch (e: any) {
            setErr(getApiErrorDetail(e).message || "Could not submit this report.");
        } finally {
            setBusy(false);
        }
    };

    const ready = form.alleged_unit.trim() && form.description.trim();

    return (
        <Modal title="Report a by-law breach" onClose={onClose}>
            <div className="space-y-3 text-sm" data-testid="breach-create">
                <Input label="Unit the breach concerns" required value={form.alleged_unit}
                       testId="breach-create-unit" onChange={(v: string) => set("alleged_unit", v)}/>
                <Input label="By-law section (optional)" value={form.by_law_section}
                       testId="breach-create-section" onChange={(v: string) => set("by_law_section", v)}/>
                <div>
                    <label className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">
                        What happened <span className="text-rose-500">*</span>
                    </label>
                    <textarea value={form.description} onChange={e => set("description", e.target.value)}
                              rows={4} data-testid="breach-create-description"
                              className="w-full mt-1 rounded-xl ring-1 ring-border px-3 py-2"/>
                </div>
                <div className="grid grid-cols-2 gap-3">
                    <div>
                        <label className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Incident date</label>
                        <input type="date" value={form.incident_date} data-testid="breach-create-date"
                               onChange={e => set("incident_date", e.target.value)}
                               className="w-full mt-1 rounded-xl ring-1 ring-border px-3 py-2"/>
                    </div>
                    <div>
                        <label className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">Severity</label>
                        <select value={form.severity} onChange={e => set("severity", e.target.value)}
                                data-testid="breach-create-severity"
                                className="w-full mt-1 rounded-xl ring-1 ring-border px-3 py-2 capitalize">
                            {SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                    </div>
                </div>
                <label className="flex items-center gap-2 text-muted-foreground">
                    <input type="checkbox" checked={form.is_repeat_offence}
                           data-testid="breach-create-repeat"
                           onChange={e => set("is_repeat_offence", e.target.checked)}/>
                    This has happened before
                </label>
                {err && <p className="text-rose-600" data-testid="breach-create-error">{err}</p>}
                <button type="button" onClick={submit} disabled={busy || !ready}
                        data-testid="breach-create-submit"
                        className="rounded-full bg-primary text-primary-foreground px-4 py-2 text-sm font-bold disabled:opacity-40 active:scale-95">
                    {busy ? "Submitting…" : "Submit report"}
                </button>
            </div>
        </Modal>
    );
}

function Input({label, value, onChange, required, testId}: any) {
    return (
        <div>
            <label className="text-[10px] font-black uppercase tracking-widest text-muted-foreground">
                {label} {required && <span className="text-rose-500">*</span>}
            </label>
            <input value={value} onChange={e => onChange(e.target.value)} data-testid={testId}
                   className="w-full mt-1 rounded-xl ring-1 ring-border px-3 py-2"/>
        </div>
    );
}
