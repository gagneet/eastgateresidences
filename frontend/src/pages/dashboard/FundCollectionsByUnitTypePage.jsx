"use client";
// @featuretrace:levy — Life-to-date Admin/Sinking Fund collections by unit type.
// Layer: frontend
// Data flow: this page -> GET /finance/fund-collections-by-unit-type -> finance.py
//            -> utils.finance_helpers.get_fund_collections_by_unit_type() -> unit_levy_ledger
//            + units (building-scoped).
// Related: backend/routers/finance.py, backend/utils/finance_helpers.py
// Toggle: fund_collections_by_unit_type_report
// Collection: unit_levy_ledger, units
import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../components/ui/table';
import {
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { ArrowLeft, ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { formatCurrency } from '../../lib/utils';

const FundCollectionsByUnitTypePage = () => {
    const router = useRouter();
    const { api } = useAuth();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [data, setData] = useState(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.get('/finance/fund-collections-by-unit-type');
            setData(res.data || null);
        } catch (err) {
            console.error('Failed to fetch /finance/fund-collections-by-unit-type', err);
            setError('Could not load fund collections data.');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    if (loading) return (
        <div className="flex items-center justify-center min-h-[400px]">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"/>
        </div>
    );

    if (error || !data) return (
        <div className="min-h-screen bg-slate-50/50 p-6 md:p-10">
            <p className="text-sm text-rose-600 font-medium">{error || 'No data available.'}</p>
        </div>
    );

    const byUnitType = data.by_unit_type || {};
    const unitTypes = Object.keys(byUnitType);

    const chartData = unitTypes.map((ut) => ({
        name: ut,
        Admin: byUnitType[ut].admin_collected_to_date || 0,
        Sinking: byUnitType[ut].sinking_collected_to_date || 0,
    }));

    return (
        <div className="min-h-screen bg-slate-50/50 p-6 md:p-10 space-y-6 pb-24">
            <nav className="flex items-center gap-2 text-xs font-bold text-slate-400 uppercase tracking-widest mb-4">
                <Link href="/financials/overview" className="hover:text-indigo-600 transition-colors">Financials</Link>
                <ChevronRight size={10}/>
                <span className="text-slate-900">Fund Collections by Unit Type</span>
            </nav>

            <div className="flex items-center gap-4">
                <button
                    onClick={() => router.back()}
                    className="p-2 rounded-xl border bg-white hover:bg-slate-50 transition-colors"
                >
                    <ArrowLeft size={18} className="text-slate-500"/>
                </button>
                <div>
                    <h1 className="text-2xl font-black text-slate-900">Fund Collections by Unit Type</h1>
                    <p className="text-slate-500 text-sm font-medium mt-0.5">
                        Life-to-date Admin Fund and Sinking Fund collections
                        {data.years_included?.length > 0 && (
                            <> — {data.years_included[0]} to {data.years_included[data.years_included.length - 1]}</>
                        )}
                    </p>
                </div>
            </div>

            {data.postgres_promotion_pending && (
                <Card className="rounded-2xl border-0 shadow-sm bg-amber-50">
                    <CardContent className="p-4 text-xs text-amber-700 font-medium">
                        This building's finance ledger has been promoted toward PostgreSQL, but this report
                        still reads from MongoDB — a Postgres-native version has not been built yet.
                    </CardContent>
                </Card>
            )}

            {/* Headline totals */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-indigo-600"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                            Admin Fund Collected
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-black text-slate-900">
                            {formatCurrency(data.admin_fund?.collected_to_date)}
                        </p>
                        <p className="text-xs text-slate-500 mt-1">
                            of {formatCurrency(data.admin_fund?.due_to_date)} due to date
                        </p>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-emerald-500"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                            Sinking Fund Collected
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-black text-slate-900">
                            {formatCurrency(data.sinking_fund?.collected_to_date)}
                        </p>
                        <p className="text-xs text-slate-500 mt-1">
                            of {formatCurrency(data.sinking_fund?.due_to_date)} due to date
                        </p>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-slate-900"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                            Total Collected
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-black text-slate-900">
                            {formatCurrency(data.total_collected_to_date)}
                        </p>
                        <p className="text-xs text-slate-500 mt-1">Admin + Sinking, both funds combined</p>
                    </CardContent>
                </Card>
            </div>

            {/* Chart by unit type */}
            {chartData.length > 0 && (
                <Card className="rounded-2xl border-0 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-sm font-black text-slate-900">Collected by Unit Type</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div style={{ width: '100%', height: 320 }}>
                            <ResponsiveContainer>
                                <BarChart data={chartData}>
                                    <CartesianGrid strokeDasharray="3 3"/>
                                    <XAxis dataKey="name"/>
                                    <YAxis tickFormatter={(v) => formatCurrency(v)}/>
                                    <RechartsTooltip formatter={(v) => formatCurrency(v)}/>
                                    <Legend/>
                                    <Bar dataKey="Admin" fill="#4f46e5"/>
                                    <Bar dataKey="Sinking" fill="#10b981"/>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Table breakdown */}
            <Card className="rounded-2xl border-0 shadow-sm">
                <CardHeader>
                    <CardTitle className="text-sm font-black text-slate-900">Breakdown by Unit Type</CardTitle>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Unit Type</TableHead>
                                <TableHead className="text-right">Admin Collected</TableHead>
                                <TableHead className="text-right">Admin Due</TableHead>
                                <TableHead className="text-right">Sinking Collected</TableHead>
                                <TableHead className="text-right">Sinking Due</TableHead>
                                <TableHead className="text-right">Total Collected</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {unitTypes.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="text-center text-sm text-slate-400 py-8">
                                        No unit levy ledger records found for this building.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                unitTypes.map((ut) => {
                                    const row = byUnitType[ut];
                                    return (
                                        <TableRow key={ut}>
                                            <TableCell className="font-semibold">{ut}</TableCell>
                                            <TableCell className="text-right">{formatCurrency(row.admin_collected_to_date)}</TableCell>
                                            <TableCell className="text-right text-slate-500">{formatCurrency(row.admin_due_to_date)}</TableCell>
                                            <TableCell className="text-right">{formatCurrency(row.sinking_collected_to_date)}</TableCell>
                                            <TableCell className="text-right text-slate-500">{formatCurrency(row.sinking_due_to_date)}</TableCell>
                                            <TableCell className="text-right font-bold">{formatCurrency(row.total_collected_to_date)}</TableCell>
                                        </TableRow>
                                    );
                                })
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
};

export default FundCollectionsByUnitTypePage;
