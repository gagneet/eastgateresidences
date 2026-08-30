"use client";
import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Skeleton } from '../ui/skeleton';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger, } from '../ui/tooltip';
import { AlertCircle, Copy, ExternalLink, Info, Phone, Zap } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: InfoRow
 * Path: frontend/src/components/dashboard/ElectricityCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InfoRow = ({label, value, mono = false, copyable = false}) => {
    /**
     * @generated FunctionHeader
     * Function: handleCopy
     * Path: frontend/src/components/dashboard/ElectricityCard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCopy = () => {
        navigator.clipboard.writeText(value);
        toast.success(`Copied: ${value}`);
    };
    return (
        <div className="flex items-center justify-between py-2 border-b border-slate-100 last:border-0 gap-3">
            <span className="text-sm text-slate-500 shrink-0">{label}</span>
            <div className="flex items-center gap-1.5 min-w-0">
                <span className={`text-sm font-medium text-right truncate ${mono ? 'font-mono' : ''}`}>{value}</span>
                {copyable && (
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <button
                                type="button"
                                onClick={handleCopy}
                                className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rounded-sm"
                                aria-label={`Copy ${label}`}
                            >
                                <Copy className="h-3.5 w-3.5"/>
                            </button>
                        </TooltipTrigger>
                        <TooltipContent>
                            <p>Copy {label}</p>
                        </TooltipContent>
                    </Tooltip>
                )}
            </div>
        </div>
    );
};
// ─── Main Component ───────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: ElectricityCard
 * Path: frontend/src/components/dashboard/ElectricityCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ElectricityCard = ({unitNumber: propUnitNumber}) => {
    const {api, selectedBuilding} = useAuth();
    const {activeUnit} = useActiveUnit();
    // Fall back to the sidebar's active unit, not the account default, so a
    // multi-unit owner's switch reaches cards rendered without an explicit prop.
    const unitNumber = propUnitNumber || activeUnit;

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [infoOpen, setInfoOpen] = useState(false);

    const fetchData = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const res = await api.get(`/utilities/${unitNumber}`);
            setData(res.data);
        } catch (err) {
            setError(err.response?.data?.detail || 'Could not load electricity details');
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const elec = data?.electricity;

    if (!unitNumber) return null;

    if (loading) return (
        <Card className="card-dashboard">
            <CardHeader><Skeleton className="h-5 w-40"/></CardHeader>
            <CardContent className="space-y-3">
                {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-8 w-full"/>)}
            </CardContent>
        </Card>
    );

    return (
        <>
            <Card className="card-dashboard">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <div className="w-8 h-8 rounded-lg bg-yellow-100 flex items-center justify-center">
                                <Zap className="h-4 w-4 text-yellow-600"/>
                            </div>
                            <div>
                                <CardTitle className="text-base">Electricity</CardTitle>
                                <CardDescription className="text-xs">Origin Energy — Embedded Network</CardDescription>
                            </div>
                        </div>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon" variant="ghost" className="h-7 w-7"
                                    onClick={() => setInfoOpen(true)}
                                    aria-label="How electricity charges are calculated"
                                    data-testid="electricity-info-trigger"
                                >
                                    <Info className="h-4 w-4 text-muted-foreground"/>
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                                <p>How electricity charges are calculated</p>
                            </TooltipContent>
                        </Tooltip>
                    </div>
                </CardHeader>

                <CardContent className="space-y-0">
                    {error ? (
                        <div className="flex items-center gap-2 text-red-600 text-sm py-4">
                            <AlertCircle className="h-4 w-4 shrink-0"/> {error}
                        </div>
                    ) : elec ? (
                        <>
                            {/* Meter / Account details */}
                            <motion.div
                                role="button"
                                tabIndex={0}
                                className="rounded-xl bg-yellow-50 border border-yellow-100 p-4 mb-4 cursor-pointer hover:bg-yellow-100 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                whileTap={{scale: 0.98}}
                                onClick={() => setInfoOpen(true)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        setInfoOpen(true);
                                    }
                                }}
                                aria-label="View electricity charge breakdown"
                                data-testid="electricity-breakdown-trigger"
                            >
                                <InfoRow label="Supplier" value={elec.supplier}/>
                                <InfoRow label="Distributor" value={elec.distributor}/>
                                <InfoRow label="Meter No. (LOC ID)" value={elec.loc_id || '—'} mono
                                         copyable={!!elec.loc_id}/>
                                <InfoRow label="Supply Charge" value={`$${elec.supply_charge_per_day.toFixed(6)}/day`}/>
                                <InfoRow label="Usage Charge" value={`$${elec.usage_charge_per_unit.toFixed(6)}/unit`}/>
                                <p className="text-xs text-yellow-700 mt-2 flex items-center gap-1">
                                    <Info className="h-3 w-3 shrink-0"/>Click to see how charges are calculated
                                </p>
                            </motion.div>

                            {/* Contacts */}
                            <div className="space-y-2">
                                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Contacts</p>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                    <a
                                        href={`tel:${elec.faults_emergency}`}
                                        className="flex items-center gap-2 p-2.5 rounded-lg border bg-red-50 border-red-100 hover:bg-red-100 transition-colors text-sm"
                                    >
                                        <Phone className="h-4 w-4 text-red-600 shrink-0"/>
                                        <div>
                                            <p className="font-medium text-red-800 text-xs">Faults & Emergencies</p>
                                            <p className="font-mono text-red-700">{elec.faults_emergency}</p>
                                        </div>
                                    </a>
                                    <a
                                        href={`tel:${elec.assistance}`}
                                        className="flex items-center gap-2 p-2.5 rounded-lg border bg-slate-50 border-slate-100 hover:bg-slate-100 transition-colors text-sm"
                                    >
                                        <Phone className="h-4 w-4 text-slate-600 shrink-0"/>
                                        <div>
                                            <p className="font-medium text-slate-700 text-xs">Customer Assistance</p>
                                            <p className="font-mono text-slate-700">{elec.assistance}</p>
                                        </div>
                                    </a>
                                </div>
                                <a
                                    href={elec.my_account_url}
                                    target="_blank" rel="noopener noreferrer"
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-yellow-50 border-yellow-100 hover:bg-yellow-100 transition-colors text-sm w-full"
                                >
                                    <ExternalLink className="h-4 w-4 text-yellow-700 shrink-0"/>
                                    <span
                                        className="text-yellow-800 font-medium">My Account — origin.com.au/myaccount</span>
                                </a>
                            </div>
                        </>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">No electricity data available</p>
                    )}
                </CardContent>
            </Card>

            {/* Breakdown Dialog */}
            <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Zap className="h-5 w-5 text-yellow-500"/>How Electricity Charges Work
                        </DialogTitle>
                        <DialogDescription>Origin Energy embedded network
                            — {selectedBuilding?.name || 'East Gate Residences'}</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 text-sm">
                        <div className="p-3 bg-yellow-50 rounded-lg border border-yellow-100 space-y-2">
                            <p className="font-semibold text-yellow-900 mb-1">Current Rates (Origin Energy)</p>
                            <div className="space-y-1 text-xs">
                                <div className="flex justify-between">
                                    <span>Supply Charge (daily):</span>
                                    <span className="font-mono font-bold">$1.467840 / day</span>
                                </div>
                                <div className="flex justify-between">
                                    <span>Usage Charge:</span>
                                    <span className="font-mono font-bold">$0.256740 / kWh</span>
                                </div>
                            </div>
                        </div>

                        <div className="space-y-1.5 text-sm text-muted-foreground">
                            <p><span className="font-medium text-foreground">Supply Charge</span> — a fixed daily charge
                                for being connected to the electricity network, regardless of usage.</p>
                            <p><span className="font-medium text-foreground">Usage Charge</span> — charged per
                                kilowatt-hour (kWh) consumed. Your bill will vary based on how much electricity you use.
                            </p>
                            <p><span className="font-medium text-foreground">LOC ID (Location Identifier)</span> — your
                                unique embedded network connection point identifier, used by the distributor (Evoenergy)
                                to identify your meter.</p>
                        </div>

                        <div className="p-3 bg-blue-50 border border-blue-100 rounded text-blue-800 text-xs">
                            <p className="font-medium mb-1">Embedded Network</p>
                            <p>{selectedBuilding?.name || 'East Gate Residences'} operates an embedded electricity
                                network. Origin Energy is the embedded network retailer. Evoenergy is the distribution
                                network service provider (DNSP).</p>
                        </div>

                        {elec?.loc_id && (
                            <div className="p-3 bg-slate-50 border rounded text-xs space-y-1">
                                <p className="font-medium">Your Meter Details</p>
                                <div className="flex justify-between">
                                    <span>LOC ID:</span>
                                    <span className="font-mono">{elec.loc_id}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span>Unit:</span>
                                    <span>{unitNumber}</span>
                                </div>
                            </div>
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};

export default ElectricityCard;
