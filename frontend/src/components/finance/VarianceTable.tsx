"use client";
import React, {useState} from "react";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import {ArrowUpDown, Download} from "lucide-react";

interface CategoryRow {
    name: string;
    fund_type: string;
    budgeted_amount: number;
    actual_amount: number;
}

interface VarianceTableProps {
    categories: CategoryRow[];
    year: string;
}

type SortField = "name" | "budgeted" | "actual" | "variance" | "variance_pct";
type SortDir = "asc" | "desc";
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/finance/VarianceTable.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) =>
    `$${Math.abs(v).toLocaleString("en-AU", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
/**
 * @generated FunctionHeader
 * Function: VarianceTable
 * Path: frontend/src/components/finance/VarianceTable.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const VarianceTable: React.FC<VarianceTableProps> = ({categories, year}) => {
    const [fundFilter, setFundFilter] = useState<"all" | "administrative" | "sinking">("all");
    const [sortField, setSortField] = useState<SortField>("variance_pct");
    const [sortDir, setSortDir] = useState<SortDir>("desc");
    /**
     * @generated FunctionHeader
     * Function: handleSort
     * Path: frontend/src/components/finance/VarianceTable.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSort = (field: SortField) => {
        if (sortField === field) {
            setSortDir(sortDir === "asc" ? "desc" : "asc");
        } else {
            setSortField(field);
            setSortDir("desc");
        }
    };

    const rows = categories
        .filter((c) => fundFilter === "all" || c.fund_type === fundFilter)
        .map((c) => {
            const budgeted = c.budgeted_amount || 0;
            const actual = c.actual_amount || 0;
            const variance = actual - budgeted;
            const variance_pct = budgeted > 0 ? (variance / budgeted) * 100 : 0;
            return {...c, budgeted, actual, variance, variance_pct};
        })
        .sort((a, b) => {
            /**
             * @generated FunctionHeader
             * Function: val
             * Path: frontend/src/components/finance/VarianceTable.tsx
             *
             * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
             */
            const val = (x: typeof a) => {
                if (sortField === "name") return x.name;
                if (sortField === "budgeted") return x.budgeted;
                if (sortField === "actual") return x.actual;
                if (sortField === "variance") return x.variance;
                return x.variance_pct;
            };
            const av = val(a);
            const bv = val(b);
            if (typeof av === "string") return sortDir === "asc" ? av.localeCompare(bv as string) : (bv as string).localeCompare(av);
            return sortDir === "asc" ? (av as number) - (bv as number) : (bv as number) - (av as number);
        });
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/components/finance/VarianceTable.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = () => {
        const csv = [
            ["Category", "Fund", "Budgeted", "Actual", "Variance", "Variance %"],
            ...rows.map((r) => [r.name, r.fund_type, r.budgeted, r.actual, r.variance, r.variance_pct.toFixed(2) + "%"]),
        ].map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `variance_${year}.csv`;
        a.click();
    };
    /**
     * @generated FunctionHeader
     * Function: getVarianceStyle
     * Path: frontend/src/components/finance/VarianceTable.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getVarianceStyle = (pct: number) => {
        if (pct > 10) return "text-red-600 font-semibold";
        if (pct < -1) return "text-emerald-600 font-semibold";
        return "text-amber-600";
    };
    /**
     * @generated FunctionHeader
     * Function: SortButton
     * Path: frontend/src/components/finance/VarianceTable.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const SortButton: React.FC<{ field: SortField; label: string }> = ({field, label}) => (
        <button
            className="flex items-center gap-1 hover:text-gray-900"
            onClick={() => handleSort(field)}
        >
            {label}
            <ArrowUpDown className="w-3 h-3"/>
        </button>
    );

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <CardTitle>Budget vs Actual Variance</CardTitle>
                        <CardDescription>{year} — {rows.length} categories</CardDescription>
                    </div>
                    <div className="flex gap-2">
                        {(["all", "administrative", "sinking"] as const).map((f) => (
                            <Button
                                key={f}
                                size="sm"
                                variant={fundFilter === f ? "default" : "outline"}
                                onClick={() => setFundFilter(f)}
                                className="capitalize text-xs"
                            >
                                {f === "all" ? "All" : f === "administrative" ? "Admin" : "Sinking"}
                            </Button>
                        ))}
                        <Button size="sm" variant="outline" onClick={handleExport}>
                            <Download className="w-4 h-4 mr-1"/> CSV
                        </Button>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="p-0">
                <div className="overflow-auto max-h-[450px]">
                    <Table>
                        <TableHeader className="sticky top-0 bg-white z-10">
                            <TableRow>
                                <TableHead className="w-48"><SortButton field="name" label="Category"/></TableHead>
                                <TableHead>Fund</TableHead>
                                <TableHead className="text-right"><SortButton field="budgeted"
                                                                              label="Budgeted"/></TableHead>
                                <TableHead className="text-right"><SortButton field="actual"
                                                                              label="Actual"/></TableHead>
                                <TableHead className="text-right"><SortButton field="variance"
                                                                              label="Variance"/></TableHead>
                                <TableHead className="text-right"><SortButton field="variance_pct"
                                                                              label="Var %"/></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {rows.map((row, i) => (
                                <TableRow key={`${row.name}-${row.fund_type}`}
                                          className={i % 2 === 0 ? "" : "bg-gray-50/50"}>
                                    <TableCell className="font-medium text-sm">{row.name}</TableCell>
                                    <TableCell>
                    <span
                        className={`text-xs px-1.5 py-0.5 rounded ${row.fund_type === "administrative" ? "bg-blue-100 text-blue-700" : "bg-purple-100 text-purple-700"}`}>
                      {row.fund_type === "administrative" ? "Admin" : "Sinking"}
                    </span>
                                    </TableCell>
                                    <TableCell className="text-right text-sm">{fmt(row.budgeted)}</TableCell>
                                    <TableCell className="text-right text-sm">{fmt(row.actual)}</TableCell>
                                    <TableCell className={`text-right text-sm ${getVarianceStyle(row.variance_pct)}`}>
                                        {row.variance >= 0 ? "+" : "-"}{fmt(row.variance)}
                                    </TableCell>
                                    <TableCell className={`text-right text-sm ${getVarianceStyle(row.variance_pct)}`}>
                                        {row.variance_pct >= 0 ? "+" : ""}{row.variance_pct.toFixed(1)}%
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </CardContent>
        </Card>
    );
};

export default VarianceTable;
