// @featuretrace:safety-feed — Building safety events log for residents and managers.
// Layer: frontend
// Data flow: GET/POST /safety/events, PATCH /safety/events/{id}/resolve
//            -> safety_events (MongoDB, building-scoped) -> this page.
// Related: backend/routers/safety.py
//          backend/seeds/feature_toggles.py (safety_feed)
// Tests: frontend/src/pages/dashboard/__tests__/SafetyFeedPage.test.tsx

"use client";

import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {FeatureDisabled} from "@/components/shared/RecoveryStates";
import {
    SortableTh as SortableThJs,
    useTableSort as useTableSortJs,
} from "@/components/shared/SortableTableHeader";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Button} from "@/components/ui/button";
import {Badge} from "@/components/ui/badge";
import {Input} from "@/components/ui/input";
import {Skeleton} from "@/components/ui/skeleton";
import {PageHeader} from "@/components/shared/PageHeader";
import {AlertTriangle, RefreshCw, Shield, ShieldCheck} from "lucide-react";
import {toast} from "sonner";

// SortableTableHeader is plain .jsx typed only by JSDoc, and this is its first
// .tsx consumer — TS cannot infer the prop object from those annotations. Typed
// locally rather than via a co-located .d.ts, which breaks Turbopack resolution
// for a first-party .jsx file.
interface SortableThProps {
    label: string;
    field: string;
    sort: { field: string | null; direction: "asc" | "desc" };
    onSort: (field: string) => void;
    align?: "left" | "right" | "center";
    className?: string;
}

const SortableTh = SortableThJs as unknown as React.FC<SortableThProps>;

const useTableSort = useTableSortJs as unknown as <T>(
    rows: T[],
    initial?: SortableThProps["sort"],
    accessors?: Record<string, (row: T) => unknown>,
) => { sorted: T[]; sort: SortableThProps["sort"]; toggle: (field: string) => void };

/** Severity -> badge styling. `info` is the default the backend applies. */
const SEVERITY_STYLES: Record<string, string> = {
    major: "bg-red-50 text-red-700 ring-1 ring-red-200",
    minor: "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
    info: "bg-muted text-muted-foreground ring-1 ring-ring",
};

/** Mirrors the regex-constrained `event_type` values in backend/routers/safety.py. */
const EVENT_TYPE_LABELS: Record<string, string> = {
    access_incident: "Access incident",
    noise_complaint: "Noise complaint",
    property_damage: "Property damage",
    suspicious_activity: "Suspicious activity",
    fire_alarm: "Fire alarm",
    lift_fault: "Lift fault",
    water_leak: "Water leak",
};

interface SafetyEvent {
    id: string;
    event_type: string;
    severity: string;
    location: string;
    description: string;
    visibility: string;
    is_resolved: boolean;
    resolved_at: string | null;
    created_at: string;
}

