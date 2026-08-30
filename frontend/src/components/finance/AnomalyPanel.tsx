"use client";
import React, {useState} from "react";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Badge} from "@/components/ui/badge";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from "@/components/ui/select";
import {AlertTriangle, CheckCircle, RefreshCw} from "lucide-react";
import {toast} from "sonner";

interface Anomaly {
    id: string;
    financial_year: string;
    fund_type?: string;
    category?: string;
    lot_number?: string;
    anomaly_type: string;
    severity: string;
    deviation_pct?: number;
    expected_value?: number;
    actual_value?: number;
    description: string;
    detected_at: string;
    resolved: boolean;
}

interface AnomalyPanelProps {
    anomalies: Anomaly[];
    year: string;
    canManage: boolean;
    onRescan: () => Promise<void>;
    onResolve: (id: string) => Promise<void>;
}

const SEVERITY_CONFIG: Record<string, { bg: string; text: string; border: string; label: string }> = {
    critical: {bg: "bg-red-50", text: "text-red-700", border: "border-red-200", label: "CRITICAL"},
    high: {bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200", label: "HIGH"},
    medium: {bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200", label: "MEDIUM"},
    low: {bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200", label: "LOW"},
};

const TYPE_LABELS: Record<string, string> = {
    budget_overrun: "Budget Overrun",
    unbudgeted: "Unbudgeted",
    expense_spike: "Expense Spike",
    owner_arrears: "Owner Arrears",
    interest_spike: "Interest Spike",
    forecasted_deficit: "Forecasted Deficit",
    cashflow_risk: "Cashflow Risk",
};
/**
 * @generated FunctionHeader
 * Function: AnomalyPanel
 * Path: frontend/src/components/finance/AnomalyPanel.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const AnomalyPanel: React.FC<AnomalyPanelProps> = ({
                                                              anomalies, year, canManage, onRescan, onResolve,
                                                          }) => {
    const [filterSeverity, setFilterSeverity] = useState<string>("all");
    const [filterType, setFilterType] = useState<string>("all");
    const [scanning, setScanning] = useState(false);
    const [resolvingId, setResolvingId] = useState<string | null>(null);

    const filtered = anomalies.filter((a) => {
        if (filterSeverity !== "all" && a.severity !== filterSeverity) return false;
        if (filterType !== "all" && a.anomaly_type !== filterType) return false;
        return true;
    });

    const counts = {
        critical: anomalies.filter((a) => a.severity === "critical").length,
        high: anomalies.filter((a) => a.severity === "high").length,
        medium: anomalies.filter((a) => a.severity === "medium").length,
        low: anomalies.filter((a) => a.severity === "low").length,
    };
    /**
     * @generated FunctionHeader
     * Function: handleRescan
     * Path: frontend/src/components/finance/AnomalyPanel.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRescan = async () => {
        setScanning(true);
        try {
            await onRescan();
            toast.success("Anomaly scan complete");
        } catch {
            toast.error("Scan failed");
        } finally {
            setScanning(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleResolve
     * Path: frontend/src/components/finance/AnomalyPanel.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleResolve = async (id: string) => {
        setResolvingId(id);
        try {
            await onResolve(id);
            toast.success("Anomaly marked as resolved");
        } catch {
            toast.error("Failed to resolve");
        } finally {
            setResolvingId(null);
        }
    };

    return (
        <Card data-testid="anomaly-panel">
            <CardHeader>
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <AlertTriangle className="w-5 h-5 text-orange-500"/>
                            Financial Anomalies
                        </CardTitle>
                        <CardDescription>{filtered.length} anomalie(s) for {year}</CardDescription>
                    </div>
                    {canManage && (
                        <Button size="sm" variant="outline" onClick={handleRescan} disabled={scanning}>
                            <RefreshCw className={`w-4 h-4 mr-1 ${scanning ? "animate-spin" : ""}`}/>
                            {scanning ? "Scanning…" : "Rescan"}
                        </Button>
                    )}
                </div>
                {/* Severity badges */}
                <div className="flex gap-2 flex-wrap mt-2">
                    {(["critical", "high", "medium", "low"] as const).map((sev) => {
                        const cfg = SEVERITY_CONFIG[sev];
                        return counts[sev] > 0 ? (
                            <span
                                key={sev}
                                className={`px-2 py-0.5 rounded text-xs font-semibold cursor-pointer ${cfg.bg} ${cfg.text} ${cfg.border} border`}
                                onClick={() => setFilterSeverity(filterSeverity === sev ? "all" : sev)}
                            >
                {cfg.label} ({counts[sev]})
              </span>
                        ) : null;
                    })}
                </div>
            </CardHeader>
            <CardContent>
                <div className="flex gap-2 mb-4 flex-wrap">
                    <Select value={filterSeverity} onValueChange={setFilterSeverity}>
                        <SelectTrigger className="w-36 h-8 text-xs">
                            <SelectValue placeholder="Severity"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Severities</SelectItem>
                            <SelectItem value="critical">Critical</SelectItem>
                            <SelectItem value="high">High</SelectItem>
                            <SelectItem value="medium">Medium</SelectItem>
                            <SelectItem value="low">Low</SelectItem>
                        </SelectContent>
                    </Select>
                    <Select value={filterType} onValueChange={setFilterType}>
                        <SelectTrigger className="w-44 h-8 text-xs">
                            <SelectValue placeholder="Type"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Types</SelectItem>
                            {Object.entries(TYPE_LABELS).map(([k, v]) => (
                                <SelectItem key={k} value={k}>{v}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                {filtered.length === 0 ? (
                    <div className="text-center py-8 text-gray-400">
                        <CheckCircle className="w-10 h-10 mx-auto mb-2 text-emerald-400"/>
                        <div className="text-sm">No anomalies match the current filters</div>
                    </div>
                ) : (
                    <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                        {filtered.map((a) => {
                            const cfg = SEVERITY_CONFIG[a.severity] || SEVERITY_CONFIG.low;
                            return (
                                <div
                                    key={a.id}
                                    className={`rounded-lg border p-3 ${cfg.bg} ${cfg.border}`}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap mb-1">
                                                <span className={`text-xs font-bold ${cfg.text}`}>{cfg.label}</span>
                                                <Badge variant="outline" className="text-xs">
                                                    {TYPE_LABELS[a.anomaly_type] || a.anomaly_type}
                                                </Badge>
                                                {a.fund_type && (
                                                    <Badge variant="secondary" className="text-xs capitalize">
                                                        {a.fund_type}
                                                    </Badge>
                                                )}
                                                {a.lot_number && (
                                                    <Badge variant="secondary" className="text-xs">
                                                        Unit {a.lot_number}
                                                    </Badge>
                                                )}
                                            </div>
                                            <p className="text-xs text-gray-700">{a.description}</p>
                                            {a.deviation_pct != null && (
                                                <p className="text-xs text-gray-500 mt-1">
                                                    Deviation: {a.deviation_pct.toFixed(1)}%
                                                    {a.expected_value != null && ` | Expected: $${a.expected_value.toLocaleString("en-AU", {maximumFractionDigits: 2})}`}
                                                    {a.actual_value != null && ` | Actual: $${a.actual_value.toLocaleString("en-AU", {maximumFractionDigits: 2})}`}
                                                </p>
                                            )}
                                        </div>
                                        {canManage && !a.resolved && (
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="shrink-0 h-7 text-xs"
                                                disabled={resolvingId === a.id}
                                                onClick={() => handleResolve(a.id)}
                                            >
                                                <CheckCircle className="w-3 h-3 mr-1"/>
                                                Resolve
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default AnomalyPanel;
