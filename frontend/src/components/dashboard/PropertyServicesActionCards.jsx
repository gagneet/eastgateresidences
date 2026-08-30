"use client";
/**
 * PropertyServicesActionCards.jsx
 *
 * Six compact action cards for the Owner Dashboard's "Utilities & Property Services"
 * section.  Each card shows only the most important numbers at a glance and opens a
 * focused detail dialog on click.
 *
 * Exported components:
 *   CouncilRateActionCard  – ACT Council Rates (total + current quarter)
 *   LandTaxActionCard      – ACT Land Tax (only rendered if land_tax_applicable)
 *   WaterBillActionCard    – Icon Water (YTD total + next due)
 *   ElectricityActionCard  – Origin Electricity (supplier / charges / meter)
 *   GasActionCard          – Gas or Induction cooktop (supplier / charges / meter)
 *   NBNActionCard          – NBN connection (Box No / Property No)
 */

import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Skeleton } from '../ui/skeleton';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger, } from '../ui/tooltip';
import {
    AlertCircle,
    CheckCircle,
    ChevronRight,
    Clock,
    Copy,
    Droplets,
    ExternalLink,
    Flame,
    Info,
    Landmark,
    Receipt,
    Users,
    Wifi,
    Zap,
} from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
import { useAuth } from '../../contexts/AuthContext';
import { toast } from 'sonner';
// ─── helpers ────────────────────────────────────────────────────────────────

/*
 * WHY THERE IS NO PER-SERVICE COLOUR HERE.
 *
 * A first pass gave each of the six cards its own chip colour from CHART_SERIES,
 * on the reasoning that in a six-card grid the chip is IDENTITY (which tile is
 * water, which is gas) rather than decoration. That reasoning is fine; the
 * execution was not, and it was measured wrong before being measured right.
 *
 * chartTheme's palette is validated for adjacent-pair CVD separation, an OKLCH
 * lightness band and a chroma floor — as chart MARKS against the card surface. It
 * is NOT validated as a thin icon stroke on a tint of itself, which is a different
 * job. Measured contrast of a CHART_SERIES glyph on a 12% tint of the same colour:
 *
 *     teal 2.72:1   terracotta 2.68:1   ochre 2.03:1   green 2.28:1     (all FAIL)
 *     blue 4.21:1   plum 5.01:1                                          (pass)
 *
 * Four of six below the 3:1 floor for non-text contrast, and ochre came out WORSE
 * than the raw `text-yellow-600 on bg-yellow-100` (2.74:1) it was replacing. A
 * solid-fill variant does not rescue it either: white on ochre is 2.22:1.
 *
 * So the chip uses the design system's own pairing, which measures 5.75:1, and
 * identity is carried by the thing that was already carrying it — each card's own
 * lucide glyph and its title. That also survives greyscale and colour-vision
 * deficiency, which a six-hue row never did.
 */

/**
 * Render a served daily supply rate.
 *
 * Missing is not zero: if the rate endpoint did not answer, this returns an em dash,
 * never "$0.0000 / day", which reads as a real free-water rate. The rates themselves
 * come from GET /water-bills/quarterly-estimates — they are ACT regulated figures
 * with an effective period, and three frontend files used to carry their own typed
 * copies of them.
 */
