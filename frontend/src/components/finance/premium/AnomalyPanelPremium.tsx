"use client";

import React from "react";
import {AnimatePresence, motion} from "framer-motion";
import {Badge} from "@/components/ui/badge";
import {AlertTriangle, ArrowRight, CheckCircle2, RefreshCw, ShieldAlert} from "lucide-react";
import {useRouter} from "next/navigation";
import InfoButton from "./InfoButton";
import {Tooltip, TooltipContent, TooltipTrigger,} from "@/components/ui/tooltip";

interface Anomaly {
    id: string;
    anomaly_type: string;
    severity: string;
    description: string;
    financial_year: string;
    fund_type: string;
    category?: string;
    amount_impact?: number;
    resolved: boolean;
    created_at: string;
}

interface AnomalyPanelPremiumProps {
    anomalies: Anomaly[];
    year: string;
    canManage: boolean;
    onRescan: () => void;
    onResolve: (id: string) => void;
}

const SEVERITY_CONFIG: Record<string, { bg: string; text: string; icon: any }> = {
    critical: {bg: "bg-rose-50", text: "text-rose-600", icon: ShieldAlert},
    high: {bg: "bg-orange-50", text: "text-orange-600", icon: AlertTriangle},
    medium: {bg: "bg-amber-50", text: "text-amber-600", icon: AlertTriangle},
    low: {bg: "bg-primary/10", text: "text-primary", icon: AlertTriangle},
};
/**
 * @generated FunctionHeader
 * Function: AnomalyPanelPremium
 * Path: frontend/src/components/finance/premium/AnomalyPanelPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const AnomalyPanelPremium = ({
                                        anomalies,
                                        year,
                                        canManage,
                                        onRescan,
                                        onResolve,
                                    }: AnomalyPanelPremiumProps) => {
    const router = useRouter();
    const activeAnomalies = anomalies.filter(a => !a.resolved);

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex justify-between items-start mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Audit Anomalies</h3>
                        <ShieldAlert className="w-5 h-5 text-rose-600"/>
                        <InfoButton
                            title="Audit Anomalies"
                            description="Algorithmic detection of unusual financial activity, potential data entry errors, or significant budget deviations."
                            dataSources={["financial_transactions", "levy_categories", "unit_levy_ledger"]}
                            logic={
                                <div className="space-y-2">
                                    <p>The system periodically scans all records for:</p>
                                    <ul className="list-disc pl-3 space-y-1">
                                        <li><span className="font-bold text-rose-600">Budget Overruns:</span> Actual
                                            spending exceeding budget by &gt;15%.
                                        </li>
                                        <li><span className="font-bold text-rose-600">Unbudgeted Items:</span> New
                                            expense categories with $0 allocation.
                                        </li>
                                        <li><span className="font-bold text-rose-600">Payment Gaps:</span> Unusual
                                            clusters of arrears in specific unit types.
                                        </li>
                                        <li><span
                                            className="font-bold text-rose-600">Predictive Volatility:</span> Spikes
                                            that deviate significantly from 3-year historical patterns.
                                        </li>
                                    </ul>
                                </div>
                            }
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">AI-detected financial irregularities</p>
                </div>

                <div className="flex items-center gap-3">
                    {canManage && (
                        <motion.button
                            onClick={onRescan}
                            whileTap={{scale: 0.95}}
                            className="flex items-center gap-2 px-4 py-2 bg-card/50 hover:bg-card text-foreground rounded-xl text-xs font-bold border border-border transition-all shadow-sm group/rescan"
                        >
                            <RefreshCw size={14} className="group-hover/rescan:animate-spin"/>
                            Re-scan
                        </motion.button>
                    )}
                    <div
                        className={`px-4 py-2 rounded-2xl ${activeAnomalies.length > 0 ? 'bg-rose-50 border border-rose-100' : 'bg-emerald-50 border border-emerald-100'} flex items-center gap-2`}>
                        {activeAnomalies.length > 0 ? (
                            <span className="text-[10px] font-semibold uppercase tracking-widest text-rose-600">
                 {activeAnomalies.length} Flagged
               </span>
                        ) : (
                            <span className="text-[10px] font-semibold uppercase tracking-widest text-emerald-600">
                 System Clean
               </span>
                        )}
                    </div>
                </div>
            </div>

            <div className="flex-1 space-y-3 overflow-auto max-h-[400px] pr-2 custom-scrollbar">
                <AnimatePresence mode="popLayout">
                    {activeAnomalies.length > 0 ? (
                        activeAnomalies.map((anomaly) => {
                            const sev = SEVERITY_CONFIG[anomaly.severity.toLowerCase()] || SEVERITY_CONFIG.medium;
                            const Icon = sev.icon;
                            return (
                                <motion.div
                                    key={anomaly.id}
                                    initial={{opacity: 0, x: -20}}
                                    animate={{opacity: 1, x: 0}}
                                    exit={{opacity: 0, x: 20}}
                                    className={`p-4 rounded-2xl border border-white/40 bg-card/60 hover:bg-card transition-all shadow-sm group/item`}
                                >
                                    <div className="flex justify-between items-start gap-4">
                                        <div className="flex gap-4">
                                            <div className={`mt-1 p-2 rounded-xl ${sev.bg} ${sev.text}`}>
                                                <Icon size={18}/>
                                            </div>
                                            <div>
                                                <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-semibold text-foreground uppercase tracking-tight">
                            {anomaly.anomaly_type.replace(/_/g, ' ')}
                          </span>
                                                    <Badge
                                                        variant="outline"
                                                        className={`border-transparent px-1.5 py-0 text-[8px] font-semibold uppercase tracking-widest ${sev.bg} ${sev.text}`}
                                                    >
                                                        {anomaly.severity}
                                                    </Badge>
                                                </div>
                                                <p className="text-xs text-muted-foreground font-medium leading-relaxed">{anomaly.description}</p>
                                                {anomaly.category && (
                                                    <div className="mt-2 flex gap-2">
                             <span
                                 className="text-[9px] font-semibold text-primary uppercase px-2 py-0.5 bg-primary/10 rounded-lg">
                               {anomaly.category}
                             </span>
                                                        {anomaly.amount_impact && (
                                                            <span
                                                                className="text-[9px] font-semibold text-rose-500 uppercase px-2 py-0.5 bg-rose-50 rounded-lg">
                                 Impact: ${anomaly.amount_impact.toLocaleString()}
                               </span>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        </div>

                                        {canManage && (
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <motion.button
                                                        onClick={() => onResolve(anomaly.id)}
                                                        whileTap={{scale: 0.92}}
                                                        className="opacity-0 group-hover/item:opacity-100 focus-visible:opacity-100 p-2 rounded-lg bg-emerald-50 text-emerald-600 hover:bg-emerald-100 transition-all focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:outline-none"
                                                        aria-label="Resolve Anomaly"
                                                    >
                                                        <CheckCircle2 size={16}/>
                                                    </motion.button>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    <p>Resolve Anomaly</p>
                                                </TooltipContent>
                                            </Tooltip>
                                        )}
                                    </div>
                                </motion.div>
                            );
                        })
                    ) : (
                        <div className="flex flex-col items-center justify-center py-12 text-center">
                            <div
                                className="w-16 h-16 rounded-xl bg-emerald-50 flex items-center justify-center text-emerald-500 mb-4 border border-emerald-100">
                                <CheckCircle2 size={32}/>
                            </div>
                            <h4 className="text-foreground font-semibold">All Audit Checks Passed</h4>
                            <p className="text-muted-foreground text-xs font-medium max-w-[200px] mt-1">No financial
                                irregularities detected for the selected period.</p>
                        </div>
                    )}
                </AnimatePresence>
            </div>

            <button
                onClick={() => router.push('/admin/audit-logs')}
                className="mt-6 flex items-center gap-2 text-[10px] font-semibold text-primary uppercase tracking-widest hover:text-primary transition-colors"
            >
                Full Audit Log
                <ArrowRight size={12}/>
            </button>
        </motion.div>
    );
};

export default AnomalyPanelPremium;
