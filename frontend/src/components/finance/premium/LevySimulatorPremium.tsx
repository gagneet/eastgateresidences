"use client";

import React, {useCallback, useRef, useState} from "react";
import {AnimatePresence, motion} from "framer-motion";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import * as SliderPrimitive from "@radix-ui/react-slider";
import {Calculator, Download, TrendingUp} from "lucide-react";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

interface UnitImpact {
    unit_number: string;
    current_annual: number;
    new_annual: number;
    impact: number;
    entitlement: number;
}

interface SimulationResult {
    year: string;
    increase_pct: number;
    current_total_collection: number;
    new_total_collection: number;
    surplus_deficit: number;
    per_unit_samples: UnitImpact[];
    all_units: UnitImpact[];
}

interface LevySimulatorPremiumProps {
    year: string;
    onSimulate: (increasePct: number) => Promise<SimulationResult>;
}
/**
 * @generated FunctionHeader
 * Function: LevySimulatorPremium
 * Path: frontend/src/components/finance/premium/LevySimulatorPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LevySimulatorPremium = ({year, onSimulate}: LevySimulatorPremiumProps) => {
    const [sliderValue, setSliderValue] = useState(0);
    const [result, setResult] = useState<SimulationResult | null>(null);
    const [loading, setLoading] = useState(false);
    const debounceRef = useRef<NodeJS.Timeout | null>(null);

    const runSimulation = useCallback(async (pct: number) => {
        setLoading(true);
        try {
            const res = await onSimulate(pct);
            setResult(res);
        } catch {
            // silent
        } finally {
            setLoading(false);
        }
    }, [onSimulate]);
    /**
     * @generated FunctionHeader
     * Function: handleSliderChange
     * Path: frontend/src/components/finance/premium/LevySimulatorPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSliderChange = (vals: number[]) => {
        const pct = vals[0];
        setSliderValue(pct);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => runSimulation(pct), 300);
    };
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/components/finance/premium/LevySimulatorPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = () => {
        if (!result) return;
        const rows = [
            ["Unit", "Entitlement", "Current Annual", "New Annual", "Impact"],
            ...result.all_units.map((u) => [
                u.unit_number, u.entitlement, u.current_annual, u.new_annual, u.impact,
            ]),
        ];
        const csv = rows.map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `levy_simulation_${year}_+${sliderValue}pct.csv`;
        a.click();
    };

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex justify-between items-start mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Levy Simulator</h3>
                        <Calculator className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Levy Simulator"
                            description="An interactive tool for modeling the impact of potential levy adjustments on individual units and the building's aggregate budget."
                            dataSources={["units (UOE)", "annual_levies", "levy_categories"]}
                            logic="The simulator applies the selected percentage change to the building's current combined levy rate. It then re-calculates the projected annual collection and compares it against current budgeted expenses to determine the resulting surplus or deficit. Per-unit impacts are derived directly from individual Unit Entitlements (UOE)."
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">Model levy adjustments for {year}</p>
                </div>

                <button
                    onClick={handleExport}
                    disabled={!result}
                    className="p-2.5 rounded-xl bg-card border border-border text-muted-foreground hover:text-primary hover:border-primary/20 transition-all shadow-sm disabled:opacity-50"
                >
                    <Download className="w-4 h-4"/>
                </button>
            </div>

            <div className="mb-8">
                <div className="flex justify-between items-end mb-4">
                    <div>
                        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Adjustment Rate</span>
                        <div className="text-2xl font-semibold text-primary">{sliderValue}%</div>
                    </div>
                    {loading && <div
                        className="text-[10px] font-bold text-muted-foreground uppercase animate-pulse">Calculating...</div>}
                </div>

                <SliderPrimitive.Root
                    className="relative flex items-center w-full h-5 cursor-pointer select-none touch-none"
                    min={0}
                    max={50}
                    step={0.5}
                    value={[sliderValue]}
                    onValueChange={handleSliderChange}
                >
                    <SliderPrimitive.Track className="relative h-1.5 grow rounded-full bg-muted overflow-hidden">
                        <SliderPrimitive.Range className="absolute h-full bg-primary"/>
                    </SliderPrimitive.Track>
                    <SliderPrimitive.Thumb
                        className="block w-4 h-4 bg-card border-2 border-primary/20 rounded-full shadow-lg focus:outline-none transition-transform hover:scale-110"/>
                </SliderPrimitive.Root>

                <div className="flex justify-between mt-2 text-[9px] font-semibold text-muted-foreground uppercase">
                    <span>0%</span>
                    <span>25%</span>
                    <span>50%</span>
                </div>
            </div>

            <AnimatePresence mode="wait">
                {result ? (
                    <motion.div
                        key="result"
                        initial={{opacity: 0, y: 10}}
                        animate={{opacity: 1, y: 0}}
                        exit={{opacity: 0, y: -10}}
                        className="space-y-6 flex-1"
                    >
                        <div className="grid grid-cols-2 gap-3">
                            <div className="p-4 rounded-2xl bg-muted border border-border">
                                <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">New
                                    Collection</p>
                                <p className="text-sm font-semibold text-foreground">{formatCurrency(result.new_total_collection)}</p>
                            </div>
                            <div
                                className={`p-4 rounded-2xl border ${result.surplus_deficit >= 0 ? 'bg-emerald-50 border-emerald-100' : 'bg-rose-50 border-rose-100'}`}>
                                <p className={`text-[9px] font-semibold uppercase tracking-widest mb-1 ${result.surplus_deficit >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                                    Surplus / Deficit
                                </p>
                                <p className={`text-sm font-semibold ${result.surplus_deficit >= 0 ? 'text-emerald-700' : 'text-rose-700'}`}>
                                    {formatCurrency(result.surplus_deficit)}
                                </p>
                            </div>
                        </div>

                        <div className="rounded-xl border border-border overflow-hidden bg-card/50">
                            <Table>
                                <TableHeader className="bg-muted/50">
                                    <TableRow>
                                        <TableHead
                                            className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground py-2">Unit</TableHead>
                                        <TableHead
                                            className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground py-2 text-right">Current</TableHead>
                                        <TableHead
                                            className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground py-2 text-right">New</TableHead>
                                        <TableHead
                                            className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground py-2 text-right">Impact</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {result.per_unit_samples.slice(0, 5).map((u) => (
                                        <TableRow key={u.unit_number}>
                                            <TableCell
                                                className="text-[10px] font-bold text-foreground py-2">{u.unit_number}</TableCell>
                                            <TableCell
                                                className="text-[10px] text-right text-muted-foreground py-2">{formatCurrency(u.current_annual)}</TableCell>
                                            <TableCell
                                                className="text-[10px] text-right font-semibold text-foreground py-2">{formatCurrency(u.new_annual)}</TableCell>
                                            <TableCell
                                                className="text-[10px] text-right font-semibold text-primary py-2">+{formatCurrency(u.impact)}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    </motion.div>
                ) : (
                    <div className="flex-1 flex flex-col items-center justify-center text-center py-12">
                        <div
                            className="w-16 h-16 rounded-xl bg-primary/10 flex items-center justify-center text-primary mb-4 border border-primary/20">
                            <TrendingUp size={32}/>
                        </div>
                        <p className="text-muted-foreground text-xs font-medium max-w-[200px]">Adjust the slider to simulate
                            building-wide levy adjustments</p>
                    </div>
                )}
            </AnimatePresence>
        </motion.div>
    );
};

export default LevySimulatorPremium;
