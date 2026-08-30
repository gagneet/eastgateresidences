"use client";
import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Skeleton } from '../ui/skeleton';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger, } from '../ui/tooltip';
import { AlertCircle, Copy, ExternalLink, Flame, Info, Phone, Zap } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: InfoRow
 * Path: frontend/src/components/dashboard/GasCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InfoRow = ({label, value, mono = false, copyable = false}) => {
    /**
     * @generated FunctionHeader
     * Function: handleCopy
     * Path: frontend/src/components/dashboard/GasCard.jsx
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
 * Function: GasCard
 * Path: frontend/src/components/dashboard/GasCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const GasCard = ({unitNumber: propUnitNumber}) => {
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
            setError(err.response?.data?.detail || 'Could not load gas details');
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const gas = data?.gas;
    const hasGas = gas?.has_gas;

    if (!unitNumber) return null;

    if (loading) return (
        <Card className="card-dashboard">
            <CardHeader><Skeleton className="h-5 w-40"/></CardHeader>
            <CardContent className="space-y-3">
                {[1, 2, 3].map(i => <Skeleton key={i} className="h-8 w-full"/>)}
            </CardContent>
        </Card>
    );

    return (
        <>
            <Card className="card-dashboard">
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <div
                                className={`w-8 h-8 rounded-lg flex items-center justify-center ${hasGas ? 'bg-orange-100' : 'bg-slate-100'}`}>
                                {hasGas
                                    ? <Flame className="h-4 w-4 text-orange-600"/>
                                    : <Zap className="h-4 w-4 text-slate-500"/>
                                }
                            </div>
                            <div>
                                <CardTitle className="text-base flex items-center gap-2">
                                    Cooktop & Gas
                                    <Badge variant={hasGas ? 'default' : 'secondary'} className="text-xs">
                                        {hasGas ? 'Gas' : 'Induction'}
                                    </Badge>
                                </CardTitle>
                                <CardDescription className="text-xs">
                                    {hasGas ? 'Origin Gas — Natural Gas Connection' : 'Electric induction cooktop — no gas supply'}
                                </CardDescription>
                            </div>
                        </div>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon" variant="ghost" className="h-7 w-7"
                                    onClick={() => setInfoOpen(true)}
                                    aria-label="Gas & cooktop information"
                                    data-testid="gas-info-trigger"
                                >
                                    <Info className="h-4 w-4 text-muted-foreground"/>
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                                <p>Gas & cooktop information</p>
                            </TooltipContent>
                        </Tooltip>
                    </div>
                </CardHeader>

                <CardContent className="space-y-0">
                    {error ? (
                        <div className="flex items-center gap-2 text-red-600 text-sm py-4">
                            <AlertCircle className="h-4 w-4 shrink-0"/> {error}
                        </div>
                    ) : gas ? (
                        <>
                            <motion.div
                                role="button"
                                tabIndex={0}
                                className={`rounded-xl border p-4 mb-4 cursor-pointer transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${hasGas ? 'bg-orange-50 border-orange-100 hover:bg-orange-100' : 'bg-slate-50 border-slate-100 hover:bg-slate-100'}`}
                                whileTap={{scale: 0.98}}
                                onClick={() => setInfoOpen(true)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        setInfoOpen(true);
                                    }
                                }}
                                aria-label="View gas charge information"
                                data-testid="gas-breakdown-trigger"
                            >
                                <InfoRow label="Cooktop" value={gas.cooktop_type}/>
                                <InfoRow label="Supplier" value={gas.supplier}/>
                                {hasGas ? (
                                    <>
                                        <InfoRow label="Meter No." value={gas.meter_number || '—'} mono
                                                 copyable={!!gas.meter_number}/>
                                        <InfoRow label="Supply Charge"
                                                 value={`$${gas.supply_charge_per_day.toFixed(6)}/day`}/>
                                        <InfoRow label="Usage (first 3,863 MJ)"
                                                 value={`$${gas.usage_charge_first_3863.toFixed(6)}/MJ`}/>
                                    </>
                                ) : (
                                    <p className="text-xs text-slate-500 mt-2 italic">
                                        This unit uses an induction cooktop. No natural gas supply is connected.
                                    </p>
                                )}
                                <p className={`text-xs mt-2 flex items-center gap-1 ${hasGas ? 'text-orange-700' : 'text-slate-500'}`}>
                                    <Info className="h-3 w-3 shrink-0"/>Click for more information
                                </p>
                            </motion.div>

                            {/* Contacts — shown for all units since Evoenergy handles gas network emergencies */}
                            <div className="space-y-2">
                                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Gas
                                    Network Contacts (Evoenergy)</p>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                    <a
                                        href="tel:1300137078"
                                        className="flex items-center gap-2 p-2.5 rounded-lg border bg-orange-50 border-orange-100 hover:bg-orange-100 transition-colors text-sm"
                                    >
                                        <Phone className="h-4 w-4 text-orange-600 shrink-0"/>
                                        <div>
                                            <p className="font-medium text-orange-800 text-xs">Faults (Evoenergy)</p>
                                            <p className="font-mono text-orange-700">1300 137 078</p>
                                        </div>
                                    </a>
                                    <a
                                        href="tel:131909"
                                        className="flex items-center gap-2 p-2.5 rounded-lg border bg-red-50 border-red-100 hover:bg-red-100 transition-colors text-sm"
                                    >
                                        <Phone className="h-4 w-4 text-red-600 shrink-0"/>
                                        <div>
                                            <p className="font-medium text-red-800 text-xs">Emergencies (Evoenergy)</p>
                                            <p className="font-mono text-red-700">13 19 09</p>
                                        </div>
                                    </a>
                                </div>
                                {hasGas && (
                                    <>
                                        <a
                                            href="tel:1300137427"
                                            className="flex items-center gap-2 p-2.5 rounded-lg border bg-slate-50 border-slate-100 hover:bg-slate-100 transition-colors text-sm w-full"
                                        >
                                            <Phone className="h-4 w-4 text-slate-600 shrink-0"/>
                                            <div>
                                                <p className="font-medium text-slate-700 text-xs">Origin Customer
                                                    Assistance</p>
                                                <p className="font-mono text-slate-700">1300 137 427</p>
                                            </div>
                                        </a>
                                        <a
                                            href="https://www.originenergy.com.au/myaccount"
                                            target="_blank" rel="noopener noreferrer"
                                            className="flex items-center gap-2 p-2.5 rounded-lg border bg-orange-50 border-orange-100 hover:bg-orange-100 transition-colors text-sm w-full"
                                        >
                                            <ExternalLink className="h-4 w-4 text-orange-700 shrink-0"/>
                                            <span className="text-orange-800 font-medium">My Account — origin.com.au/myaccount</span>
                                        </a>
                                    </>
                                )}
                            </div>
                        </>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">No gas data available</p>
                    )}
                </CardContent>
            </Card>

            {/* Breakdown Dialog */}
            <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            {hasGas
                                ? <><Flame className="h-5 w-5 text-orange-500"/>Gas Connection Details</>
                                : <><Zap className="h-5 w-5 text-slate-500"/>Cooktop — Induction (No Gas)</>
                            }
                        </DialogTitle>
                        <DialogDescription>
                            {hasGas ? 'Origin Gas — Natural gas supply for this townhouse' : 'This unit has an electric induction cooktop'}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 text-sm">
                        {hasGas ? (
                            <>
                                <div className="p-3 bg-orange-50 rounded-lg border border-orange-100 space-y-2">
                                    <p className="font-semibold text-orange-900 mb-1">Gas Charges (Origin Gas)</p>
                                    <div className="space-y-1 text-xs">
                                        <div className="flex justify-between">
                                            <span>Supply Charge (daily):</span>
                                            <span className="font-mono font-bold">$0.912670 / day</span>
                                        </div>
                                        <div className="flex justify-between">
                                            <span>Usage — First 3,863 MJ:</span>
                                            <span className="font-mono font-bold">$0.054890 / MJ</span>
                                        </div>
                                        <div className="flex justify-between">
                                            <span>Meter No.:</span>
                                            <span className="font-mono font-bold">{gas?.meter_number}</span>
                                        </div>
                                    </div>
                                </div>
                                <div className="space-y-1.5 text-sm text-muted-foreground">
                                    <p><span className="font-medium text-foreground">Supply Charge</span> — fixed daily
                                        charge for maintaining your gas connection.</p>
                                    <p><span className="font-medium text-foreground">Usage Charge</span> — charged per
                                        megajoule (MJ) of gas consumed. Only townhouses
                                        at {selectedBuilding?.name || 'East Gate'} have natural gas.</p>
                                    <p><span className="font-medium text-foreground">Distributor</span> — Evoenergy
                                        manages the gas network. Call 1300 137 078 for faults or 13 19 09 for
                                        emergencies.</p>
                                </div>
                            </>
                        ) : (
                            <>
                                <div className="p-3 bg-slate-50 rounded-lg border space-y-2 text-sm">
                                    <p><span className="font-semibold">Cooktop type:</span> Induction (electric)</p>
                                    <p><span className="font-semibold">Gas supply:</span> Not connected to this unit</p>
                                    <p><span className="font-semibold">Supplier:</span> Origin Energy (electricity only)
                                    </p>
                                </div>
                                <div className="p-3 bg-blue-50 border border-blue-100 rounded text-blue-800 text-xs">
                                    <p className="font-medium mb-1">Why Induction?</p>
                                    <p>Some units at {selectedBuilding?.name || 'East Gate Residences'} (apartments and
                                        select townhouses) were designed with induction cooktops and do not have a
                                        natural gas connection. Induction cooking uses electricity exclusively.</p>
                                </div>
                            </>
                        )}
                        <div className="p-3 bg-red-50 border border-red-100 rounded text-red-800 text-xs">
                            <p className="font-medium mb-1">Gas Emergency — Smell Gas?</p>
                            <p>Leave the building, do not use switches or phones inside, and call Evoenergy
                                immediately: <span className="font-mono font-bold">13 19 09</span></p>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};

export default GasCard;
