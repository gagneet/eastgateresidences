"use client";
import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Skeleton } from '../ui/skeleton';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger, } from '../ui/tooltip';
import { AlertCircle, Copy, ExternalLink, Info, Wifi } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: InfoRow
 * Path: frontend/src/components/dashboard/NBNConnectionCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InfoRow = ({label, value, mono = false, copyable = false}) => {
    /**
     * @generated FunctionHeader
     * Function: handleCopy
     * Path: frontend/src/components/dashboard/NBNConnectionCard.jsx
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
 * Function: NBNConnectionCard
 * Path: frontend/src/components/dashboard/NBNConnectionCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const NBNConnectionCard = ({unitNumber: propUnitNumber}) => {
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
            setError(err.response?.data?.detail || 'Could not load NBN details');
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const nbn = data?.nbn;

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
                            <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center">
                                <Wifi className="h-4 w-4 text-blue-600"/>
                            </div>
                            <div>
                                <CardTitle className="text-base">NBN Connection</CardTitle>
                                <CardDescription className="text-xs">National Broadband Network — Fibre to
                                    Premises</CardDescription>
                            </div>
                        </div>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    size="icon" variant="ghost" className="h-7 w-7"
                                    onClick={() => setInfoOpen(true)}
                                    aria-label="NBN connection information"
                                    data-testid="nbn-info-trigger"
                                >
                                    <Info className="h-4 w-4 text-muted-foreground"/>
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                                <p>NBN connection information</p>
                            </TooltipContent>
                        </Tooltip>
                    </div>
                </CardHeader>

                <CardContent className="space-y-0">
                    {error ? (
                        <div className="flex items-center gap-2 text-red-600 text-sm py-4">
                            <AlertCircle className="h-4 w-4 shrink-0"/> {error}
                        </div>
                    ) : nbn ? (
                        <>
                            {/* Connection details */}
                            <motion.div
                                className="rounded-xl bg-blue-50 border border-blue-100 p-4 mb-4 cursor-pointer hover:bg-blue-100 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                onClick={() => setInfoOpen(true)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        setInfoOpen(true);
                                    }
                                }}
                                tabIndex={0}
                                role="button"
                                whileTap={{scale: 0.98}}
                                aria-label="View NBN connection details"
                                data-testid="nbn-breakdown-trigger"
                            >
                                <InfoRow label="Supplier" value={nbn.supplier}/>
                                <InfoRow label="Box No. (NMI)" value={nbn.nmi || '—'} mono copyable={!!nbn.nmi}/>
                                <InfoRow label="Property No." value={nbn.property_number || '—'} mono
                                         copyable={!!nbn.property_number}/>
                                <p className="text-xs text-blue-700 mt-2 flex items-center gap-1">
                                    <Info className="h-3 w-3 shrink-0"/>Click for more information
                                </p>
                            </motion.div>

                            {/* Important note */}
                            <div className="rounded-lg border border-blue-100 bg-blue-50/60 p-3">
                                <p className="text-xs text-blue-800 leading-relaxed">
                                    <span className="font-semibold">Important:</span> The Broadband connectivity is
                                    specific to the Person staying in the Unit.
                                    Contact your chosen retail service provider (RSP) to connect or transfer service.
                                </p>
                            </div>

                            {/* RSP link */}
                            <div className="mt-3">
                                <a
                                    href="https://www.nbn.com.au/residential/service-providers"
                                    target="_blank" rel="noopener noreferrer"
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-blue-50 border-blue-100 hover:bg-blue-100 transition-colors text-sm w-full"
                                >
                                    <ExternalLink className="h-4 w-4 text-blue-700 shrink-0"/>
                                    <span className="text-blue-800 font-medium">Find an RSP — nbn.com.au/service-providers</span>
                                </a>
                            </div>
                        </>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">No NBN data available</p>
                    )}
                </CardContent>
            </Card>

            {/* Info Dialog */}
            <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Wifi className="h-5 w-5 text-blue-500"/>NBN Connection Details
                        </DialogTitle>
                        <DialogDescription>National Broadband Network — {selectedBuilding?.name || 'Your Building'}</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 text-sm">
                        {nbn && (
                            <div className="p-3 bg-blue-50 rounded-lg border border-blue-100 space-y-2">
                                <p className="font-semibold text-blue-900 mb-1">Your Connection Details</p>
                                <div className="space-y-1 text-xs">
                                    <div className="flex justify-between">
                                        <span>Box No. (NMI):</span>
                                        <span className="font-mono font-bold">{nbn.nmi}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span>Property Number:</span>
                                        <span className="font-mono font-bold">{nbn.property_number}</span>
                                    </div>
                                    <div className="flex justify-between">
                                        <span>Unit:</span>
                                        <span className="font-mono font-bold">{unitNumber}</span>
                                    </div>
                                </div>
                            </div>
                        )}

                        <div className="space-y-1.5 text-sm text-muted-foreground">
                            <p><span className="font-medium text-foreground">Box No. (NMI)</span> — Network Metering
                                Identifier, the unique identifier for your NBN connection point at this property.
                                Provide this to your RSP when connecting.</p>
                            <p><span className="font-medium text-foreground">Property Number</span> — The NBN property
                                identifier for your unit, used by nbn co to locate your connection in the network.</p>
                            <p><span className="font-medium text-foreground">Technology Type</span> — {selectedBuilding?.name || 'This building'} uses Fibre to the Premises (FTTP), which provides the fastest and most
                                reliable NBN connection type.</p>
                        </div>

                        <div className="p-3 bg-amber-50 border border-amber-100 rounded text-amber-800 text-xs">
                            <p className="font-medium mb-1">Connectivity is Person-Specific</p>
                            <p>The NBN connection at this address is not automatically transferred between residents.
                                Each occupant must contact their own Retail Service Provider (RSP) to connect, transfer,
                                or cancel a service at this address.</p>
                        </div>

                        <div className="p-3 bg-slate-50 border rounded text-xs space-y-2">
                            <p className="font-medium">How to Connect</p>
                            <ol className="list-decimal list-inside space-y-1 text-slate-700">
                                <li>Choose an RSP (e.g., Aussie Broadband, Superloop, TPG, iiNet, Telstra)</li>
                                <li>Provide your NMI and Property Number when signing up</li>
                                <li>RSP will arrange activation — typically 1–5 business days</li>
                                <li>Existing equipment from a previous plan can usually be reused</li>
                            </ol>
                        </div>

                        <a
                            href="https://www.nbn.com.au/residential/service-providers"
                            target="_blank" rel="noopener noreferrer"
                            className="flex items-center gap-2 p-2.5 rounded-lg border bg-blue-50 border-blue-100 hover:bg-blue-100 transition-colors text-sm w-full"
                        >
                            <ExternalLink className="h-4 w-4 text-blue-700 shrink-0"/>
                            <span
                                className="text-blue-800 font-medium">Find a Retail Service Provider — nbn.com.au</span>
                        </a>
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};

export default NBNConnectionCard;