const dailyRate = (value) =>
    typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(4)} / day` : '—';

const copyText = (text) => {
    navigator.clipboard.writeText(text);
    toast.success(`Copied: ${text}`);
};
// ─── Status badge ────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: PayBadge
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PayBadge = ({status}) => {
    if (status === 'paid') return (
        <Badge variant="outline"
               className="text-[10px] font-semibold bg-emerald-50 text-emerald-700 border-emerald-200 flex items-center gap-0.5 px-1.5">
            <CheckCircle size={9}/>Paid
        </Badge>
    );
    if (status === 'partial') return (
        <Badge variant="outline"
               className="text-[10px] font-semibold bg-amber-50 text-amber-700 border-amber-200 flex items-center gap-0.5 px-1.5">
            <Clock size={9}/>Partial
        </Badge>
    );
    return (
        <Badge variant="outline"
               className="text-[10px] font-semibold bg-red-50 text-red-600 border-red-200 flex items-center gap-0.5 px-1.5">
            <AlertCircle size={9}/>Unpaid
        </Badge>
    );
};
// ─── Shared compact card shell ────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: ActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ActionCard = ({
                        icon: Icon, iconMuted = false,
                        title, subtitle,
                        primaryAmount, primaryLabel,
                        secondaryText,
                        badge,
                        onClick,
                        loading = false,
                    }) => {
    if (loading) return (
        <div className="rounded-xl border border-border bg-card p-4 space-y-3">
            <div className="flex items-center gap-3">
                <Skeleton className="w-10 h-10 rounded-xl flex-shrink-0"/>
                <div className="space-y-1.5 flex-1">
                    <Skeleton className="h-4 w-28"/>
                    <Skeleton className="h-3 w-20"/>
                </div>
            </div>
            <Skeleton className="h-6 w-24"/>
            <Skeleton className="h-3 w-40"/>
        </div>
    );

    return (
        <motion.button
            type="button"
            onClick={onClick}
            whileHover={onClick ? {y: -4} : undefined}
            whileTap={onClick ? {scale: 0.98} : undefined}
            className="w-full rounded-xl border border-border bg-card p-4 text-left hover:border-primary/30 hover:bg-primary/5 hover:shadow-md transition-all group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
            <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2.5 min-w-0">
                    {/* `iconMuted` is not styling for its own sake: on the gas card it is
                        the gas/induction distinction. Everywhere else the chip is the
                        brand pair, and the glyph carries which service this is. */}
                    <div
                        className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${
                            iconMuted ? 'bg-muted text-muted-foreground' : 'bg-primary/10 text-primary'
                        }`}
                    >
                        <Icon size={18} aria-hidden="true"/>
                    </div>
                    <div className="min-w-0">
                        <p className="font-bold text-sm text-foreground leading-tight truncate">{title}</p>
                        {subtitle && (
                            <p className="text-[11px] text-muted-foreground mt-0.5 leading-tight truncate">{subtitle}</p>
                        )}
                    </div>
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0 ml-2">
                    {badge}
                    <ChevronRight
                        size={14}
                        className="text-muted-foreground group-hover:text-primary group-focus-visible:text-primary transition-all duration-300 group-hover:translate-x-1 group-focus-visible:translate-x-1"
                    />
                </div>
            </div>

            {primaryAmount && (
                <p className="text-xl font-semibold text-foreground tabular-nums leading-tight">
                    {primaryAmount}
                </p>
            )}
            {primaryLabel && (
                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest mt-0.5">
                    {primaryLabel}
                </p>
            )}
            {secondaryText && (
                <p className="text-xs text-muted-foreground mt-1.5 leading-snug">{secondaryText}</p>
            )}
        </motion.button>
    );
};
// ─── Council Rates Action Card ────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: CouncilRateActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const CouncilRateActionCard = ({unitNumber}) => {
    const {api, selectedBuilding} = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    const fetch = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        const m = new Date().getMonth();
        const y = new Date().getFullYear();
        const fyStart = m >= 6 ? y : y - 1;
        const fy = `${fyStart}-${String(fyStart + 1).slice(2)}`;
        try {
            const res = await api.get(`/council-rates/${unitNumber}?financial_year=${fy}`);
            setData(res.data);
        } catch { /* silent */
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetch();
    }, [fetch]);

    const cq = getCurrentRateQuarter();
    const qAmount = data?.[ cq.key ];

    return (
        <>
            <ActionCard
                icon={Landmark}
                title="ACT Council Rates"
                subtitle={data?.financial_year ? `FY ${data.financial_year}` : 'ACT Revenue Office'}
                primaryAmount={data?.total_rates ? formatCurrency(data.total_rates) : '—'}
                primaryLabel="Annual Total"
                secondaryText={
                    qAmount
                        ? `${cq.label} instalment: ${formatCurrency(qAmount)} · due ${cq.due}`
                        : data?.total_rates
                            ? `${cq.label} instalment: ${formatCurrency(data.total_rates / 4)} · due ${cq.due}`
                            : null
                }
                badge={data ? <PayBadge status={data.payment_status}/> : null}
                onClick={() => setOpen(true)}
                loading={loading}
            />

            {/* Detail Dialog */}
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Landmark size={16} className="text-muted-foreground"/>
                            ACT Council Rates — {unitNumber}
                        </DialogTitle>
                        <DialogDescription>
                            {data?.financial_year ? `FY ${data.financial_year} · ` : ''}ACT Revenue Office
                        </DialogDescription>
                    </DialogHeader>

                    {data ? (
                        <div className="space-y-4 text-sm">
                            {/* Block AUV */}
                            {data.block_auv && (
                                <div className="p-3 bg-primary/5 border border-primary/15 rounded-xl space-y-2">
                                    <p className="text-xs font-bold text-primary uppercase tracking-widest">
                                        Strata Block AUV
                                    </p>
                                    <div className="flex justify-between">
                                        <span className="text-muted-foreground text-xs">Block AUV (Section 75 Block 4)</span>
                                        <span className="font-bold text-foreground tabular-nums">
                      {formatCurrency(data.block_auv)}
                    </span>
                                    </div>
                                    {data.unit_entitlement_pct && (
                                        <div className="flex justify-between">
                                            <span className="text-muted-foreground text-xs">Unit {unitNumber} entitlement</span>
                                            <span
                                                className="font-bold text-foreground">{data.unit_entitlement_pct}%</span>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Rate breakdown */}
                            <div className="rounded-xl bg-muted/60 border border-border divide-y divide-border">
                                {[
                                    {
                                        label: 'Fixed Charge',
                                        sub: 'incl. $100 Health Levy (recurring)',
                                        value: data.fixed_charge,
                                    },
                                    {
                                        label: 'Variable Charge',
                                        sub: 'f(Block AUV) × unit entitlement %',
                                        value: data.variable_charge ?? data.valuation_charge,
                                    },
                                    {
                                        label: 'PFESL',
                                        sub: 'Police, Fire & Emergency Services Levy',
                                        value: data.fesl ?? data.pfesl,
                                    },
                                    {
                                        label: 'Safer Families Levy',
                                        value: data.sfl ?? data.safer_families_levy,
                                    },
                                ]
                                    .filter((r) => r.value != null)
                                    .map((r) => (
                                        <div key={r.label}
                                             className="flex items-start justify-between px-4 py-2.5 gap-2">
                                            <div>
                                                <span className="text-foreground">{r.label}</span>
                                                {r.sub && (
                                                    <p className="text-[10px] text-muted-foreground mt-0.5">{r.sub}</p>
                                                )}
                                            </div>
                                            <span className="font-medium tabular-nums flex-shrink-0">
                        {formatCurrency(r.value)}
                      </span>
                                        </div>
                                    ))}
                                <div className="flex justify-between px-4 py-3 font-bold bg-muted rounded-b-xl">
                                    <span>Total Annual Rates</span>
                                    <span className="tabular-nums text-base">
                    {formatCurrency(data.total_rates ?? 0)}
                  </span>
                                </div>
                            </div>

                            {/* Quarterly instalments */}
                            {data.rates_q1 && (
                                <div>
                                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-2">
                                        Quarterly Instalments
                                    </p>
                                    <div className="grid grid-cols-2 gap-2">
                                        {[
                                            {label: 'Q1 Jul–Sep', amount: data.rates_q1, due: '31 Aug'},
                                            {label: 'Q2 Oct–Dec', amount: data.rates_q2, due: '30 Nov'},
                                            {label: 'Q3 Jan–Mar', amount: data.rates_q3, due: '28 Feb'},
                                            {label: 'Q4 Apr–Jun', amount: data.rates_q4, due: '31 May'},
                                        ].map((q) => (
                                            <div
                                                key={q.label}
                                                className="rounded-lg bg-muted/60 border border-border px-3 py-2 text-center"
                                            >
                                                <p className="text-[10px] font-bold text-muted-foreground uppercase">{q.label}</p>
                                                <p className="text-sm font-bold tabular-nums">{formatCurrency(q.amount)}</p>
                                                <p className="text-[10px] text-muted-foreground">due {q.due}</p>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Payment status */}
                            {data.total_paid > 0 && (
                                <div
                                    className="flex justify-between p-3 bg-emerald-50 border border-emerald-100 rounded-xl">
                  <span className="text-emerald-800 font-medium flex items-center gap-1.5">
                    <CheckCircle size={14}/>Amount Paid
                  </span>
                                    <span className="font-bold tabular-nums text-emerald-900">
                    {formatCurrency(data.total_paid)}
                  </span>
                                </div>
                            )}

                            <p className="text-[11px] text-muted-foreground">
                                Source: ACT Revenue Office · refreshed from live API · cached 24h.{' '}
                                <a
                                    href="https://www.revenue.act.gov.au/rates/calculating-rates"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-primary hover:underline"
                                >
                                    Learn more
                                </a>
                            </p>
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">
                            Council rates data not yet available.
                        </p>
                    )}
                </DialogContent>
            </Dialog>
        </>
    );
};
// ─── Land Tax Action Card ─────────────────────────────────────────────────────
// Only renders when land_tax_applicable OR land_tax_total > 0

/**
 * @generated FunctionHeader
 * Function: LandTaxActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LandTaxActionCard = ({unitNumber}) => {
    const {api, hasPermission, selectedBuilding} = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);
    const [hasTenant, setHasTenant] = useState(false);

    const fetch = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        try {
            const [ratesRes, occupantsRes] = await Promise.all([
                api.get(`/council-rates/${unitNumber}`),
                api.get(`/units/${unitNumber}/occupants`)
            ]);
            setData(ratesRes.data);
            const occupants = occupantsRes.data.occupants || [];
            setHasTenant(occupants.some((o) => o.role === 'tenant'));
        } catch { /* silent */
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetch();
    }, [fetch]);

    const cq = getCurrentLandTaxQuarter();
    const qAmount = data?.[ cq.key ];
    const canManage = hasPermission('can_manage_finances');

    return (
        <>
            <ActionCard
                icon={Receipt}
                title="ACT Land Tax"
                subtitle={hasTenant ? "Tenanted Property" : "Owner Occupied / No Tenant"}
                primaryAmount={data?.land_tax_total ? formatCurrency(data.land_tax_total) : '—'}
                primaryLabel="Annual Total"
                secondaryText={
                    !hasTenant
                        ? "Exempt if owner-occupied"
                        : qAmount
                            ? `${cq.label} assessment: ${formatCurrency(qAmount)} · due ${cq.due}`
                            : null
                }
                badge={hasTenant ? <Badge
                    className="bg-emerald-100 text-emerald-700 border-emerald-200 text-[9px]">Tenanted</Badge> : null}
                onClick={() => setOpen(true)}
                loading={loading}
            />

            {/* Detail Dialog */}
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Receipt size={16} className="text-primary"/>
                            ACT Land Tax — {unitNumber}
                        </DialogTitle>
                        <DialogDescription>
                            Quarterly assessment{data?.financial_year ? ` · ${data.financial_year}` : ''}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 text-sm">
                        <div className="p-3 bg-amber-50 border border-amber-100 rounded-xl flex gap-3">
                            <Info size={16} className="text-amber-500 shrink-0 mt-0.5"/>
                            <p className="text-xs text-amber-800 leading-relaxed">
                                <strong>Note:</strong> Land Tax <strong>only applies</strong> to investment properties
                                where a tenant resides. Owner-occupied residences are exempt.
                            </p>
                        </div>

                        {hasTenant ? (
                            <div
                                className="bg-emerald-50 border border-emerald-100 p-3 rounded-xl flex items-center gap-3">
                                <Users size={16} className="text-emerald-500"/>
                                <p className="text-xs text-emerald-800 font-medium">Active tenant record detected for
                                    Unit {unitNumber}</p>
                            </div>
                        ) : (
                            <div
                                className="bg-muted/60 border border-border p-3 rounded-xl flex items-center gap-3 text-muted-foreground">
                                <Users size={16} className="text-muted-foreground"/>
                                <p className="text-xs italic">No active tenant record found for this unit.</p>
                            </div>
                        )}

                        {data?.land_tax_total ? (
                            <>
                                {data.land_tax_note && (
                                    <div className="p-3 bg-primary/5 border border-primary/15 rounded-xl flex gap-2">
                                        <Info size={14} className="text-primary flex-shrink-0 mt-0.5"/>
                                        <p className="text-xs text-muted-foreground">{data.land_tax_note}</p>
                                    </div>
                                )}

                                {/* Breakdown */}
                                <div
                                    className="rounded-xl bg-muted/60 border border-border divide-y divide-border">
                                    <div className="flex justify-between px-4 py-2.5">
                                        <div>
                                            <span className="text-foreground">Fixed Charge</span>
                                            <p className="text-[10px] text-muted-foreground">Annual fixed component</p>
                                        </div>
                                        <span className="font-medium tabular-nums">
                    {formatCurrency(data.land_tax_fixed ?? 1693)}
                  </span>
                                    </div>
                                    <div className="flex justify-between px-4 py-2.5">
                                        <div>
                                            <span className="text-foreground">Variable Charge</span>
                                            <p className="text-[10px] text-muted-foreground">
                                                f(Block AUV) × {data.unit_entitlement_pct ?? '—'}%
                                            </p>
                                        </div>
                                        <span className="font-medium tabular-nums">
                    {formatCurrency(data.land_tax_variable ?? 0)}
                  </span>
                                    </div>
                                    <div className="flex justify-between px-4 py-3 font-bold bg-muted rounded-b-xl">
                                        <span className="text-foreground">Annual Land Tax</span>
                                        <span className="tabular-nums text-base text-foreground">
                    {formatCurrency(data.land_tax_total)}
                  </span>
                                    </div>
                                </div>

                                {/* Quarterly assessments */}
                                {data.land_tax_q1 && (
                                    <div>
                                        <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-2">
                                            Quarterly Assessment
                                        </p>
                                        <div className="grid grid-cols-2 gap-2">
                                            {[
                                                {label: 'Q1 Jul–Sep', amount: data.land_tax_q1, due: '31 Aug'},
                                                {label: 'Q2 Oct–Dec', amount: data.land_tax_q2, due: '30 Nov'},
                                                {label: 'Q3 Jan–Mar', amount: data.land_tax_q3, due: '28 Feb'},
                                                {label: 'Q4 Apr–Jun', amount: data.land_tax_q4, due: '31 May'},
                                            ].map((q) => (
                                                <div
                                                    key={q.label}
                                                    className="rounded-lg bg-primary/5 border border-primary/15 px-3 py-2 text-center"
                                                >
                                                    <p className="text-[10px] font-bold text-muted-foreground uppercase">{q.label}</p>
                                                    <p className="text-sm font-bold text-foreground tabular-nums">
                                                        {formatCurrency(q.amount)}
                                                    </p>
                                                    <p className="text-[10px] text-muted-foreground">due {q.due}</p>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                <p className="text-[11px] text-muted-foreground">
                                    Land tax is assessed on 1 Jul, 1 Oct, 1 Jan, 1 Apr each year.
                                    Only investment/rental properties pay land tax — owner-occupied (PPR) is
                                    exempt.{' '}
                                    <a
                                        href="https://www.revenue.act.gov.au/land-tax"
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-primary hover:underline"
                                    >
                                        Learn more
                                    </a>
                                </p>

                                <div className="pt-2">
                                    <Button
                                        className="w-full font-bold rounded-xl"
                                        disabled={!hasTenant || !canManage}
                                        onClick={() => {
                                            setOpen(false);
                                            // In a real app, this would open a payment gateway or mark as paid
                                            toast.success("Proceeding to payment gateway...");
                                        }}
                                    >
                                        {hasTenant ? "Proceed to Payment" : "Payment Disabled (No Tenant)"}
                                    </Button>
                                    {!hasTenant && (
                                        <p className="text-[10px] text-center text-muted-foreground mt-2">
                                            Payment is enabled only when a tenant is registered for this unit.
                                        </p>
                                    )}
                                </div>
                            </>
                        ) : (
                            <p className="text-sm text-muted-foreground py-4 text-center border border-dashed rounded-xl">
                                Land tax figures not yet available for this unit.
                            </p>
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};
// ─── Water Bill Action Card ───────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: WaterBillActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const WaterBillActionCard = ({unitNumber}) => {
    const {api, selectedBuilding} = useAuth();
    const [bills, setBills] = useState([]);
    const [estimates, setEstimates] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    const currentYear = new Date().getFullYear();

    const fetch = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        try {
            const [billsRes, estRes] = await Promise.all([
                api.get(`/water-bills/${unitNumber}?limit=8`),
                api.get(`/water-bills/quarterly-estimates?year=${currentYear}`),
            ]);
            setBills(billsRes.data || []);
            setEstimates(estRes.data || null);
        } catch { /* silent */
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber, currentYear]);

    useEffect(() => {
        fetch();
    }, [fetch]);

    // Current calendar-year quarter for water bills (Jan-Mar=Q1, distinct from ACT financial year)
    const waterQ = getCurrentWaterQuarter();
    const qEst = estimates?.estimates?.find((e) => e.quarter === waterQ.label);

    // Next/most recent unpaid DB bill (if any)
    const unpaidBills = bills
        .filter((b) => b.status !== 'paid')
        .sort((a, b) => new Date(a.due_date) - new Date(b.due_date));
    const nextUnpaid = unpaidBills[ 0 ];

    // Primary: show DB bill amount if available, otherwise current-quarter supply estimate
    const primaryAmount = nextUnpaid
        ? formatCurrency(nextUnpaid.amount)
        : qEst
            ? formatCurrency(qEst.supply_total ?? 0)
            : '—';
    const primaryLabel = nextUnpaid
        ? `${nextUnpaid.quarter} · due ${formatDate(nextUnpaid.due_date)}`
        : qEst
            ? `${waterQ.label} supply charge`
            : 'Supply charges';

    // Annual estimated total from all 4 quarters
    const annualEst = estimates?.estimates?.reduce((s, e) => s + ( e.supply_total ?? 0 ), 0) ?? 0;

    // Combined daily rate, from the served schedule rather than a typed constant.
    const combinedDailyRate =
        typeof estimates?.water_daily_rate === 'number' && typeof estimates?.sewer_daily_rate === 'number'
            ? estimates.water_daily_rate + estimates.sewer_daily_rate
            : null;
    const secondaryText = combinedDailyRate != null
        ? `${dailyRate(combinedDailyRate)} · Water + Sewage Supply`
        : 'Water + Sewage Supply';

    const statusForBadge = nextUnpaid
        ? nextUnpaid.status
        : bills.length > 0 ? 'paid' : null;

    return (
        <>
            <ActionCard
                icon={Droplets}
                title="Icon Water"
                subtitle={`Supply charges · ${currentYear}`}
                primaryAmount={primaryAmount}
                primaryLabel={primaryLabel}
                secondaryText={secondaryText}
                badge={statusForBadge ? <PayBadge status={statusForBadge}/> : null}
                onClick={() => setOpen(true)}
                loading={loading}
            />

            {/* Detail Dialog */}
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Droplets size={16} className="text-primary"/>
                            Icon Water — {unitNumber}
                        </DialogTitle>
                        <DialogDescription>
                            Icon Water ACT · Supply charges only · {currentYear}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 text-sm">
                        {/* Supply rates */}
                        <div className="p-3 bg-primary/5 border border-primary/15 rounded-xl space-y-1.5">
                            <p className="text-xs font-bold text-primary uppercase tracking-widest mb-1">
                                Fixed Supply Rates (per day)
                                {estimates?.rate_schedule?.schedule_label
                                    ? ` · ${estimates.rate_schedule.schedule_label}`
                                    : ''}
                            </p>
                            <div className="flex justify-between text-xs">
                                <span className="text-foreground">Water supply charge</span>
                                <span className="font-mono font-bold">{dailyRate(estimates?.water_daily_rate)}</span>
                            </div>
                            <div className="flex justify-between text-xs">
                                <span className="text-foreground">Sewerage supply charge</span>
                                <span className="font-mono font-bold">{dailyRate(estimates?.sewer_daily_rate)}</span>
                            </div>
                            <div
                                className="flex justify-between text-xs font-semibold border-t border-primary/25 pt-1 mt-1">
                                <span className="text-foreground">Combined daily rate</span>
                                <span className="font-mono">{dailyRate(combinedDailyRate)}</span>
                            </div>
                            {!estimates && (
                                <p className="pt-1 text-[10px] text-muted-foreground">
                                    Rates unavailable — the supply-rate service did not respond.
                                </p>
                            )}
                        </div>

                        {/* Quarterly estimates */}
                        {estimates?.estimates && (
                            <div>
                                <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-2">
                                    {currentYear} Supply-Only Estimates
                                </p>
                                <div className="grid grid-cols-2 gap-2">
                                    {estimates.estimates.map((e) => (
                                        <div
                                            key={e.quarter}
                                            className="rounded-lg bg-muted/60 border px-3 py-2 text-center"
                                        >
                                            <p className="text-[10px] font-bold text-muted-foreground uppercase truncate">
                                                {e.label}
                                            </p>
                                            <p className="text-sm font-bold tabular-nums">
                                                {formatCurrency(e.supply_total ?? 0)}
                                            </p>
                                            <p className="text-[10px] text-muted-foreground">{e.days} days</p>
                                        </div>
                                    ))}
                                </div>
                                <p className="text-[10px] text-muted-foreground mt-1">
                                    Supply only — does not include water usage charges
                                </p>
                            </div>
                        )}

                        {/* Recent bills */}
                        {bills.length > 0 && (
                            <div>
                                <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-2">
                                    Recent Bills
                                </p>
                                <div className="space-y-2">
                                    {bills.slice(0, 4).map((bill) => (
                                        <div
                                            key={bill.id}
                                            className="flex items-start justify-between p-3 rounded-xl border bg-muted/60"
                                        >
                                            <div>
                                                <p className="font-semibold text-sm">{bill.quarter}</p>
                                                <p className="text-xs text-muted-foreground">
                                                    {bill.water_usage_kl ? `${bill.water_usage_kl} kL · ` : ''}
                                                    due {formatDate(bill.due_date)}
                                                </p>
                                                {bill.status === 'paid' && bill.payment_date && (
                                                    <p className="text-[11px] text-emerald-600">
                                                        Paid {formatDate(bill.payment_date)}
                                                    </p>
                                                )}
                                                {bill.status === 'partial' && bill.amount_paid > 0 && (
                                                    <p className="text-[11px] text-amber-600">
                                                        Paid {formatCurrency(bill.amount_paid)} of {formatCurrency(bill.amount)}
                                                    </p>
                                                )}
                                            </div>
                                            <div className="text-right space-y-1 flex-shrink-0 ml-2">
                                                <p className="font-bold tabular-nums">{formatCurrency(bill.amount)}</p>
                                                <PayBadge status={bill.status}/>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* OC usage charge note */}
                        <div className="p-3 bg-primary/5 border border-primary/15 rounded-xl">
                            <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-1">
                                About Your Water Charges
                            </p>
                            <p className="text-xs text-muted-foreground leading-relaxed">
                                <strong>You pay:</strong> Water Supply + Sewage Supply (fixed daily rate — identical for
                                all units).
                            </p>
                            <p className="text-xs text-primary leading-relaxed mt-1">
                                <strong>Owners Corporation pays:</strong> Actual water usage for the building, allocated
                                by Unit of Entitlement.
                            </p>
                        </div>

                        {/* Annual estimate summary */}
                        {annualEst > 0 && (
                            <div className="flex justify-between items-center px-1">
                                <span className="text-xs text-muted-foreground">{currentYear} annual supply estimate</span>
                                <span className="text-sm font-bold tabular-nums">{formatCurrency(annualEst)}</span>
                            </div>
                        )}

                        {bills.length === 0 && (
                            <p className="text-xs text-muted-foreground py-1 text-center">
                                Supply charges are auto-calculated. No bill upload required.
                            </p>
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};
// ─── Electricity Action Card ──────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: ElectricityActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ElectricityActionCard = ({unitNumber}) => {
    const {api, selectedBuilding} = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    const fetch = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        try {
            const res = await api.get(`/utilities/${unitNumber}`);
            setData(res.data);
        } catch { /* silent */
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetch();
    }, [fetch]);

    const elec = data?.electricity;

    return (
        <>
            <ActionCard
                icon={Zap}
                title="Electricity"
                subtitle={elec?.supplier ?? 'Origin Energy · Embedded Network'}
                primaryAmount={elec?.loc_id ?? '—'}
                primaryLabel="Meter No. (LOC ID)"
                secondaryText={
                    elec
                        ? `Supply $${elec.supply_charge_per_day?.toFixed(4)}/day · Usage $${elec.usage_charge_per_unit?.toFixed(4)}/kWh`
                        : null
                }
                badge={null}
                onClick={() => setOpen(true)}
                loading={loading}
            />

            {/* Detail Dialog */}
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-md max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Zap size={16} className="text-primary"/>
                            Electricity — {unitNumber}
                        </DialogTitle>
                        <DialogDescription>
                            Origin Energy · Embedded network · Evoenergy distribution
                        </DialogDescription>
                    </DialogHeader>

                    {elec ? (
                        <div className="space-y-4 text-sm">
                            <div
                                className="rounded-xl bg-primary/5 border border-primary/15 divide-y divide-primary/15">
                                {[
                                    {label: 'Supplier', value: elec.supplier},
                                    {label: 'Distributor', value: elec.distributor},
                                    {
                                        label: 'Meter No. (LOC ID)',
                                        value: elec.loc_id ?? '—',
                                        mono: true,
                                        copyable: !!elec.loc_id,
                                    },
                                    {
                                        label: 'Supply Charge',
                                        value: `$${elec.supply_charge_per_day?.toFixed(6)} / day`,
                                        mono: true,
                                    },
                                    {
                                        label: 'Usage Charge',
                                        value: `$${elec.usage_charge_per_unit?.toFixed(6)} / kWh`,
                                        mono: true,
                                    },
                                ].map((row) => (
                                    <div
                                        key={row.label}
                                        className="flex items-center justify-between px-4 py-2.5 gap-2"
                                    >
                                        <span className="text-muted-foreground text-xs">{row.label}</span>
                                        <div className="flex items-center gap-1.5">
                      <span className={`text-sm font-medium ${row.mono ? 'font-mono' : ''}`}>
                        {row.value}
                      </span>
                                            {row.copyable && (
                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <button
                                                            type="button"
                                                            onClick={() => copyText(row.value)}
                                                            className="text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm transition-all p-0.5"
                                                            aria-label={`Copy ${row.label}`}
                                                        >
                                                            <Copy size={12}/>
                                                        </button>
                                                    </TooltipTrigger>
                                                    <TooltipContent>
                                                        <p>Copy {row.label}</p>
                                                    </TooltipContent>
                                                </Tooltip>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>

                            <div className="p-3 bg-muted/60 border rounded-xl text-xs text-muted-foreground space-y-1">
                                <p>
                                    <span className="font-semibold">Supply Charge</span> — fixed daily charge
                                    for being connected to the network, regardless of usage.
                                </p>
                                <p>
                                    <span className="font-semibold">Usage Charge</span> — per kilowatt-hour
                                    consumed. Your bill varies based on electricity use.
                                </p>
                                <p>
                                    <span className="font-semibold">Embedded Network</span> — Origin Energy
                                    is the embedded network operator. Evoenergy is the distribution network
                                    service provider (DNSP).
                                </p>
                            </div>

                            <div className="grid grid-cols-2 gap-2">
                                <a
                                    href={`tel:${elec.faults_emergency}`}
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-red-50 border-red-100 hover:bg-red-100 transition-colors"
                                >
                                    <div>
                                        <p className="text-[10px] font-bold text-red-700">Faults & Emergencies</p>
                                        <p className="font-mono text-xs text-red-800">{elec.faults_emergency}</p>
                                    </div>
                                </a>
                                <a
                                    href={`tel:${elec.assistance}`}
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-muted/60 hover:bg-muted transition-colors"
                                >
                                    <div>
                                        <p className="text-[10px] font-bold text-muted-foreground">Customer Assistance</p>
                                        <p className="font-mono text-xs text-foreground">{elec.assistance}</p>
                                    </div>
                                </a>
                            </div>

                            {elec.my_account_url && (
                                <a
                                    href={elec.my_account_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-primary/5 border-primary/15 hover:bg-primary/10 transition-colors text-sm"
                                >
                                    <ExternalLink size={14} className="text-muted-foreground shrink-0"/>
                                    <span className="text-foreground font-medium text-xs">
                    My Account — origin.com.au/myaccount
                  </span>
                                </a>
                            )}
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">
                            No electricity data available.
                        </p>
                    )}
                </DialogContent>
            </Dialog>
        </>
    );
};
// ─── Gas / Induction Action Card ─────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: GasActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const GasActionCard = ({unitNumber}) => {
    const {api, selectedBuilding} = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    const fetch = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        try {
            const res = await api.get(`/utilities/${unitNumber}`);
            setData(res.data);
        } catch { /* silent */
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetch();
    }, [fetch]);

    const gas = data?.gas;
    const hasGas = gas?.has_gas;

    return (
        <>
            <ActionCard
                icon={hasGas ? Flame : Zap}
                iconMuted={!hasGas}
                title={hasGas ? 'Cooktop Gas' : 'Cooktop — Induction'}
                subtitle={
                    hasGas
                        ? ( gas?.supplier ?? 'Origin Gas · Natural gas' )
                        : 'Electric induction — no gas supply'
                }
                primaryAmount={hasGas ? ( gas?.meter_number ?? '—' ) : '—'}
                primaryLabel={hasGas ? 'Gas Meter No.' : 'No gas meter'}
                secondaryText={
                    hasGas && gas?.supply_charge_per_day
                        ? `Supply $${gas.supply_charge_per_day?.toFixed(4)}/day · Usage $${gas.usage_charge_first_3863?.toFixed(4)}/MJ`
                        : hasGas
                            ? null
                            : 'Uses electricity for cooking — no gas charges'
                }
                badge={
                    hasGas !== undefined
                        ? (
                            <Badge variant="outline"
                                   className={`text-[10px] font-semibold px-1.5 ${hasGas ? 'bg-primary/10 text-primary border-primary/20' : 'bg-muted/60 text-muted-foreground border-border'}`}>
                                {hasGas ? 'Gas' : 'Induction'}
                            </Badge>
                        )
                        : null
                }
                onClick={() => setOpen(true)}
                loading={loading}
            />

            {/* Detail Dialog */}
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-md max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            {hasGas
                                ? <><Flame size={16} className="text-primary"/>Cooktop Gas — {unitNumber}</>
                                : <><Zap size={16} className="text-muted-foreground"/>Induction Cooktop — {unitNumber}</>
                            }
                        </DialogTitle>
                        <DialogDescription>
                            {hasGas
                                ? 'Origin Gas · Natural gas supply connection'
                                : 'Electric induction cooktop · no gas supply connected'}
                        </DialogDescription>
                    </DialogHeader>

                    {gas ? (
                        <div className="space-y-4 text-sm">
                            <div
                                className={`rounded-xl border divide-y ${hasGas ? 'bg-primary/5 border-primary/15 divide-primary/15' : 'bg-muted/60 border-border divide-border'}`}
                            >
                                {[
                                    {label: 'Cooktop', value: gas.cooktop_type},
                                    {label: 'Supplier', value: gas.supplier},
                                    hasGas && {
                                        label: 'Gas Meter No.',
                                        value: gas.meter_number ?? '—',
                                        mono: true,
                                        copyable: !!gas.meter_number,
                                    },
                                    hasGas && {
                                        label: 'Supply Charge',
                                        value: `$${gas.supply_charge_per_day?.toFixed(6)} / day`,
                                        mono: true,
                                    },
                                    hasGas && {
                                        label: 'Usage (first 3,863 MJ)',
                                        value: `$${gas.usage_charge_first_3863?.toFixed(6)} / MJ`,
                                        mono: true,
                                    },
                                ]
                                    .filter(Boolean)
                                    .map((row) => (
                                        <div
                                            key={row.label}
                                            className="flex items-center justify-between px-4 py-2.5 gap-2"
                                        >
                                            <span className="text-muted-foreground text-xs">{row.label}</span>
                                            <div className="flex items-center gap-1.5">
                        <span className={`text-sm font-medium ${row.mono ? 'font-mono' : ''}`}>
                          {row.value}
                        </span>
                                                {row.copyable && (
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <button
                                                                type="button"
                                                                onClick={() => copyText(row.value)}
                                                                className="text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm transition-all p-0.5"
                                                                aria-label={`Copy ${row.label}`}
                                                            >
                                                                <Copy size={12}/>
                                                            </button>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            <p>Copy {row.label}</p>
                                                        </TooltipContent>
                                                    </Tooltip>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                            </div>

                            {!hasGas && (
                                <div className="p-3 bg-primary/5 border border-primary/15 rounded-xl text-xs text-foreground">
                                    <p className="font-medium mb-1">Why Induction?</p>
                                    <p>
                                        Some units at {selectedBuilding?.name || 'this complex'} (apartments and select
                                        townhouses) were
                                        designed with induction cooktops and do not have a natural gas connection.
                                        Induction cooking uses electricity exclusively.
                                    </p>
                                </div>
                            )}

                            {/* Gas emergency contacts always shown */}
                            <div className="grid grid-cols-2 gap-2">
                                <a
                                    href="tel:1300137078"
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-primary/5 border-primary/15 hover:bg-primary/10 transition-colors"
                                >
                                    <div>
                                        <p className="text-[10px] font-bold text-muted-foreground">Gas Faults (Evoenergy)</p>
                                        <p className="font-mono text-xs text-foreground">1300 137 078</p>
                                    </div>
                                </a>
                                <a
                                    href="tel:131909"
                                    className="flex items-center gap-2 p-2.5 rounded-lg border bg-red-50 border-red-100 hover:bg-red-100 transition-colors"
                                >
                                    <div>
                                        <p className="text-[10px] font-bold text-red-700">Gas Emergency</p>
                                        <p className="font-mono text-xs text-red-800">13 19 09</p>
                                    </div>
                                </a>
                            </div>

                            <div className="p-3 bg-red-50 border border-red-100 rounded-xl text-xs text-red-800">
                                <p className="font-medium mb-1">Smell Gas?</p>
                                <p>
                                    Leave the building immediately. Do not use switches or phones inside.
                                    Call Evoenergy: <span className="font-mono font-bold">13 19 09</span>
                                </p>
                            </div>
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">
                            No gas / cooktop data available.
                        </p>
                    )}
                </DialogContent>
            </Dialog>
        </>
    );
};
// ─── NBN Connection Action Card ───────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: NBNActionCard
 * Path: frontend/src/components/dashboard/PropertyServicesActionCards.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const NBNActionCard = ({unitNumber}) => {
    const {api, selectedBuilding} = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [open, setOpen] = useState(false);

    const fetch = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        try {
            const res = await api.get(`/utilities/${unitNumber}`);
            setData(res.data);
        } catch { /* silent */
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetch();
    }, [fetch]);

    const nbn = data?.nbn;

    return (
        <>
            <ActionCard
                icon={Wifi}
                title="NBN Connection"
                subtitle="Fibre to the Premises (FTTP)"
                primaryAmount={nbn?.nmi ?? '—'}
                primaryLabel="Box No. (NMI)"
                secondaryText={nbn?.property_number ? `Property No: ${nbn.property_number}` : null}
                badge={null}
                onClick={() => setOpen(true)}
                loading={loading}
            />

            {/* Detail Dialog */}
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-md max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Wifi size={16} className="text-primary"/>
                            NBN Connection — {unitNumber}
                        </DialogTitle>
                        <DialogDescription>
                            National Broadband Network · {selectedBuilding?.address || 'Community Location'}
                        </DialogDescription>
                    </DialogHeader>

                    {nbn ? (
                        <div className="space-y-4 text-sm">
                            <div className="rounded-xl bg-primary/5 border border-primary/15 divide-y divide-primary/15">
                                {[
                                    {label: 'Supplier', value: nbn.supplier ?? 'nbn co'},
                                    {
                                        label: 'Box No. (NMI)',
                                        value: nbn.nmi ?? '—',
                                        mono: true,
                                        copyable: !!nbn.nmi,
                                        desc: 'Network Metering Identifier — give this to your RSP when signing up',
                                    },
                                    {
                                        label: 'Property Number',
                                        value: nbn.property_number ?? '—',
                                        mono: true,
                                        copyable: !!nbn.property_number,
                                        desc: 'NBN property identifier for your unit',
                                    },
                                    {label: 'Technology', value: 'FTTP (Fibre to the Premises)'},
                                ].map((row) => (
                                    <div key={row.label} className="px-4 py-2.5">
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="text-muted-foreground text-xs">{row.label}</span>
                                            <div className="flex items-center gap-1.5">
                        <span className={`text-sm font-medium ${row.mono ? 'font-mono' : ''}`}>
                          {row.value}
                        </span>
                                                {row.copyable && (
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <button
                                                                type="button"
                                                                onClick={() => copyText(row.value)}
                                                                className="text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded-sm transition-all p-0.5"
                                                                aria-label={`Copy ${row.label}`}
                                                            >
                                                                <Copy size={12}/>
                                                            </button>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            <p>Copy {row.label}</p>
                                                        </TooltipContent>
                                                    </Tooltip>
                                                )}
                                            </div>
                                        </div>
                                        {row.desc && (
                                            <p className="text-[10px] text-muted-foreground mt-0.5">{row.desc}</p>
                                        )}
                                    </div>
                                ))}
                            </div>

                            <div className="p-3 bg-amber-50 border border-amber-100 rounded-xl text-xs text-amber-800">
                                <p className="font-medium mb-1">Important — Connectivity is person-specific</p>
                                <p>
                                    The NBN connection is not automatically transferred between residents.
                                    Contact your chosen Retail Service Provider (RSP) to connect or transfer service.
                                </p>
                            </div>

                            <div className="p-3 bg-muted/60 border rounded-xl text-xs space-y-1.5 text-foreground">
                                <p className="font-medium">How to connect:</p>
                                <ol className="list-decimal list-inside space-y-0.5">
                                    <li>Choose an RSP (Aussie Broadband, Superloop, TPG, iiNet, Telstra…)</li>
                                    <li>Provide your NMI and Property Number when signing up</li>
                                    <li>RSP arranges activation — typically 1–5 business days</li>
                                </ol>
                            </div>

                            <a
                                href="https://www.nbn.com.au/residential/service-providers"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-2 p-2.5 rounded-lg border bg-primary/5 border-primary/15 hover:bg-primary/10 transition-colors text-sm"
                            >
                                <ExternalLink size={14} className="text-muted-foreground shrink-0"/>
                                <span className="text-foreground font-medium text-xs">
                  Find an RSP — nbn.com.au/service-providers
                </span>
                            </a>
                        </div>
                    ) : (
                        <p className="text-sm text-muted-foreground py-4 text-center">
                            No NBN data available.
                        </p>
                    )}
                </DialogContent>
            </Dialog>
        </>
    );
};
