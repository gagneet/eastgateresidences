"use client";

import React, {useState} from "react";
import {motion} from "framer-motion";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Tabs, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Input} from "@/components/ui/input";
import {Badge} from "@/components/ui/badge";
import {ArrowDownRight, ArrowUpRight, BarChart3, Download, Search} from "lucide-react";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

interface CategoryRow {
    name: string;
    fund_type: string;
    budgeted_amount: number;
    actual_amount: number;
}

interface BudgetVarianceDetailedPremiumProps {
    categories: CategoryRow[];
    year: string;
}
/**
 * @generated FunctionHeader
 * Function: BudgetVarianceDetailedPremium
 * Path: frontend/src/components/finance/premium/BudgetVarianceDetailedPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const BudgetVarianceDetailedPremium = ({categories, year}: BudgetVarianceDetailedPremiumProps) => {
    const [fundFilter, setFundFilter] = useState<"all" | "administrative" | "sinking">("all");
    const [searchTerm, setSearchTerm] = useState("");

    // Include items with actual spending even if budgeted is 0 (unbudgeted expenditure)
    const filteredRows = categories
        .filter(c => c.budgeted_amount > 0 || c.actual_amount > 0)
        .filter(c => fundFilter === "all" || c.fund_type === fundFilter)
        .filter(c => c.name.toLowerCase().includes(searchTerm.toLowerCase()))
        .map(c => {
            const variance = c.actual_amount - c.budgeted_amount;
            const variancePct = c.budgeted_amount > 0 ? (variance / c.budgeted_amount) * 100 : (c.actual_amount > 0 ? 100 : 0);
            const isUnbudgeted = c.budgeted_amount === 0 && c.actual_amount > 0;
            return {...c, variance, variancePct, isUnbudgeted};
        })
        .sort((a, b) => Math.abs(b.variancePct) - Math.abs(a.variancePct));
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/components/finance/premium/BudgetVarianceDetailedPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = () => {
        const csv = [
            ["Category", "Fund", "Budgeted", "Actual", "Variance", "Variance %"],
            ...filteredRows.map((r) => [r.name, r.fund_type, r.budgeted_amount, r.actual_amount, r.variance, r.variancePct.toFixed(2) + "%"]),
        ].map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `budget_variance_${year}.csv`;
        a.click();
    };

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full"
        >
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Budget Forensics</h3>
                        <BarChart3 className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Budget Forensics"
                            description="A granular audit of building expenditures, comparing actual year-to-date spending against the original approved budget categories."
                            dataSources={["levy_categories", "financial_transactions"]}
                            logic="Variance is calculated as (Actual - Budgeted). Items with $0 budgeted amount are excluded to focus on deviation from the planned fiscal path. Strategic alerts are triggered for items exceeding 10% variance."
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">Detailed category-level variance analysis</p>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                    {/* Tremor TabGroup was index-addressed; shadcn Tabs bind the
                        state string directly (see ForecastChartPremium). */}
                    <Tabs value={fundFilter} onValueChange={(v) => setFundFilter(v as any)}>
                        <TabsList>
                            <TabsTrigger value="all" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">All</TabsTrigger>
                            <TabsTrigger value="administrative" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">Admin</TabsTrigger>
                            <TabsTrigger value="sinking" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">Sinking</TabsTrigger>
                        </TabsList>
                    </Tabs>

                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground"/>
                        <Input
                            placeholder="Search categories..."
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            className="pl-10 rounded-xl"
                        />
                    </div>

                    <button
                        onClick={handleExport}
                        className="p-2.5 rounded-xl bg-card border border-border text-muted-foreground hover:text-primary hover:border-primary/20 transition-all shadow-sm"
                    >
                        <Download className="w-4 h-4"/>
                    </button>
                </div>
            </div>

            <div className="overflow-hidden rounded-2xl border border-border bg-card/50">
                <Table>
                    <TableHeader className="bg-muted/50">
                        <TableRow>
                            <TableHead
                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Category</TableHead>
                            <TableHead
                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Fund</TableHead>
                            <TableHead
                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground text-right">Budgeted</TableHead>
                            <TableHead
                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground text-right">Actual</TableHead>
                            <TableHead
                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground text-right">Variance</TableHead>
                            <TableHead
                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground text-right">Analysis</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {filteredRows.map((item) => (
                            <TableRow key={`${item.name}-${item.fund_type}`}
                                      className="hover:bg-card/80 transition-colors">
                                <TableCell className="text-sm font-bold text-foreground">
                                    <div className="flex items-center gap-2">
                                        {item.name}
                                        {item.isUnbudgeted && (
                                            <Badge variant="secondary"
                                                   className="text-[8px] font-semibold uppercase">Unbudgeted</Badge>
                                        )}
                                    </div>
                                </TableCell>
                                <TableCell>
                                    <Badge variant="outline"
                                           className="text-[9px] font-semibold uppercase">
                                        {item.fund_type === 'administrative' ? 'Admin' : 'Sinking'}
                                    </Badge>
                                </TableCell>
                                <TableCell className="text-right text-sm font-medium text-muted-foreground">
                                    {item.isUnbudgeted ? <span
                                        className="text-muted-foreground italic">$—</span> : formatCurrency(item.budgeted_amount)}
                                </TableCell>
                                <TableCell
                                    className="text-right text-sm font-semibold text-foreground">{formatCurrency(item.actual_amount)}</TableCell>
                                <TableCell
                                    className={`text-right text-sm font-semibold ${item.variance > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                                    {item.variance > 0 ? '+' : ''}{formatCurrency(item.variance)}
                                </TableCell>
                                <TableCell className="text-right">
                                    <div className="flex items-center justify-end gap-1.5">
                    <span
                        className={`text-xs font-semibold ${item.variancePct > 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                      {item.isUnbudgeted ? '∞' : `${item.variancePct > 0 ? '+' : ''}${item.variancePct.toFixed(1)}%`}
                    </span>
                                        {item.variancePct > 0 ? (
                                            <ArrowUpRight className="w-3.5 h-3.5 text-rose-500"/>
                                        ) : (
                                            <ArrowDownRight className="w-3.5 h-3.5 text-emerald-500"/>
                                        )}
                                    </div>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>

            <div className="mt-6 flex items-center justify-between">
                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
                    Showing {filteredRows.length} active budget categories
                </p>
                <div className="flex gap-4">
                    <div className="flex items-center gap-1.5">
                        <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"/>
                        <span
                            className="text-[9px] font-semibold text-muted-foreground uppercase tracking-tighter">Under Budget</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <div className="w-1.5 h-1.5 rounded-full bg-rose-500"/>
                        <span
                            className="text-[9px] font-semibold text-muted-foreground uppercase tracking-tighter">Over Budget</span>
                    </div>
                </div>
            </div>
        </motion.div>
    );
};

export default BudgetVarianceDetailedPremium;
