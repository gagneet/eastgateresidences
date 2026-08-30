// @featuretrace:levy — Owner "My Finances" page: personal levy balance, paid history, next due amount.
// Layer: frontend
// Data flow: MyFinancesPage → GET /owner-finance/levy-breakdown?unit_number=, /savings-summary?unit_number=,
//            /health-explanation → unit_levy_ledger + building_summaries (building-scoped).
// Related: backend/routers/owner_finance.py, backend/utils/finance_helpers.py (compute_next_estimated_payment)
//           frontend/src/hooks/useActiveUnit.ts
//           frontend/src/pages/dashboard/LevyPaymentPage.tsx
//
// @featuretrace:multi-unit-ownership — every figure here is scoped to the sidebar's active unit.
// Layer: frontend
// Data flow: useActiveUnit() → GET /owner-finance/levy-breakdown?unit_number= + /savings-summary?unit_number=
//            → unit_levy_ledger (building-scoped). Switching units re-queries, it does not just relabel.
// Related: frontend/src/hooks/useActiveUnit.ts
//           frontend/src/components/layout/UnitSwitcher.tsx
//           backend/routers/owner_finance.py
// Toggle: multi_unit_ownership (the in-page picker; the page itself is not gated)
// Tests: tests/frontend/unit/pages/MyFinancesUnitScope.test.tsx
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useActiveUnit } from '../../hooks/useActiveUnit';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip as RechartsTooltip, XAxis, YAxis, } from 'recharts';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../components/ui/tooltip';
import { Dialog, DialogContent, DialogHeader, DialogTitle, } from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { toast } from 'sonner';
import { AlertTriangle, CheckCircle2, ChevronRight, DollarSign, Heart, Home, Info, TrendingUp, } from 'lucide-react';
import { getFYLabel } from '../../lib/utils';

const FUND_COLORS = {
    admin: '#3b82f6',
    sinking: '#8b5cf6',
    insurance: '#f59e0b',
};

const FUND_DESCRIPTIONS = {
    admin: 'Day-to-day operating expenses: cleaning, insurance, utilities, garden maintenance, management fees.',
    sinking: 'Long-term capital works fund: building repairs, roof replacement, lift maintenance, major refurbishments.',
    insurance: 'Building insurance, public liability, and workers compensation premiums.',
};
/**
 * @generated FunctionHeader
 * Function: fmtDollars
 * Path: frontend/src/pages/dashboard/MyFinancesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtDollars = (cents) =>
    typeof cents === 'number'
        ? `$${( cents / 100 ).toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`
        : '—';
/**
 * @generated FunctionHeader
 * Function: fmtDollarsWhole
 * Path: frontend/src/pages/dashboard/MyFinancesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtDollarsWhole = (val) =>
    typeof val === 'number'
        ? `$${val.toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`
        : '—';
/** Grade colour classes */
function gradeStyle(grade) {
    const map = {
        A: {bg: '#dcfce7', color: '#15803d'},
        B: {bg: '#dbeafe', color: '#1d4ed8'},
        C: {bg: '#fef9c3', color: '#a16207'},
        D: {bg: '#fee2e2', color: '#b91c1c'},
    };
    return map[ grade ] || {bg: '#f3f4f6', color: '#4b5563'};
}