export default function SafetyFeedPage() {
    const {api, hasFeatureAccess, isManager, isECMember, loading: authLoading} = useAuth() as any;

    const [events, setEvents] = useState<SafetyEvent[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [refreshing, setRefreshing] = useState(false);
    const [query, setQuery] = useState("");
    const [resolvingId, setResolvingId] = useState<string | null>(null);

    // Managers and EC members may log and resolve events; everyone else reads.
    const canManage = Boolean(isManager?.() || isECMember?.());

    const load = useCallback(async () => {
        setRefreshing(true);
        setError(null);
        try {
            const res = await api.get("/safety/events");
            setEvents(Array.isArray(res.data) ? res.data : []);
        } catch (err: any) {
            // An empty feed and an unreachable feed are different states and must
            // not both render as "no events" — that is the missing-vs-zero rule.
            setError(err?.response?.data?.detail || "Could not load the safety feed.");
            setEvents(null);
        } finally {
            setRefreshing(false);
        }
    }, [api]);

    // The initial fetch must happen exactly once per mount, so neither
    // `hasFeatureAccess` nor `load` may drive this effect. Both change identity
    // whenever AuthContext re-renders (the feature map reloading is enough), and
    // a re-fired fetch overwrites the previous result — including replacing a
    // recorded load FAILURE with a later success, which would make an unreachable
    // safety log look like an empty one. On a safety feed those must stay
    // distinguishable, so both are read through refs and the effect depends only
    // on auth having resolved. Manual refresh goes through the button.
    const featureGateRef = useRef(hasFeatureAccess);
    featureGateRef.current = hasFeatureAccess;
    const loadRef = useRef(load);
    loadRef.current = load;
    const hasLoadedRef = useRef(false);

    useEffect(() => {
        if (authLoading || hasLoadedRef.current) return;
        if (!featureGateRef.current("safety_feed")) return;
        hasLoadedRef.current = true;
        loadRef.current();
    }, [authLoading]);

    const resolve = async (id: string) => {
        setResolvingId(id);
        try {
            await api.patch(`/safety/events/${id}/resolve`);
            toast.success("Event marked resolved");
            await load();
        } catch (err: any) {
            toast.error(err?.response?.data?.detail || "Could not resolve that event");
        } finally {
            setResolvingId(null);
        }
    };

    const filtered = useMemo(() => {
        const rows = events ?? [];
        const q = query.trim().toLowerCase();
        if (!q) return rows;
        return rows.filter((e) =>
            [EVENT_TYPE_LABELS[e.event_type] ?? e.event_type, e.severity, e.location, e.description]
                .join(" ")
                .toLowerCase()
                .includes(q),
        );
    }, [events, query]);

    const {sorted, sort, toggle} = useTableSort(filtered, {field: "created_at", direction: "desc"});

    if (authLoading) return null;
    if (!hasFeatureAccess("safety_feed")) return <FeatureDisabled featureKey="safety_feed"/>;

    const openCount = (events ?? []).filter((e) => !e.is_resolved).length;

    return (
        <div className="space-y-6" data-testid="safety-feed-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <PageHeader
                    className="flex-1 border-b-0 pb-0"
                    title="Building Safety Feed"
                    icon={<Shield className="h-5 w-5"/>}
                    description={
                        <>
                            Safety events logged for this building
                            {events !== null && ` — ${openCount} open of ${events.length}`}
                        </>
                    }
                />
                <Button variant="outline" onClick={load} disabled={refreshing} data-testid="safety-feed-refresh">
                    <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}/>
                    Refresh
                </Button>
            </div>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base">Events</CardTitle>
                    <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Search events by type, severity, location or description"
                        className="mt-2 max-w-md"
                        data-testid="safety-feed-search"
                    />
                </CardHeader>
                <CardContent>
                    {events === null && error && (
                        <div
                            className="flex items-start gap-3 rounded-lg bg-red-50 ring-1 ring-red-200 p-4 text-sm text-red-700"
                            data-testid="safety-feed-error">
                            <AlertTriangle className="h-5 w-5 shrink-0"/>
                            <div>
                                <p className="font-semibold">The safety feed could not be loaded.</p>
                                <p className="mt-0.5">{error}</p>
                            </div>
                        </div>
                    )}

                    {events === null && !error && (
                        <div className="space-y-2" data-testid="safety-feed-loading">
                            <Skeleton className="h-10 w-full"/>
                            <Skeleton className="h-10 w-full"/>
                            <Skeleton className="h-10 w-full"/>
                        </div>
                    )}

                    {events !== null && sorted.length === 0 && (
                        <div className="py-10 text-center" data-testid="safety-feed-empty">
                            <ShieldCheck className="mx-auto h-10 w-10 text-emerald-500"/>
                            <p className="mt-2 font-semibold text-foreground">
                                {events.length === 0 ? "No safety events recorded" : "No events match that search"}
                            </p>
                            <p className="text-sm text-muted-foreground">
                                {events.length === 0
                                    ? "Nothing has been logged for this building yet."
                                    : "Try a different search term."}
                            </p>
                        </div>
                    )}

                    {events !== null && sorted.length > 0 && (
                        <div className="overflow-x-auto">
                            <Table data-testid="safety-feed-table">
                                <TableHeader>
                                <TableRow className="border-b">
                                    <SortableTh label="Logged" field="created_at" sort={sort} onSort={toggle}/>
                                    <SortableTh label="Type" field="event_type" sort={sort} onSort={toggle}/>
                                    <SortableTh label="Severity" field="severity" sort={sort} onSort={toggle}/>
                                    <SortableTh label="Location" field="location" sort={sort} onSort={toggle}/>
                                    <SortableTh label="Description" field="description" sort={sort} onSort={toggle}/>
                                    <SortableTh label="Status" field="is_resolved" sort={sort} onSort={toggle}/>
                                    {canManage && <TableHead className="py-2 px-3 text-right font-semibold">Action</TableHead>}
                                </TableRow>
                                </TableHeader>
                                <TableBody>
                                {sorted.map((e: SafetyEvent) => (
                                    <TableRow key={e.id} className="border-b last:border-0 hover:bg-muted">
                                        <TableCell className="py-2 px-3 whitespace-nowrap text-muted-foreground">
                                            {e.created_at
                                                ? new Date(e.created_at).toLocaleString("en-AU", {
                                                    day: "2-digit", month: "short", year: "numeric",
                                                    hour: "2-digit", minute: "2-digit",
                                                })
                                                : "—"}
                                        </TableCell>
                                        <TableCell className="py-2 px-3 font-medium">
                                            {EVENT_TYPE_LABELS[e.event_type] ?? e.event_type}
                                        </TableCell>
                                        <TableCell className="py-2 px-3">
                                            <span
                                                className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${SEVERITY_STYLES[e.severity] ?? SEVERITY_STYLES.info}`}>
                                                {e.severity}
                                            </span>
                                        </TableCell>
                                        <TableCell className="py-2 px-3">{e.location || "—"}</TableCell>
                                        <TableCell className="py-2 px-3 max-w-md">{e.description || "—"}</TableCell>
                                        <TableCell className="py-2 px-3">
                                            <Badge variant={e.is_resolved ? "secondary" : "destructive"}>
                                                {e.is_resolved ? "Resolved" : "Open"}
                                            </Badge>
                                        </TableCell>
                                        {canManage && (
                                            <TableCell className="py-2 px-3 text-right">
                                                {!e.is_resolved && (
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        disabled={resolvingId === e.id}
                                                        onClick={() => resolve(e.id)}
                                                        data-testid={`safety-resolve-${e.id}`}
                                                    >
                                                        {resolvingId === e.id ? "Resolving…" : "Resolve"}
                                                    </Button>
                                                )}
                                            </TableCell>
                                        )}
                                    </TableRow>
                                ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
