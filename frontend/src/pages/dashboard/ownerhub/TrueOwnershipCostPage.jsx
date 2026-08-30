// @ts-nocheck
// @featuretrace:owner-hub — True Ownership Cost: per-property or per-unit cost of ownership, mortgage inputs, council rates.
// Layer: frontend
// Data flow: TrueOwnershipCostPage → GET /owner-hub/{properties,units,unit-tco,unit-mortgage/{unit}},
//            PUT /owner-hub/unit-mortgage/{unit}, GET/PUT /council-rates/settings
//            → owner_properties + units + unit_levy_ledger + settings (building-scoped).
// Related: backend/routers/ownerhub.py
//           frontend/src/hooks/useActiveUnit.ts (re-points the "By Unit" picker on a switch)
"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import {
    AlertCircle,
    Calculator,
    Check,
    ChevronDown,
    ChevronUp,
    ChevronsUpDown,
    DollarSign,
    Home,
    Loader2,
    RefreshCw,
    TrendingUp
} from 'lucide-react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip as RechartsTooltip, XAxis, YAxis } from 'recharts';
import { useAuth } from '../../../contexts/AuthContext';
import {useActiveUnit} from '../../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Badge } from '../../../components/ui/badge';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '../../../components/ui/popover';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '../../../components/ui/command';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../../components/ui/tooltip';
import { cn, formatCurrency } from '../../../lib/utils';

