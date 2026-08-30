"use client";
// @ts-nocheck
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../ui/table';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis, } from 'recharts';
import { formatCurrency } from '../../lib/utils';
import { ArrowRight } from 'lucide-react';

// All years from FY2021 up to and including current year
const CURRENT_FY = new Date().getFullYear();
const ALL_YEARS = Array.from({length: CURRENT_FY - 2021 + 1}, (_, i) => String(2021 + i));
// Last 4 years for bar chart (space-constrained)
const CHART_YEARS = ALL_YEARS.slice(-4);
/**
 * @generated FunctionHeader
 * Function: rateColor
 * Path: frontend/src/components/finance/CollectionRateDetailDialog.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const rateColor = (rate) => {
    if (rate >= 95) return 'text-emerald-600';
    if (rate >= 80) return 'text-amber-600';
    return 'text-rose-600';
};
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/components/finance/CollectionRateDetailDialog.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const statusBadge = (openingArrears, isHistorical) => {
    if (isHistorical) {
        if (openingArrears > 0.01) return <Badge
            className="text-xs bg-rose-100 text-rose-700 border-rose-200">Arrears</Badge>;
        return <Badge className="text-xs bg-green-100 text-green-700 border-green-200">Paid Up</Badge>;
    }
    if (openingArrears > 0.01) return <Badge
        className="text-xs bg-rose-100 text-rose-700 border-rose-200">Arrears</Badge>;
    return <Badge className="text-xs bg-green-100 text-green-700 border-green-200">On Track</Badge>;
};
/**
 * @generated FunctionHeader
 * Function: CollectionRateDetailDialog
 * Path: frontend/src/components/finance/CollectionRateDetailDialog.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CollectionRateDetailDialog({open, onOpenChange, selectedYear = '2026'}) {
    const {api} = useAuth();
    const router = useRouter();

    const [activeYear, setActiveYear] = useState(selectedYear);
    const [kpisMap, setKpisMap] = useState({});
    const [units, setUnits] = useState([]);
    const [ledgerByYear, setLedgerByYear] = useState({});
    const [loadingKpis, setLoadingKpis] = useState(false);
    const [loadingUnits, setLoadingUnits] = useState(false);

    // Fetch KPIs for all years when dialog opens
    useEffect(() => {
        if (!open || Object.keys(kpisMap).length > 0) return;
        setLoadingKpis(true);
        // METRIC[collection_rate|total_opening_arrears|total_obligations]: source endpoint.
        // All three metrics consumed below come from /stats/building-kpis.
        Promise.allSettled(ALL_YEARS.map(yr => api.get(`/stats/building-kpis?financial_year=${yr}`)))
            .then(results => {
                const map = {};
                results.forEach((r, i) => {
                    if (r.status === 'fulfilled') map[ ALL_YEARS[ i ] ] = r.value.data;
                });
                setKpisMap(map);
            })
            .finally(() => setLoadingKpis(false));
    }, [open, api, kpisMap]);

    // Fetch unit list (with opening_arrears) once
    useEffect(() => {
        if (!open || units.length > 0) return;
        setLoadingUnits(true);
        api.get('/owners-units?limit=200')
            .then(res => setUnits(Array.isArray(res.data) ? res.data : ( res.data?.units || [] )))
            .catch(() => setUnits([]))
            .finally(() => setLoadingUnits(false));
    }, [open, api, units.length]);

    // Fetch levy ledger for the active year (for total_paid per unit)
    useEffect(() => {
        if (!open || !activeYear || ledgerByYear[ activeYear ]) return;
        api.get(`/unit-levy-ledger?year=${activeYear}&limit=200`)
            .then(res => {
                const entries = Array.isArray(res.data) ? res.data : [];
                const paidMap = {};
                entries.forEach(e => {
                    paidMap[ e.unit_number ] = e.total_paid || 0;
                });
                setLedgerByYear(prev => ( {...prev, [ activeYear ]: paidMap} ));
            })
            .catch(() => {
            });
    }, [open, activeYear, api, ledgerByYear]);

    // Sync activeYear with parent's selectedYear
    useEffect(() => {
        setActiveYear(selectedYear);
    }, [selectedYear]);

    // Chart uses last 4 years to keep bars readable; selector shows all years
    const chartData = CHART_YEARS.map(yr => ( {
        year: `FY${yr}`,
        rate: parseFloat(( kpisMap[ yr ]?.collection_rate ?? 0 ).toFixed(1)),
    } ));

    const currentKpi = kpisMap[ activeYear ] || {};
    const totalLevied = currentKpi.total_levied ?? 0;
    const totalOpeningArrears = currentKpi.total_opening_arrears ?? 0;
    const totalObligations = currentKpi.total_obligations ?? ( totalOpeningArrears + totalLevied );
    const confirmedPaid = currentKpi.confirmed_paid ?? 0;
    const collectionRate = parseFloat(( currentKpi.collection_rate ?? 0 ).toFixed(1));
    const unitsInArrears = currentKpi.units_in_arrears ?? 0;
    const isHistoricalYear = parseInt(activeYear) < new Date().getFullYear();
    const todayAU = new Date().toLocaleDateString('en-AU', {day: 'numeric', month: 'long', year: 'numeric'});

    // Build paidMap for the active year from ledger data
    const paidMap = ledgerByYear[ activeYear ] || {};

    // Filter: show units in good standing (no opening arrears carry-forward).
    // Sort by highest total_paid for the active year (best payers first).
    const goodPayers = units.filter(u => ( ( u.admin_opening || 0 ) + ( u.sinking_opening || 0 ) ) <= 0.01);
    const sortedUnits = [...goodPayers].sort((a, b) => {
        const aPaid = paidMap[ a.unit_number ] || 0;
        const bPaid = paidMap[ b.unit_number ] || 0;
        if (bPaid !== aPaid) return bPaid - aPaid;  // highest paid first
        // secondary: credits first (most negative opening)
        const aOp = ( a.admin_opening || 0 ) + ( a.sinking_opening || 0 );
        const bOp = ( b.admin_opening || 0 ) + ( b.sinking_opening || 0 );
        return aOp - bOp;
    });

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="sm:max-w-4xl rounded-[2rem] p-0 overflow-hidden border-none max-h-[90vh] flex flex-col">
                {/* Header */}
                <div className="p-8 bg-gradient-to-r from-indigo-900 to-slate-900 text-white shrink-0">
                    <DialogHeader>
                        <DialogTitle className="text-2xl font-black">Collection Rate Analysis</DialogTitle>
                        <DialogDescription className="text-slate-400">
                            Levy collection performance by year and per-unit payment status.
                        </DialogDescription>
                    </DialogHeader>

                    {/* Year selector — all available years */}
                    <div className="flex flex-wrap gap-2 mt-4">
                        {ALL_YEARS.map(yr => (
                            <button
                                key={yr}
                                onClick={() => setActiveYear(yr)}
                                className={`px-4 py-1.5 rounded-xl text-sm font-bold transition-all ${
                                    activeYear === yr
                                        ? 'bg-white text-slate-900'
                                        : 'bg-white/10 text-white hover:bg-white/20'
                                }`}
                            >
                                FY {yr}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="overflow-auto flex-1 p-8 space-y-6 bg-white">
                    {/* Summary cards */}
                    <div className="grid grid-cols-3 gap-4">
                        <div className="p-4 rounded-2xl border-2 border-indigo-100 bg-indigo-50">
                            <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Total
                                Obligations</p>
                            <p className="text-xl font-black text-slate-900">{formatCurrency(totalObligations)}</p>
                            {isHistoricalYear ? (
                                <p className="text-xs text-slate-500 mt-0.5">FY {activeYear} annual levy</p>
                            ) : (
                                <p className="text-xs text-slate-500 mt-0.5">
                                    Opening: {formatCurrency(totalOpeningArrears)} + FY
                                    Levy: {formatCurrency(totalLevied)}
                                </p>
                            )}
                        </div>
                        <div className="p-4 rounded-2xl border-2 border-emerald-100 bg-emerald-50">
                            <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Confirmed
                                Collected</p>
                            <p className="text-xl font-black text-emerald-700">{formatCurrency(confirmedPaid)}</p>
                            <p className="text-xs text-slate-500 mt-0.5">In-system payments</p>
                        </div>
                        <div
                            className={`p-4 rounded-2xl border-2 ${collectionRate >= 95 ? 'border-emerald-200 bg-emerald-50' : collectionRate >= 80 ? 'border-amber-200 bg-amber-50' : 'border-rose-200 bg-rose-50'}`}>
                            <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Collection
                                Rate</p>
                            <p className={`text-xl font-black ${rateColor(collectionRate)}`}>{collectionRate}%</p>
                            <p className="text-xs text-slate-500 mt-0.5">{unitsInArrears} units outstanding</p>
                            {!isHistoricalYear && (
                                <p className="text-xs text-slate-400 mt-0.5">As of {todayAU}</p>
                            )}
                        </div>
                    </div>

                    {/* 3-year bar chart */}
                    <div>
                        <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-3">3-Year Collection
                            Rate Trend</p>
                        {loadingKpis ? (
                            <div className="h-36 bg-slate-50 animate-pulse rounded-xl"/>
                        ) : (
                            <ResponsiveContainer width="100%" height={150}>
                                <BarChart data={chartData} margin={{top: 4, right: 4, left: 0, bottom: 4}}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                                    <XAxis dataKey="year" tick={{fontSize: 11, fontWeight: 700}}/>
                                    <YAxis domain={[-20, 105]} tickFormatter={v => `${v}%`} tick={{fontSize: 10}}/>
                                    <Tooltip formatter={(v) => `${v}%`}/>
                                    <Bar dataKey="rate" radius={[6, 6, 0, 0]}>
                                        {chartData.map((entry, idx) => (
                                            <Cell key={idx}
                                                  fill={entry.rate >= 95 ? '#10b981' : entry.rate >= 80 ? '#f59e0b' : '#f43f5e'}/>
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        )}
                    </div>

                    {/* DEFT/BPAY callout */}
                    <div className="p-4 rounded-xl border border-amber-200 bg-amber-50 text-amber-800 text-xs">
                        <p className="font-bold mb-1">Note — DEFT/BPAY External Payments</p>
                        <p>
                            The <strong>current-year collection rate</strong> reflects only in-platform confirmed
                            payments (portal BPAY/bank transfer). Most owners pay via DEFT/BPAY directly — those
                            payments are <em>not</em> recorded here, so the rate will appear low early in the year.
                            {' '}
                            <strong>Historical years</strong> (2024–2025) use next-year opening arrears to derive the
                            true end-of-year collection rate, bypassing the DEFT/BPAY gap.
                        </p>
                    </div>

                    {/* Per-unit table — good payers only, sorted by highest FY payment */}
                    <div>
                        <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-3">
                            Units in Good Standing — Sorted by Highest FY{activeYear} Payment
                            {!loadingUnits && ` (${Math.min(sortedUnits.length, 30)} of ${sortedUnits.length})`}
                        </p>
                        {loadingUnits ? (
                            <div className="space-y-2">
                                {[...Array(5)].map((_, i) => <div key={i}
                                                                  className="h-10 bg-slate-50 animate-pulse rounded-xl"/>)}
                            </div>
                        ) : sortedUnits.length === 0 ? (
                            <div className="p-6 text-center text-slate-400 border rounded-xl bg-slate-50 text-sm">
                                All units carry an opening arrears balance — no credit or paid-up units found.
                            </div>
                        ) : (
                            <div className="border rounded-xl overflow-hidden">
                                <Table>
                                    <TableHeader>
                                        <TableRow className="bg-slate-50">
                                            <TableHead
                                                className="text-xs font-black uppercase tracking-widest">Unit</TableHead>
                                            <TableHead
                                                className="text-xs font-black uppercase tracking-widest">Owner</TableHead>
                                            <TableHead
                                                className="text-xs font-black uppercase tracking-widest text-right">Total
                                                Paid</TableHead>
                                            <TableHead
                                                className="text-xs font-black uppercase tracking-widest text-right">Opening
                                                Balance</TableHead>
                                            <TableHead
                                                className="text-xs font-black uppercase tracking-widest text-center">Status</TableHead>
                                            <TableHead className="w-16"/>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sortedUnits.slice(0, 30).map(unit => {
                                            const openingArrears = ( unit.admin_opening || 0 ) + ( unit.sinking_opening || 0 );
                                            const isHistorical = parseInt(activeYear) < new Date().getFullYear();
                                            const totalPaid = paidMap[ unit.unit_number ] || 0;
                                            return (
                                                <TableRow key={unit.unit_number} className="hover:bg-slate-50/50">
                                                    <TableCell
                                                        className="font-bold text-sm">{unit.unit_number}</TableCell>
                                                    <TableCell
                                                        className="text-sm text-slate-600">{unit.owner_name}</TableCell>
                                                    <TableCell className="text-right text-sm font-mono">
                                                        {totalPaid > 0 ? (
                                                            <span
                                                                className="text-emerald-700 font-bold">{formatCurrency(totalPaid)}</span>
                                                        ) : (
                                                            <span className="text-slate-400">—</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-right text-sm font-mono">
                                                        {openingArrears < -0.01 ? (
                                                            <span
                                                                className="text-emerald-600 font-bold">{formatCurrency(Math.abs(openingArrears))} CR</span>
                                                        ) : (
                                                            <span className="text-slate-400">—</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-center">
                                                        {statusBadge(openingArrears, isHistorical)}
                                                    </TableCell>
                                                    <TableCell>
                                                        <button
                                                            onClick={() => {
                                                                onOpenChange(false);
                                                                router.push(`/financials/unit/${unit.unit_number}`);
                                                            }}
                                                            className="p-1.5 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                                                        >
                                                            <ArrowRight size={14}/>
                                                        </button>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                                {sortedUnits.length > 30 && (
                                    <p className="text-xs text-center text-slate-400 py-3">Showing top 30
                                        of {sortedUnits.length} good-standing units</p>
                                )}
                            </div>
                        )}
                    </div>
                </div>

                {/* Footer */}
                <div className="p-6 bg-slate-50 border-t flex justify-end gap-4 shrink-0">
                    <Button variant="outline" onClick={() => onOpenChange(false)}
                            className="rounded-xl font-bold">Close</Button>
                    <Button onClick={() => {
                        onOpenChange(false);
                        router.push('/intelligence/debt-recovery');
                    }} className="rounded-xl font-bold bg-indigo-600">
                        View Recovery Board
                    </Button>
                </div>
            </DialogContent>
        </Dialog>
    );
}