/** Thresholds explanation per grade */
const GRADE_THRESHOLDS = 'A ≥ 85 · B ≥ 70 · C ≥ 55 · D < 55';
/**
 * @generated FunctionHeader
 * Function: MyFinancesPage
 * Path: frontend/src/pages/dashboard/MyFinancesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MyFinancesPage() {
    const {api, hasFeatureAccess} = useAuth();
    // Scope every figure on this page to the unit the sidebar switcher is on.
    // Reading `user.unit_number` here pinned multi-unit owners to their account's
    // default unit with no way to reach the other one (GAP-IDENTITY-UNIT-SWITCH-001).
    const {activeUnit, availableUnits, switchUnit, isMultiUnit} = useActiveUnit();
    // Gate the picker on the SAME toggle that gates POST /auth/switch-unit
    // (require_feature("multi_unit_ownership") in routers/auth.py). UnitSwitcher
    // already does this; without it here the picker renders for a building with the
    // toggle off and every selection 403s into a "Failed to switch unit" toast.
    // A control must be gated wherever it is rendered, not only at its first home.
    const canSwitchUnits = isMultiUnit && hasFeatureAccess('multi_unit_ownership');

    const [levyData, setLevyData] = useState(null);
    const [healthData, setHealthData] = useState(null);
    const [savingsData, setSavingsData] = useState(null);
    const [siteSettings, setSiteSettings] = useState(null);
    const [loading, setLoading] = useState(true);
    const [healthModalOpen, setHealthModalOpen] = useState(false);
    const [expandedCategory, setExpandedCategory] = useState(null);

    // Two fetch groups, because the page mixes two scopes and they must not share a
    // lifecycle. Building health and site settings are building-wide: re-requesting
    // them on a unit switch would contradict this page's own contract (switching
    // units cannot move a building's health score) and would blank that card behind
    // the loading state for data that provably did not change.
    const fetchBuildingWide = useCallback(async () => {
        const [healthRes, settingsRes] = await Promise.allSettled([
            api.get('/owner-finance/health-explanation'),
            api.get('/settings'),
        ]);

        if (healthRes.status === 'fulfilled') setHealthData(healthRes.value.data);
        else toast.error('Could not load building health');

        if (settingsRes.status === 'fulfilled') setSiteSettings(settingsRes.value.data);
    }, [api]);

    const fetchPerUnit = useCallback(async () => {
        setLoading(true);
        const unitParam = activeUnit ? `?unit_number=${encodeURIComponent(activeUnit)}` : '';
        const [levyRes, savingsRes] = await Promise.allSettled([
            api.get(`/owner-finance/levy-breakdown${unitParam}`),
            api.get(`/owner-finance/savings-summary${unitParam}`),
        ]);

        if (levyRes.status === 'fulfilled') setLevyData(levyRes.value.data);
        else toast.error('Could not load levy breakdown');

        if (savingsRes.status === 'fulfilled') setSavingsData(savingsRes.value.data);
        else toast.error('Could not load savings data');

        setLoading(false);
        // activeUnit is a real dependency, not decoration: without it a unit switch
        // relabels the header but leaves every figure below it showing the old unit.
    }, [api, activeUnit]);

    useEffect(() => {
        fetchBuildingWide();
    }, [fetchBuildingWide]);

    useEffect(() => {
        fetchPerUnit();
    }, [fetchPerUnit]);

    if (loading) {
        return (
            <div className="p-6 text-center text-gray-500">Loading your financial dashboard…</div>
        );
    }

    const fyStartMonth = siteSettings?.financial_year_start_month ?? 7;
    const dataYear = levyData?.data_year;
    // Parse year from formats like "2026-2027" or "2026"
    const baseYear = dataYear
        ? parseInt(String(dataYear).split('-')[ 0 ], 10)
        : new Date().getFullYear();
    const fyLabel = getFYLabel(baseYear, fyStartMonth);

    const chartData = ( levyData?.categories || [] )
        .slice(0, 10)
        .map((cat) => ( {
            name: cat.name.length > 22 ? cat.name.slice(0, 20) + '…' : cat.name,
            amount: cat.your_quarterly,
            pct: cat.pct,
            fund_type: cat.fund_type,
        } ));

    const gStyle = gradeStyle(healthData?.grade);

    return (
        <TooltipProvider>
            <div className="p-6 space-y-6 max-w-5xl mx-auto">
                <div className="flex items-center justify-between flex-wrap gap-3">
                    <div className="flex items-center gap-3">
                        <DollarSign className="w-7 h-7 text-blue-600"/>
                        <div>
                            <h1 className="text-2xl font-bold">My Finances</h1>
                            <p className="text-sm text-muted-foreground">
                                Unit {activeUnit || '—'} · {fyLabel} · Financial transparency dashboard
                            </p>
                        </div>
                    </div>

                    {/* Multi-unit owners get an in-page picker as well as the sidebar
                        switcher — this page is entirely per-unit, so which unit it is
                        showing has to be visible and changeable without leaving it. */}
                    {canSwitchUnits && (
                        <div className="flex items-center gap-2">
                            <Home className="w-4 h-4 text-muted-foreground" aria-hidden="true"/>
                            <label htmlFor="my-finances-unit" className="text-sm text-muted-foreground">
                                Showing unit
                            </label>
                            <Select
                                value={activeUnit ?? undefined}
                                onValueChange={(value) => switchUnit(value)}
                            >
                                <SelectTrigger
                                    id="my-finances-unit"
                                    className="w-32"
                                    aria-label="Select which of your units to show"
                                    data-testid="my-finances-unit-select"
                                >
                                    <SelectValue placeholder="Select unit"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {availableUnits.map((unitNumber) => (
                                        <SelectItem key={unitNumber} value={unitNumber}>
                                            Unit {unitNumber}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    )}
                </div>

                {/* ── Section 1: Your Levy Explained ────────────────────────────── */}
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <DollarSign className="w-5 h-5 text-blue-500"/>
                            Where does my levy go?
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Info className="w-4 h-4 text-muted-foreground cursor-help"/>
                                </TooltipTrigger>
                                <TooltipContent className="text-xs max-w-xs">
                                    Your quarterly levy is split across budget categories in proportion to your
                                    Unit of Entitlement (UOE). Click any row for a description of that category.
                                </TooltipContent>
                            </Tooltip>
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {levyData?.error ? (
                            <p className="text-red-500">{levyData.error}</p>
                        ) : (
                            <>
                                <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                                    Ledger balance
                                </p>
                                <div className="flex flex-wrap gap-6 mb-2">
                                    <div>
                                        <p className="text-sm text-gray-500">Quarterly levy ({fyLabel})</p>
                                        <p className="text-3xl font-bold text-blue-700">
                                            {fmtDollarsWhole(levyData?.quarterly_levy)}
                                        </p>
                                        {levyData?.quarterly_levy_source && levyData.quarterly_levy_source !== 'ledger' && (
                                            <p
                                                data-testid="quarterly-levy-source-warning"
                                                className="text-xs text-amber-600 mt-1 flex items-center gap-1"
                                            >
                                                <Info className="w-3.5 h-3.5"/>
                                                {levyData.quarterly_levy_source === 'unit_master'
                                                    ? 'Estimated from unit record — no ledger row for this year yet'
                                                    : 'Estimated from budget categories — no ledger figure available yet'}
                                            </p>
                                        )}
                                    </div>
                                    <div>
                                        <p className="text-sm text-gray-500">Your UOE share</p>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <p className="text-lg font-semibold cursor-help">
                                                    {levyData?.uoe_share_pct?.toFixed(3)}%
                                                </p>
                                            </TooltipTrigger>
                                            <TooltipContent className="text-xs max-w-xs">
                                                Unit of Entitlement (UOE) is your lot's proportional share of the
                                                building.
                                                Your levy = building total levy × (your UOE ÷ total building UOE).
                                            </TooltipContent>
                                        </Tooltip>
                                    </div>
                                    {levyData?.balance_owing > 0 && (
                                        <div>
                                            <p className="text-sm text-red-500">Balance owing</p>
                                            <p className="text-lg font-semibold text-red-600">
                                                {fmtDollarsWhole(levyData.balance_owing)}
                                            </p>
                                        </div>
                                    )}
                                    {levyData?.balance_credit > 0 && (
                                        <div>
                                            <p className="text-sm text-green-600">Credit balance</p>
                                            <p className="text-lg font-semibold text-green-700">
                                                {fmtDollarsWhole(levyData.balance_credit)}
                                            </p>
                                        </div>
                                    )}
                                </div>

                                <p className="text-[10px] font-black uppercase tracking-widest text-slate-400 mt-2">
                                    Budget allocation explanation
                                </p>
                                <p className="text-xs text-slate-400 -mt-3">
                                    Per-category figures below are derived from budgeted amounts, proportioned by
                                    your UOE — not a ledger balance.
                                </p>

                                {chartData.length === 0 && (
                                    <div
                                        className="text-sm text-muted-foreground bg-muted/40 rounded px-4 py-3 flex items-center gap-2">
                                        <Info className="w-4 h-4 flex-shrink-0"/>
                                        Budget categories not yet configured for {fyLabel}. Contact your strata manager.
                                    </div>
                                )}

                                {chartData.length > 0 && (
                                    <div className="h-56">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart data={chartData} layout="vertical"
                                                      margin={{left: 160, right: 30}}>
                                                <XAxis
                                                    type="number"
                                                    tickFormatter={(v) => `$${v}`}
                                                    tick={{fontSize: 11}}
                                                />
                                                <YAxis
                                                    type="category"
                                                    dataKey="name"
                                                    tick={{fontSize: 11}}
                                                    width={155}
                                                />
                                                <RechartsTooltip
                                                    formatter={(val) => [`$${val.toFixed(2)}`, 'Quarterly']}
                                                />
                                                <Bar dataKey="amount" radius={[0, 4, 4, 0]}>
                                                    {chartData.map((entry, idx) => (
                                                        <Cell
                                                            key={idx}
                                                            fill={FUND_COLORS[ entry.fund_type ] || '#6b7280'}
                                                        />
                                                    ))}
                                                </Bar>
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                )}

                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Category</TableHead>
                                            <TableHead>Fund</TableHead>
                                            <TableHead className="text-right">Your quarterly</TableHead>
                                            <TableHead className="text-right">% of total</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {( levyData?.categories || [] ).length === 0 && (
                                            <TableRow>
                                                <TableCell colSpan={4}
                                                           className="text-center text-muted-foreground py-4 text-sm">
                                                    No budget categories available for {fyLabel}
                                                </TableCell>
                                            </TableRow>
                                        )}
                                        {( levyData?.categories || [] ).map((cat, i) => (
                                            <React.Fragment key={i}>
                                                <TableRow
                                                    className="cursor-pointer hover:bg-muted/40"
                                                    onClick={() => setExpandedCategory(expandedCategory === i ? null : i)}
                                                    data-testid={`levy-category-row-${i}`}
                                                >
                                                    <TableCell className="font-medium">
                                                        <div className="flex items-center gap-1">
                                                            {cat.name}
                                                            <ChevronRight
                                                                className={`w-3 h-3 text-muted-foreground transition-transform ${expandedCategory === i ? 'rotate-90' : ''}`}
                                                            />
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>
                            <span
                                className="px-2 py-0.5 rounded-full text-xs font-medium"
                                style={{
                                    background: ( FUND_COLORS[ cat.fund_type ] || '#6b7280' ) + '22',
                                    color: FUND_COLORS[ cat.fund_type ] || '#6b7280',
                                }}
                            >
                              {cat.fund_type}
                            </span>
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {fmtDollarsWhole(cat.your_quarterly)}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {cat.pct?.toFixed(1)}%
                                                    </TableCell>
                                                </TableRow>
                                                {expandedCategory === i && (
                                                    <TableRow className="bg-muted/30">
                                                        <TableCell colSpan={4}
                                                                   className="text-sm text-muted-foreground px-6 py-2">
                                                            <p className="font-medium text-foreground mb-0.5">{cat.name}</p>
                                                            <p>
                                                                {FUND_DESCRIPTIONS[ cat.fund_type ] ||
                                                                    'This budget category covers strata-related expenses allocated to the building.'}
                                                            </p>
                                                            <p className="mt-1">
                                                                <strong>Annual amount (building total):</strong>{' '}
                                                                {cat.annual_amount != null
                                                                    ? fmtDollarsWhole(cat.annual_amount)
                                                                    : 'Not available'}{' '}
                                                                &nbsp;·&nbsp;
                                                                <strong>Your share per quarter:</strong>{' '}
                                                                {fmtDollarsWhole(cat.your_quarterly)}
                                                            </p>
                                                            <p className="mt-0.5 text-xs">
                                                                Calculation: building annual budget × (your
                                                                UOE {levyData?.uoe_share_pct?.toFixed(3)}%) ÷ 4 quarters
                                                            </p>
                                                        </TableCell>
                                                    </TableRow>
                                                )}
                                            </React.Fragment>
                                        ))}
                                    </TableBody>
                                </Table>
                            </>
                        )}
                    </CardContent>
                </Card>

                {/* ── Section 2: Building Health (clickable → modal) ────────────── */}
                <Card
                    className="cursor-pointer hover:shadow-md transition-shadow"
                    onClick={() => healthData?.health_score != null && setHealthModalOpen(true)}
                    data-testid="health-score-card"
                >
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <TrendingUp className="w-5 h-5 text-green-500"/>
                            Is your building's money well managed?
                            {healthData?.health_score != null && (
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Info className="w-4 h-4 text-muted-foreground cursor-help"/>
                                    </TooltipTrigger>
                                    <TooltipContent className="text-xs">
                                        Click this card to see a detailed breakdown of your building's financial health
                                    </TooltipContent>
                                </Tooltip>
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {healthData && healthData.health_score !== null ? (
                            <>
                                <p
                                    data-testid="health-score-non-authoritative-caption"
                                    className="text-[10px] font-black uppercase tracking-widest text-slate-400"
                                >
                                    Derived analytics — not an authoritative finance figure
                                </p>
                                <div className="flex items-center gap-4">
                                    <div className="text-4xl font-bold text-gray-800">
                                        {healthData.health_score}
                                        <span className="text-xl text-gray-400">/100</span>
                                    </div>
                                    <div
                                        className="text-2xl font-bold px-3 py-1 rounded"
                                        style={{background: gStyle.bg, color: gStyle.color}}
                                    >
                                        Grade {healthData.grade}
                                    </div>
                                    <span className="text-sm text-muted-foreground">— click for full breakdown</span>
                                </div>

                                <div className="w-full bg-gray-200 rounded-full h-3">
                                    <div
                                        className="h-3 rounded-full transition-all"
                                        style={{
                                            width: `${healthData.health_score}%`,
                                            background:
                                                healthData.health_score >= 85
                                                    ? '#22c55e'
                                                    : healthData.health_score >= 70
                                                        ? '#3b82f6'
                                                        : '#f59e0b',
                                        }}
                                    />
                                </div>

                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2">
                                    {( healthData.components || [] ).map((comp) => (
                                        <div
                                            key={comp.key}
                                            className="flex items-start gap-3 p-3 rounded-lg border"
                                        >
                                            {comp.status === 'good' ? (
                                                <CheckCircle2 className="w-5 h-5 text-green-500 mt-0.5 flex-shrink-0"/>
                                            ) : (
                                                <AlertTriangle className="w-5 h-5 text-amber-500 mt-0.5 flex-shrink-0"/>
                                            )}
                                            <div>
                                                <p className="font-medium text-sm">{comp.label}</p>
                                                <p className="text-sm text-gray-600">{comp.plain_english}</p>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </>
                        ) : (
                            <p className="text-gray-500 text-sm">Building health data not yet available.</p>
                        )}
                    </CardContent>
                </Card>

                {/* ── Health Score Explanation Modal ────────────────────────────── */}
                <Dialog open={healthModalOpen} onOpenChange={setHealthModalOpen}>
                    <DialogContent className="max-w-lg">
                        <DialogHeader>
                            <DialogTitle>Building Health Score — {fyLabel}</DialogTitle>
                        </DialogHeader>
                        {healthData && (
                            <div className="space-y-4">
                                <div className="flex items-center gap-4">
                                    <div className="text-5xl font-bold"
                                         style={{color: gradeStyle(healthData.grade).color}}>
                                        {healthData.health_score}
                                        <span className="text-2xl text-gray-400">/100</span>
                                    </div>
                                    <div>
                                        <div
                                            className="text-2xl font-bold px-3 py-1 rounded inline-block mb-1"
                                            style={{background: gStyle.bg, color: gStyle.color}}
                                        >
                                            Grade {healthData.grade}
                                        </div>
                                        <p className="text-xs text-muted-foreground">{GRADE_THRESHOLDS}</p>
                                    </div>
                                </div>

                                <p className="text-sm text-muted-foreground">
                                    This score reflects the financial and operational health of your building
                                    across four key pillars. A higher score means better-managed finances,
                                    lower arrears, and a well-maintained building.
                                </p>

                                <div className="space-y-3">
                                    {( healthData.components || [] ).map((comp) => (
                                        <div
                                            key={comp.key}
                                            className="flex items-start gap-3 p-3 rounded-lg border"
                                        >
                                            {comp.status === 'good' ? (
                                                <CheckCircle2 className="w-5 h-5 text-green-500 mt-0.5 flex-shrink-0"/>
                                            ) : comp.status === 'poor' ? (
                                                <AlertTriangle className="w-5 h-5 text-red-500 mt-0.5 flex-shrink-0"/>
                                            ) : (
                                                <AlertTriangle className="w-5 h-5 text-amber-500 mt-0.5 flex-shrink-0"/>
                                            )}
                                            <div className="flex-1">
                                                <div className="flex items-center justify-between">
                                                    <p className="font-medium text-sm">{comp.label}</p>
                                                    {comp.score_pct != null && (
                                                        <span className="text-xs text-muted-foreground tabular-nums">
                              {Math.round(comp.score_pct)}%
                            </span>
                                                    )}
                                                </div>
                                                <p className="text-sm text-gray-600 mt-0.5">{comp.plain_english}</p>
                                                {comp.score_pct != null && (
                                                    <div className="mt-1.5 w-full bg-gray-200 rounded-full h-1.5">
                                                        <div
                                                            className="h-1.5 rounded-full"
                                                            style={{
                                                                width: `${Math.min(100, comp.score_pct)}%`,
                                                                background: comp.status === 'good' ? '#22c55e' : comp.status === 'poor' ? '#ef4444' : '#f59e0b',
                                                            }}
                                                        />
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>

                                {healthData.last_computed_at && (
                                    <p className="text-xs text-muted-foreground">
                                        Last computed: {new Date(healthData.last_computed_at).toLocaleString('en-AU', {
                                        dateStyle: 'medium',
                                        timeStyle: 'short'
                                    })}
                                    </p>
                                )}
                            </div>
                        )}
                    </DialogContent>
                </Dialog>

                {/* ── Section 3: Community Savings ─────────────────────────────── */}
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Heart className="w-5 h-5 text-pink-500"/>
                            Community savings this year
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {savingsData && !savingsData.error ? (
                            <>
                                <div className="flex flex-wrap gap-6">
                                    <div>
                                        <p className="text-sm text-gray-500">💰 Your share of savings YTD</p>
                                        <p className="text-2xl font-bold text-pink-600">
                                            {fmtDollars(savingsData.your_share_cents)}
                                        </p>
                                    </div>
                                    <div>
                                        <p className="text-sm text-gray-500">Quarterly levy reduction equiv.</p>
                                        <p className="text-xl font-semibold text-green-600">
                                            ~{fmtDollars(savingsData.levy_reduction_equivalent_cents)}
                                        </p>
                                    </div>
                                    <div>
                                        <p className="text-sm text-gray-500">Building-wide savings YTD</p>
                                        <p className="text-xl font-semibold">
                                            {fmtDollars(savingsData.total_saved_ytd_cents)}
                                        </p>
                                    </div>
                                </div>
                                <a
                                    href="/financials/savings"
                                    className="text-sm text-blue-600 hover:underline"
                                >
                                    View full savings record →
                                </a>
                            </>
                        ) : (
                            <p className="text-gray-500 text-sm">Savings data not yet available for {fyLabel}.</p>
                        )}
                    </CardContent>
                </Card>

                {/* ── Section 4: Investment Snapshot ───────────────────────────── */}
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Home className="w-5 h-5 text-indigo-500"/>
                            Your investment snapshot
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                        <p className="text-sm text-gray-600">
                            <span className="font-semibold">Unit:</span> {activeUnit || '—'}
                        </p>
                        <p className="text-sm text-gray-500">
                            Track capital works impact, levy history, and property value trends.
                        </p>
                        <a
                            href="/financials/overview"
                            className="text-sm text-blue-600 hover:underline"
                        >
                            View full finance dashboard →
                        </a>
                    </CardContent>
                </Card>
            </div>
        </TooltipProvider>
    );
}
