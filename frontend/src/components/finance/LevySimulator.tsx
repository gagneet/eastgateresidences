"use client";
import React, {useCallback, useRef, useState} from "react";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import * as SliderPrimitive from "@radix-ui/react-slider";
import {Calculator, Download} from "lucide-react";

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

interface LevySimulatorProps {
    year: string;
    onSimulate: (increasePct: number) => Promise<SimulationResult>;
}
/**
 * @generated FunctionHeader
 * Function: LevySimulator
 * Path: frontend/src/components/finance/LevySimulator.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LevySimulator: React.FC<LevySimulatorProps> = ({year, onSimulate}) => {
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
     * Path: frontend/src/components/finance/LevySimulator.tsx
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
     * Path: frontend/src/components/finance/LevySimulator.tsx
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
    /**
     * @generated FunctionHeader
     * Function: fmt
     * Path: frontend/src/components/finance/LevySimulator.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fmt = (v: number) =>
        `$${v.toLocaleString("en-AU", {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Calculator className="w-5 h-5 text-emerald-600"/>
                    Levy Impact Simulator
                </CardTitle>
                <CardDescription>Model the impact of levy adjustments across all {year} units</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
                {/* Slider */}
                <div>
                    <div className="flex justify-between text-sm mb-2">
                        <span className="text-gray-600">Levy increase</span>
                        <span className="font-bold text-lg text-emerald-700">{sliderValue}%</span>
                    </div>
                    <SliderPrimitive.Root
                        className="relative flex items-center w-full h-5 cursor-pointer"
                        min={0}
                        max={30}
                        step={0.5}
                        value={[sliderValue]}
                        onValueChange={handleSliderChange}
                    >
                        <SliderPrimitive.Track className="relative flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
                            <SliderPrimitive.Range className="absolute h-full bg-emerald-500 rounded-full"/>
                        </SliderPrimitive.Track>
                        <SliderPrimitive.Thumb
                            className="block w-5 h-5 bg-white border-2 border-emerald-500 rounded-full shadow focus:outline-none"/>
                    </SliderPrimitive.Root>
                    <div className="flex justify-between text-xs text-gray-400 mt-1">
                        <span>0%</span><span>5%</span><span>10%</span><span>15%</span><span>20%</span><span>25%</span><span>30%</span>
                    </div>
                </div>

                {loading && (
                    <div className="text-center text-sm text-gray-500 py-2">Calculating…</div>
                )}

                {result && !loading && (
                    <>
                        {/* Summary metrics */}
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {[
                                {
                                    label: "Current Collection",
                                    value: result.current_total_collection,
                                    color: "text-gray-700"
                                },
                                {
                                    label: "New Collection",
                                    value: result.new_total_collection,
                                    color: "text-emerald-700"
                                },
                                {
                                    label: "Additional Revenue",
                                    value: result.new_total_collection - result.current_total_collection,
                                    color: "text-blue-700"
                                },
                                {
                                    label: "Surplus / Deficit",
                                    value: result.surplus_deficit,
                                    color: result.surplus_deficit >= 0 ? "text-emerald-700" : "text-red-600",
                                },
                            ].map((m) => (
                                <div key={m.label} className="bg-gray-50 rounded-lg p-3 text-center">
                                    <div className="text-xs text-gray-500 mb-1">{m.label}</div>
                                    <div className={`font-bold text-sm ${m.color}`}>{fmt(m.value)}</div>
                                </div>
                            ))}
                        </div>

                        {/* Sample units table */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <div className="text-sm font-semibold text-gray-700">Sample Units (Top 10 by
                                    Entitlement)
                                </div>
                                <Button size="sm" variant="outline" onClick={handleExport}>
                                    <Download className="w-4 h-4 mr-1"/> Export All
                                </Button>
                            </div>
                            <div className="overflow-auto rounded border">
                                <table className="w-full text-xs">
                                    <thead className="bg-gray-50 border-b">
                                    <tr>
                                        {["Unit", "Entitlement", "Current", "New Annual", "Impact"].map((h) => (
                                            <th key={h}
                                                className="px-3 py-2 text-left font-semibold text-gray-600">{h}</th>
                                        ))}
                                    </tr>
                                    </thead>
                                    <tbody>
                                    {result.per_unit_samples.map((u, i) => (
                                        <tr key={u.unit_number} className={i % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                                            <td className="px-3 py-1.5 font-mono">{u.unit_number}</td>
                                            <td className="px-3 py-1.5">{u.entitlement}</td>
                                            <td className="px-3 py-1.5">{fmt(u.current_annual)}</td>
                                            <td className="px-3 py-1.5 text-emerald-700">{fmt(u.new_annual)}</td>
                                            <td className="px-3 py-1.5 text-blue-700 font-semibold">+{fmt(u.impact)}</td>
                                        </tr>
                                    ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </>
                )}

                {!result && !loading && (
                    <div className="text-center text-gray-400 text-sm py-4">
                        Adjust the slider to see the impact of a levy change
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default LevySimulator;