const ALLOWED_ROLES = ['owner', 'real_estate_agent', 'super_admin', 'strata_manager', 'ec_member'];
const ADMIN_ROLES = ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'];
/**
 * @generated FunctionHeader
 * Function: toFinancialYear
 * Path: frontend/src/pages/dashboardhub/TrueOwnershipCostPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const toFinancialYear = (year) => {
    // Australian financial year: calendar year N → FY "{N-1}-{last 2 digits of N}".
    // e.g. 2026 → "2025-26", 2025 → "2024-25"
    // A previous version computed `${y}-${(y+1)%100}` which produced the NEXT FY ("2026-27"),
    // causing the UAV settings fetch to target the wrong financial year.
    const y = parseInt(String(year || ''), 10);
    if (!Number.isFinite(y)) return '2025-26';
    const end = String(y % 100).padStart(2, '0');
    return `${y - 1}-${end}`;
};
/**
 * @generated FunctionHeader
 * Function: ownershipScoreColor
 * Path: frontend/src/pages/dashboardhub/TrueOwnershipCostPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ownershipScoreColor(score) {
    if (score >= 70) return 'bg-green-100 text-green-800';
    if (score >= 50) return 'bg-yellow-100 text-yellow-800';
    if (score >= 30) return 'bg-orange-100 text-orange-800';
    return 'bg-red-100 text-red-800';
}

// capital_replacement reads as "cost to replace/rebuild the building" (insurance
// reinstatement value) if shown under its raw key name. It is not insurance — it's
// the owner's forecast share of sinking-fund capital works — so it gets an explicit
// label override and an always-visible caption (see Cost Breakdown render below),
// not just a hover tooltip a user could miss.
const BREAKDOWN_LABEL_OVERRIDES = {
    capital_replacement: 'Capital Works (Sinking Fund Share)',
};

const CURRENT_YEAR = String(new Date().getFullYear());
const YEARS = [CURRENT_YEAR, String(new Date().getFullYear() - 1)];
/**
 * @generated FunctionHeader
 * Function: TrueOwnershipCostPage
 * Path: frontend/src/pages/dashboardhub/TrueOwnershipCostPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const TrueOwnershipCostPage = () => {
    const {user, api, loading: authLoading} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();

    // Properties mode (for registered owner_properties)
    const [properties, setProperties] = useState([]);
    const [selectedPropertyId, setSelectedPropertyId] = useState('');

    // Units mode (for admin / fallback when no properties)
    const [units, setUnits] = useState([]);
    const [selectedUnit, setSelectedUnit] = useState('');
    const [selectedYear, setSelectedYear] = useState(CURRENT_YEAR);
    const [useUnitMode, setUseUnitMode] = useState(false);

    // Searchable unit combobox state
    const [unitComboOpen, setUnitComboOpen] = useState(false);
    const [unitSearch, setUnitSearch] = useState('');

    const [results, setResults] = useState(null);
    const [loading, setLoading] = useState(true);
    const [calculating, setCalculating] = useState(false);
    const [formOpen, setFormOpen] = useState(false);
    const [inputs, setInputs] = useState({
        annual_rental_income: '', mortgage_balance: '', interest_rate: '',
        annual_insurance: '', council_rates: '', water_rates: '',
        property_mgmt_fee_pct: '', vacancy_weeks: '',
    });
    const [councilSettings, setCouncilSettings] = useState({
        block_auv: '',
        total_block_entitlement: '',
        is_configured: false,
    });
    const [savingCouncilSettings, setSavingCouncilSettings] = useState(false);
    const [loadingCouncilSettings, setLoadingCouncilSettings] = useState(false);

    const canAccess = user && ALLOWED_ROLES.includes(user.role);
    const isAdmin = user && ADMIN_ROLES.includes(user.role);

    // Read inside fetchData without making the active unit a fetch dependency —
    // switching units must re-point the selection, not re-run the whole load.
    const activeUnitRef = useRef(activeUnit);
    useEffect(() => {
        activeUnitRef.current = activeUnit;
    }, [activeUnit]);

    // Re-point the "By Unit" picker when the sidebar switches, but only to a unit
    // this page actually lists. An admin browsing another owner's unit keeps their
    // pick — the active unit has not changed, so nothing is re-applied.
    useEffect(() => {
        if (!activeUnit || !useUnitMode) return;
        if (!units.some(u => u.unit_number === activeUnit)) return;
        setSelectedUnit(prev => ( prev === activeUnit ? prev : activeUnit ));
    }, [activeUnit, useUnitMode, units]);

    // ── Fetch data on mount ──────────────────────────────────────────────────
    const fetchData = useCallback(async () => {
        setLoading(true);
        try {
            // Try registered properties first
            const propsRes = await api.get('/owner-hub/properties').catch(() => ( {data: []} ));
            const props = propsRes.data || [];
            setProperties(props);

            if (props.length > 0) {
                setSelectedPropertyId(props[ 0 ].id);
                setUseUnitMode(false);
            } else {
                // Fall back to units (admin) or user's own unit (owner)
                setUseUnitMode(true);
                const unitsRes = await api.get('/owner-hub/units').catch(() => ( {data: []} ));
                const allUnits = unitsRes.data || [];
                setUnits(allUnits);
                if (allUnits.length > 0) {
                    // Prefer the unit the sidebar switcher is on. `allUnits[0]` is an
                    // arbitrary pick among a multi-unit owner's lots, which showed one
                    // owner the wrong lot's cost of ownership with no signal it had
                    // chosen for them (GAP-IDENTITY-UNIT-SWITCH-001).
                    const preferred = allUnits.find(u => u.unit_number === activeUnitRef.current);
                    setSelectedUnit(( preferred || allUnits[ 0 ] ).unit_number);
                }
            }
        } catch {
            toast.error('Failed to load data');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        // Auth loading guard: `user` is briefly null/undefined before AuthContext's
        // session hydrates, which makes canAccess false on that first render — without
        // this guard, the redirect below fires for every real owner intermittently,
        // racing the session fetch (see rules/post-compact-critical.md).
        if (authLoading) return;
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchData();
    }, [authLoading, canAccess, fetchData, router]);

    // ── Calculate ────────────────────────────────────────────────────────────
    const calculate = useCallback(async () => {
        setCalculating(true);
        try {
            let res;
            if (useUnitMode && selectedUnit) {
                res = await api.get(`/owner-hub/unit-tco?unit_number=${selectedUnit}&year=${selectedYear}`);
            } else if (!useUnitMode && selectedPropertyId) {
                const filteredInputs = Object.fromEntries(
                    Object.entries(inputs).filter(([, v]) => v !== '' && v !== null && v !== undefined)
                );
                res = await api.post(`/owner-hub/properties/${selectedPropertyId}/tco`, filteredInputs);
            } else {
                return;
            }
            setResults(res.data || null);
        } catch {
            toast.error('Failed to calculate ownership cost');
        } finally {
            setCalculating(false);
        }
    }, [api, useUnitMode, selectedUnit, selectedYear, selectedPropertyId, inputs]);

    // Auto-calculate when selection changes
    useEffect(() => {
        if (useUnitMode && selectedUnit) calculate();
    }, [selectedUnit, selectedYear, useUnitMode]); // eslint-disable-line

    // Prefill the owner's saved mortgage for this unit (unit mode) so it shows on return.
    useEffect(() => {
        if (!(useUnitMode && selectedUnit)) return;
        let cancelled = false;
        api.get(`/owner-hub/unit-mortgage/${selectedUnit}`)
            .then(res => {
                if (cancelled) return;
                const m = res.data || {};
                setInputs(p => ({
                    ...p,
                    mortgage_balance: m.mortgage_balance != null ? String(m.mortgage_balance) : '',
                    interest_rate: m.interest_rate != null ? String(m.interest_rate) : '',
                }));
            })
            .catch(() => { /* no saved mortgage — leave the fields blank */ });
        return () => { cancelled = true; };
    }, [api, useUnitMode, selectedUnit]);

    // Save the owner's mortgage for the selected unit, then recalculate so the estimated
    // annual mortgage interest flows into the True Cost of Ownership breakdown.
    const saveUnitMortgage = useCallback(async () => {
        if (!(useUnitMode && selectedUnit)) return;
        const balance = parseFloat(inputs.mortgage_balance);
        const rate = parseFloat(inputs.interest_rate);
        if (!(balance >= 0) || !(rate >= 0)) {
            toast.error('Enter a mortgage balance and interest rate');
            return;
        }
        try {
            await api.put(`/owner-hub/unit-mortgage/${selectedUnit}`, {
                mortgage_balance: balance,
                interest_rate: rate,
            });
            toast.success('Mortgage saved');
            await calculate();
        } catch {
            toast.error('Failed to save mortgage');
        }
    }, [api, useUnitMode, selectedUnit, inputs.mortgage_balance, inputs.interest_rate, calculate]);

    useEffect(() => {
        if (!useUnitMode && selectedPropertyId) calculate();
    }, [selectedPropertyId, useUnitMode]); // eslint-disable-line

    // Filtered units for combobox — derived, never triggers re-renders
    const filteredUnits = useMemo(() => {
        if (!unitSearch.trim()) return units;
        const q = unitSearch.toLowerCase();
        return units.filter(u =>
            (u.unit_number || '').toLowerCase().includes(q) ||
            (u.owner_name || '').toLowerCase().includes(q)
        );
    }, [units, unitSearch]);

    const selectedUnitLabel = useMemo(() => {
        const u = units.find(u => u.unit_number === selectedUnit);
        return u ? `${u.unit_number}${u.owner_name ? ` — ${u.owner_name}` : ''}` : 'Select unit';
    }, [units, selectedUnit]);

    const fetchCouncilSettings = useCallback(async () => {
        if (!isAdmin || !useUnitMode) return;
        setLoadingCouncilSettings(true);
        try {
            const fy = toFinancialYear(selectedYear);
            const res = await api.get(`/council-rates/settings?financial_year=${fy}`);
            const data = res?.data || {};
            setCouncilSettings({
                block_auv: data.block_auv ?? '',
                total_block_entitlement: data.total_block_entitlement ?? '',
                is_configured: !!data.is_configured,
            });
        } catch {
            toast.error('Failed to load building UAV settings');
        } finally {
            setLoadingCouncilSettings(false);
        }
    }, [api, isAdmin, selectedYear, useUnitMode]);

    useEffect(() => {
        fetchCouncilSettings();
    }, [fetchCouncilSettings]);

    const saveCouncilSettings = useCallback(async () => {
        const parsedAuv = parseFloat(councilSettings.block_auv);
        const parsedEntitlement = parseFloat(councilSettings.total_block_entitlement);
        if (!parsedAuv || parsedAuv <= 0) {
            toast.error('Enter a valid block UAV amount from the rates notice.');
            return;
        }
        if (!parsedEntitlement || parsedEntitlement <= 0) {
            toast.error('Enter a valid total block entitlement.');
            return;
        }
        setSavingCouncilSettings(true);
        try {
            const fy = toFinancialYear(selectedYear);
            await api.put(`/council-rates/settings?financial_year=${fy}`, {
                block_auv: parsedAuv,
                total_block_entitlement: parsedEntitlement,
                is_configured: true,
            });
            toast.success('Building UAV settings saved. Rates/land tax will recompute on refresh.');
            await fetchCouncilSettings();
            await calculate();
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to save UAV settings';
            toast.error(msg);
        } finally {
            setSavingCouncilSettings(false);
        }
    }, [api, calculate, councilSettings.block_auv, councilSettings.total_block_entitlement, fetchCouncilSettings, selectedYear]);

    if (authLoading) return null;
    if (!canAccess) return null;

    const projection = results?.projection || results?.ten_year_projection || [];
    const breakdown = results?.breakdown || {};
    const netCashflow = results?.net_cashflow ?? null;
    const netYield = results?.net_yield ?? results?.net_yield_pct ?? null;
    const ownershipScore = results?.ownership_score ?? null;
    const totalCosts = results?.total_costs ?? null;
    const assumptions = results?.assumptions || [];

    // Normalise projection data key
    const chartData = projection.map(p => ( {
        year: p.year,
        cashflow: p.cashflow ?? p.net_cashflow ?? -( p.total_costs ?? 0 ),
        total_costs: p.total_costs ?? null,
        has_capital_works: p.has_capital_works ?? false,
    } ));
    /**
     * @generated FunctionHeader
     * Function: CustomDot
     * Path: frontend/src/pages/dashboardhub/TrueOwnershipCostPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const CustomDot = (props) => {
        const {cx, cy, payload} = props;
        if (payload.has_capital_works) {
            return <circle cx={cx} cy={cy} r={6} fill="#f59e0b" stroke="#fff" strokeWidth={2}/>;
        }
        return <circle cx={cx} cy={cy} r={3} fill={useUnitMode ? '#ef4444' : '#3b82f6'}/>;
    };

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">True Cost of Ownership</h1>
                    <p className="text-sm text-gray-500">Full financial picture including strata levies, rates, and
                        capital works</p>
                </div>
                <Button variant="outline" size="sm" onClick={calculate} disabled={calculating}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${calculating ? 'animate-spin' : ''}`}/>Recalculate
                </Button>
            </div>

            {/* Selector row */}
            {!loading && (
                <div className="flex items-center gap-3 flex-wrap">
                    {/* Mode toggle (admin only when both modes available) */}
                    {isAdmin && (
                        <div className="flex rounded-lg border border-gray-200 overflow-hidden text-xs">
                            <button
                                className={`px-3 py-1.5 ${!useUnitMode ? 'bg-slate-800 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                                onClick={() => {
                                    setUseUnitMode(false);
                                    setResults(null);
                                }}
                                disabled={properties.length === 0}
                            >My Properties
                            </button>
                            <button
                                className={`px-3 py-1.5 ${useUnitMode ? 'bg-slate-800 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                                onClick={() => {
                                    setUseUnitMode(true);
                                    setResults(null);
                                }}
                            >By Unit
                            </button>
                        </div>
                    )}

                    {/* Property selector */}
                    {!useUnitMode && properties.length > 1 && (
                        <>
                            <Label className="shrink-0 text-sm">Property:</Label>
                            <Select value={selectedPropertyId} onValueChange={setSelectedPropertyId}>
                                <SelectTrigger className="w-72"><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {properties.map(p => <SelectItem key={p.id} value={p.id}>{p.address}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </>
                    )}

                    {/* Unit selector — searchable combobox with scroll (replaces plain Select) */}
                    {useUnitMode && units.length > 0 && (
                        <>
                            <Label className="shrink-0 text-sm">Unit:</Label>
                            <Popover open={unitComboOpen} onOpenChange={setUnitComboOpen}>
                                <PopoverTrigger asChild>
                                    <Button
                                        variant="outline"
                                        role="combobox"
                                        aria-expanded={unitComboOpen}
                                        aria-label="Select a unit"
                                        className="w-56 justify-between font-normal truncate"
                                        data-testid="unit-combobox-trigger"
                                    >
                                        <span className="truncate">{selectedUnitLabel}</span>
                                        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50"/>
                                    </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-72 p-0" align="start" sideOffset={4}>
                                    <Command shouldFilter={false}>
                                        <CommandInput
                                            placeholder="Search unit or owner…"
                                            value={unitSearch}
                                            onValueChange={setUnitSearch}
                                            data-testid="unit-search-input"
                                        />
                                        <CommandList className="max-h-64 overflow-y-auto">
                                            <CommandEmpty>No units match &quot;{unitSearch}&quot;</CommandEmpty>
                                            <CommandGroup>
                                                {filteredUnits.map(u => (
                                                    <CommandItem
                                                        key={u.unit_number}
                                                        value={u.unit_number}
                                                        onSelect={(val) => {
                                                            setSelectedUnit(val);
                                                            setUnitSearch('');
                                                            setUnitComboOpen(false);
                                                        }}
                                                        data-testid={`unit-option-${u.unit_number}`}
                                                    >
                                                        <Check className={cn(
                                                            "mr-2 h-4 w-4 shrink-0",
                                                            selectedUnit === u.unit_number ? "opacity-100" : "opacity-0"
                                                        )}/>
                                                        <span className="font-medium">{u.unit_number}</span>
                                                        {u.owner_name && (
                                                            <span className="ml-2 text-slate-400 text-xs truncate">{u.owner_name}</span>
                                                        )}
                                                    </CommandItem>
                                                ))}
                                            </CommandGroup>
                                        </CommandList>
                                    </Command>
                                </PopoverContent>
                            </Popover>
                            <Label className="shrink-0 text-sm">Year:</Label>
                            <Select value={selectedYear} onValueChange={setSelectedYear}>
                                <SelectTrigger className="w-28"><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {YEARS.map(y => <SelectItem key={y} value={y}>{y}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </>
                    )}

                    {/* Input override (property mode only) */}
                    {!useUnitMode && (
                        <Button variant="outline" size="sm" onClick={() => setFormOpen(o => !o)} className="ml-auto">
                            {formOpen ? <ChevronUp className="h-4 w-4 mr-2"/> : <ChevronDown className="h-4 w-4 mr-2"/>}
                            {formOpen ? 'Hide' : 'Override'} Inputs
                        </Button>
                    )}
                </div>
            )}

            {/* Input override form */}
            {!useUnitMode && formOpen && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-sm">Input Overrides</CardTitle>
                        <CardDescription className="text-xs">Leave blank to use auto-populated values</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            {[
                                {key: 'annual_rental_income', label: 'Annual Rental Income ($)'},
                                {key: 'mortgage_balance', label: 'Mortgage Balance ($)'},
                                {key: 'interest_rate', label: 'Interest Rate (%)'},
                                {key: 'annual_insurance', label: 'Annual Insurance ($)'},
                                {key: 'council_rates', label: 'Council Rates ($)'},
                                {key: 'water_rates', label: 'Water Rates ($)'},
                                {key: 'property_mgmt_fee_pct', label: 'Mgmt Fee (%)'},
                                {key: 'vacancy_weeks', label: 'Vacancy Weeks/yr'},
                            ].map(({key, label}) => (
                                <div key={key} className="space-y-1">
                                    <Label className="text-xs">{label}</Label>
                                    <Input
                                        type="number" min="0" value={inputs[ key ]}
                                        onChange={e => setInputs(p => ( {...p, [ key ]: e.target.value} ))}
                                        placeholder="Auto" className="h-8 text-sm"
                                    />
                                </div>
                            ))}
                        </div>
                        <Button className="mt-4" size="sm" onClick={calculate} disabled={calculating}>
                            {calculating && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                            <Calculator className="mr-2 h-4 w-4"/>Calculate
                        </Button>
                    </CardContent>
                </Card>
            )}

            {/* Unit mode: the owner's personal mortgage, saved per unit so its estimated annual
                interest appears in the True Cost of Ownership breakdown. Private to the owner. */}
            {useUnitMode && selectedUnit && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-sm">Your mortgage (optional)</CardTitle>
                        <CardDescription className="text-xs">
                            Private to you and saved for this unit. Your estimated annual mortgage
                            interest is then shown in your True Cost of Ownership. Leave blank if you
                            have no mortgage on this property.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-1">
                                <Label className="text-xs">Mortgage Balance ($)</Label>
                                <Input
                                    type="number" min="0" value={inputs.mortgage_balance}
                                    onChange={e => setInputs(p => ({...p, mortgage_balance: e.target.value}))}
                                    placeholder="e.g. 450000" className="h-8 text-sm"
                                    data-testid="unit-mortgage-balance"
                                />
                            </div>
                            <div className="space-y-1">
                                <Label className="text-xs">Interest Rate (% p.a.)</Label>
                                <Input
                                    type="number" min="0" max="100" step="0.01" value={inputs.interest_rate}
                                    onChange={e => setInputs(p => ({...p, interest_rate: e.target.value}))}
                                    placeholder="e.g. 6.25" className="h-8 text-sm"
                                    data-testid="unit-mortgage-rate"
                                />
                            </div>
                        </div>
                        <Button className="mt-4" size="sm" onClick={saveUnitMortgage} disabled={calculating}
                                data-testid="save-unit-mortgage">
                            {calculating && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                            <Calculator className="mr-2 h-4 w-4"/>Save mortgage
                        </Button>
                    </CardContent>
                </Card>
            )}

            {/* Body */}
            {loading ? (
                <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-gray-400"/>
                </div>
            ) : !useUnitMode && properties.length === 0 ? (
                <Card>
                    <CardContent className="py-12 text-center space-y-4">
                        <Home className="h-12 w-12 text-gray-300 mx-auto"/>
                        <div>
                            <p className="font-semibold text-gray-700">No investment properties registered</p>
                            <p className="text-sm text-gray-500 mt-1">Register your property in <strong>Owner
                                Hub</strong> to see a full TCO analysis including rental income, vacancy costs, and
                                mortgage.</p>
                        </div>
                        {isAdmin && (
                            <p className="text-sm text-blue-600 font-medium">Tip: Switch to <button
                                className="underline" onClick={() => setUseUnitMode(true)}>By Unit</button> view to see
                                strata costs for any unit.</p>
                        )}
                    </CardContent>
                </Card>
            ) : !results ? (
                <Card>
                    <CardContent className="py-12 text-center">
                        {calculating ? <Loader2 className="h-8 w-8 animate-spin text-gray-400 mx-auto"/> :
                            <p className="text-gray-400">Select a unit to view costs</p>}
                    </CardContent>
                </Card>
            ) : (
                <>
                    {/* Unit info (unit mode) */}
                    {useUnitMode && results.unit_number && (
                        <div className="flex items-center gap-3 p-4 bg-slate-50 rounded-xl border border-slate-200">
                            <Home className="h-5 w-5 text-slate-500"/>
                            <div>
                                <p className="font-semibold text-slate-800">{results.unit_number} — {results.owner_name || 'No owner recorded'}</p>
                                <p className="text-xs text-slate-500">{results.unit_type} ·
                                    Entitlement: {results.entitlement} UOE · Levy Year {results.year}</p>
                            </div>
                            {totalCosts !== null && (
                                <div className="ml-auto text-right">
                                    <p className="text-xs text-slate-500">Total Annual Cost</p>
                                    <p className="font-bold text-lg text-slate-800">{formatCurrency(totalCosts)}</p>
                                </div>
                            )}
                        </div>
                    )}

                    {/* KPI badges */}
                    <div className="flex flex-wrap gap-3 items-center">
                        {netCashflow !== null && (
                            <div
                                className={`flex items-center gap-2 rounded-lg px-4 py-3 ${netCashflow >= 0 ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'}`}>
                                <DollarSign
                                    className={`h-5 w-5 ${netCashflow >= 0 ? 'text-green-600' : 'text-red-600'}`}/>
                                <div>
                                    <p className="text-xs text-gray-500">Annual Net Cashflow</p>
                                    <p className={`font-bold text-lg ${netCashflow >= 0 ? 'text-green-700' : 'text-red-700'}`}>{formatCurrency(netCashflow)}</p>
                                </div>
                            </div>
                        )}
                        {netYield !== null && (
                            <Badge className="text-sm px-3 py-1.5">Net Yield: {netYield.toFixed(2)}%</Badge>
                        )}
                        {ownershipScore !== null && (
                            <Badge className={`text-sm px-3 py-1.5 ${ownershipScoreColor(ownershipScore)}`}>
                                Ownership Score: {ownershipScore}/100
                            </Badge>
                        )}
                    </div>

                    {/* Cost Breakdown */}
                    {Object.keys(breakdown).length > 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base">Cost Breakdown</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-2">
                                    {Object.entries(breakdown).map(([key, val]) => (
                                        <div key={key}
                                             className="py-1.5 border-b border-gray-50 last:border-0">
                                          <div className="flex justify-between items-center text-sm">
                                           <div className="flex items-center gap-1 text-gray-600 capitalize">
                                               <span>{BREAKDOWN_LABEL_OVERRIDES[ key ] || key.replace(/_/g, ' ')}</span>
                                               {results?.breakdown_details?.[ key ]?.calculation && (
                                                   <TooltipProvider>
                                                       <Tooltip>
                                                           <TooltipTrigger asChild>
                                                               <button type="button"
                                                                       className="inline-flex text-gray-400 hover:text-gray-600"
                                                                       aria-label={`How ${( BREAKDOWN_LABEL_OVERRIDES[ key ] || key.replace(/_/g, ' ') )} is calculated`}>
                                                                   <AlertCircle className="h-3.5 w-3.5"/>
                                                               </button>
                                                           </TooltipTrigger>
                                                           <TooltipContent className="max-w-sm">
                                                               <p className="text-xs leading-relaxed">
                                                                   {results.breakdown_details[ key ].calculation}
                                                               </p>
                                                               {results.breakdown_details[ key ].source && (
                                                                   <p className="mt-1 text-[11px] text-slate-400">
                                                                       Source: {results.breakdown_details[ key ].source}
                                                                   </p>
                                                               )}
                                                           </TooltipContent>
                                                       </Tooltip>
                                                   </TooltipProvider>
                                               )}
                                           </div>
                                           <span
                                               className={`font-semibold ${val < 0 ? 'text-red-600' : 'text-gray-800'}`}>
                        {formatCurrency(val)}
                      </span>
                                          </div>
                                          {key === 'capital_replacement' && (
                                              <p className="text-[11px] text-slate-400 mt-0.5">
                                                  Not insurance — informational only, already covered by your
                                                  sinking levy above, not an extra charge.
                                              </p>
                                          )}
                                        </div>
                                    ))}
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    {/* Building UAV settings (manager/admin/EC/super-admin) */}
                    {useUnitMode && isAdmin && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base">Building UAV & Entitlement Settings</CardTitle>
                                <CardDescription className="text-xs">
                                    Enter the block UAV from the rates notice and total entitlement for this building.
                                    These values drive per-unit rates and land-tax allocation.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                {loadingCouncilSettings ? (
                                    <div className="text-sm text-slate-500">Loading current settings…</div>
                                ) : (
                                    <div className="grid gap-3 md:grid-cols-3">
                                        <div className="space-y-1">
                                            <Label className="text-xs">Block UAV (AUD)</Label>
                                            <Input
                                                type="number"
                                                min="0"
                                                value={councilSettings.block_auv}
                                                onChange={(e) => setCouncilSettings((prev) => ({
                                                    ...prev,
                                                    block_auv: e.target.value,
                                                }))}
                                                placeholder="4200000"
                                            />
                                        </div>
                                        <div className="space-y-1">
                                            <Label className="text-xs">Total Block Entitlement</Label>
                                            <Input
                                                type="number"
                                                min="1"
                                                value={councilSettings.total_block_entitlement}
                                                onChange={(e) => setCouncilSettings((prev) => ({
                                                    ...prev,
                                                    total_block_entitlement: e.target.value,
                                                }))}
                                                placeholder="10000"
                                            />
                                        </div>
                                        <div className="flex items-end">
                                            <Button onClick={saveCouncilSettings} disabled={savingCouncilSettings}>
                                                {savingCouncilSettings ? <Loader2 className="h-4 w-4 mr-2 animate-spin"/> : null}
                                                Save UAV Settings
                                            </Button>
                                        </div>
                                        <div className="md:col-span-3 text-xs text-slate-500">
                                            Status: {councilSettings.is_configured ? 'Configured from rates notice' : 'Using fallback/default values'}
                                        </div>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    )}

                    {/* 10-Year Projection Chart */}
                    {chartData.length > 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2">
                                    <TrendingUp className="h-4 w-4"/>10-Year Cost Projection
                                </CardTitle>
                                <CardDescription className="text-xs">
                    <span className="inline-flex items-center gap-1">
                      <span className="w-3 h-3 rounded-full bg-amber-400 inline-block"/>
                      Orange dots indicate capital works events
                    </span>
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={260}>
                                    <LineChart data={chartData}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0"/>
                                        <XAxis dataKey="year" tick={{fontSize: 11}}/>
                                        <YAxis tickFormatter={v => `$${( Math.abs(v) / 1000 ).toFixed(0)}k`}
                                               tick={{fontSize: 11}}/>
                                        <RechartsTooltip
                                            formatter={(v) => [formatCurrency(v), useUnitMode ? 'Annual Cost' : 'Net Cashflow']}/>
                                        <Legend/>
                                        <Line
                                            type="monotone"
                                            dataKey={useUnitMode ? 'total_costs' : 'cashflow'}
                                            name={useUnitMode ? 'Annual Cost' : 'Net Cashflow'}
                                            stroke={useUnitMode ? '#ef4444' : '#3b82f6'}
                                            strokeWidth={2.5}
                                            dot={<CustomDot/>}
                                            activeDot={{r: 6}}
                                        />
                                    </LineChart>
                                </ResponsiveContainer>
                                {assumptions.length > 0 && (
                                    <ul className="mt-4 pt-3 border-t border-gray-100 space-y-1">
                                        {assumptions.map((a, i) => (
                                            <li key={i} className="text-xs text-gray-500 flex items-start gap-1.5">
                                                <span className="text-gray-300 mt-0.5">•</span>
                                                <span>{a}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </CardContent>
                        </Card>
                    )}

                    {/* Unit mode note */}
                    {useUnitMode && (
                        <div
                            className="flex items-start gap-2 p-3 bg-blue-50 rounded-lg text-sm text-blue-700 border border-blue-100">
                            <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0"/>
                            <span>Unit mode shows strata levies, council rates, and capital works costs. Register the property in Owner Hub to include rental income, mortgage, and insurance analysis.</span>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default TrueOwnershipCostPage;
