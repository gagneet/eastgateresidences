// @ts-nocheck
"use client";
import React, {useEffect, useState} from "react";
import {Card, CardContent} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {ScrollArea} from "@/components/ui/scroll-area";
import axios from "axios";

const API = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

interface AuditRecord {
    action: string;
    user_email: string;
    user_role: string;
    building_id: string;
    details: Record<string, any>;
    timestamp: string;
}

const ACTION_LABELS: Record<string, { label: string; color: string }> = {
    model_regenerated: {label: "Model Regenerated", color: "bg-blue-100 text-blue-800"},
    snapshot_created: {label: "Snapshot Saved", color: "bg-emerald-100 text-emerald-800"},
    snapshot_restored: {label: "Snapshot Restored", color: "bg-amber-100 text-amber-800"},
    group_created: {label: "Group Created", color: "bg-violet-100 text-violet-800"},
    group_updated: {label: "Group Updated", color: "bg-violet-100 text-violet-800"},
    group_deleted: {label: "Group Deleted", color: "bg-rose-100 text-rose-800"},
    facility_created: {label: "Facility Added", color: "bg-teal-100 text-teal-800"},
    facility_updated: {label: "Facility Updated", color: "bg-teal-100 text-teal-800"},
    facility_deleted: {label: "Facility Deleted", color: "bg-rose-100 text-rose-800"},
    subsidy_map_recalculated: {label: "Subsidy Map Recalculated", color: "bg-blue-100 text-blue-800"},
};
/**
 * @generated FunctionHeader
 * Function: FairnessAuditLog
 * Path: frontend/src/components/levy/FairnessAuditLog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const FairnessAuditLog: React.FC = () => {
    const [records, setRecords] = useState<AuditRecord[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: load
         * Path: frontend/src/components/levy/FairnessAuditLog.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const load = async () => {
            try {
                const token = localStorage.getItem("token");
                const {data} = await axios.get(
                    `${API}/api/intelligence/levy-fairness/audit?limit=100`,
                    {headers: {Authorization: `Bearer ${token}`}}
                );
                setRecords(data || []);
            } catch (e: any) {
                setError(e.response?.data?.detail || "Failed to load audit log");
            } finally {
                setLoading(false);
            }
        };
        load();
    }, []);

    if (loading)
        return (
            <p className="text-sm text-muted-foreground py-8 text-center">Loading audit log…</p>
        );

    if (error) return <p className="text-sm text-rose-600 py-4">{error}</p>;

    if (records.length === 0)
        return (
            <Card>
                <CardContent className="py-12 text-center text-muted-foreground">
                    <p className="text-sm">
                        No audit records yet. Actions like regenerating the model, saving snapshots, and
                        editing groups are logged here.
                    </p>
                </CardContent>
            </Card>
        );

    return (
        <ScrollArea className="h-[500px]">
            <div className="space-y-2 pr-2">
                {records.map((r, i) => {
                    const meta = ACTION_LABELS[r.action] || {
                        label: r.action,
                        color: "bg-slate-100 text-slate-800",
                    };
                    return (
                        <Card key={i} className="border-l-4 border-l-slate-200">
                            <CardContent className="py-3 px-4">
                                <div className="flex items-start justify-between gap-2">
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                      <span
                          className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${meta.color}`}
                      >
                        {meta.label}
                      </span>
                                            <span className="text-xs text-muted-foreground">{r.user_email}</span>
                                            <Badge variant="outline" className="text-xs">
                                                {r.user_role.replace("_", " ")}
                                            </Badge>
                                        </div>
                                        {Object.keys(r.details || {}).length > 0 && (
                                            <p className="text-xs text-muted-foreground mt-1">
                                                {Object.entries(r.details)
                                                    .map(([k, v]) => `${k}: ${v}`)
                                                    .join(" · ")}
                                            </p>
                                        )}
                                    </div>
                                    <span className="text-xs text-muted-foreground shrink-0 whitespace-nowrap">
                    {new Date(r.timestamp).toLocaleString("en-AU", {
                        day: "numeric",
                        month: "short",
                        hour: "2-digit",
                        minute: "2-digit",
                    })}
                  </span>
                                </div>
                            </CardContent>
                        </Card>
                    );
                })}
            </div>
        </ScrollArea>
    );
};
