"use client";

import React from "react";
import {motion} from "framer-motion";
import {Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {CHART_SERIES, axisProps, gridProps, tooltipProps} from "@/lib/chartTheme";
import {Target, TrendingUp} from "lucide-react";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

interface ProjectionData {
    year: string;
    admin: Record<string, any>;
    sinking: Record<string, any>;
    combined: Record<string, any>;
    base_admin_rate: number;
    base_sinking_rate: number;
}

interface LevyProjectionPremiumProps {
    data: ProjectionData;
    year: string;
}
/**
 * @generated FunctionHeader
 * Function: LevyProjectionPremium
 * Path: frontend/src/components/finance/premium/LevyProjectionPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LevyProjectionPremium = ({data, year}: LevyProjectionPremiumProps) => {
    const years = Object.keys(data.combined).sort();
    const chartData = years.map((yr) => ({
        name: `FY ${yr}`,
        "Required Collection": data.combined[yr].total_collection,
        "Admin Rate": data.admin[yr].required_rate_per_uoe,
        "Sinking Rate": data.sinking[yr].required_rate_per_uoe,
        "Combined Rate": data.combined[yr].combined_rate_per_uoe,
    }));

    const latestYear = years[years.length - 1];
    const totalProjected = data.combined[latestYear]?.total_collection || 0;
    const changePct = data.combined[latestYear]?.rate_change_pct || 0;

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex justify-between items-start mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Levy Projection</h3>
                        <Target className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Levy Projection"
                            description="Future-looking estimates of required levy rates per unit of entitlement (UOE) based on projected building expenditures."
                            dataSources={["financial_forecasts", "annual_levies", "site_settings (UOE)"]}
                            logic="The required collection is derived from the Strategic Forecast total for each year. This total is then divided by the building's total Units of Entitlement (UOE) to calculate the minimum sustainable levy rate. 'Admin' and 'Sinking' rates are projected independently based on their respective forecast tracks."
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">3-Year required levy rate modeling</p>
                </div>

                <div className={`px-4 py-2 rounded-2xl bg-primary/10 border border-primary/20 flex items-center gap-2`}>
                    <TrendingUp className="w-4 h-4 text-primary"/>
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-primary">
             Target FY {latestYear}
           </span>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
                <div className="p-4 rounded-2xl bg-muted border border-border">
                    <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Current Combined
                        Rate</p>
                    <p className="text-lg font-semibold text-foreground">{(data.base_admin_rate + data.base_sinking_rate).toFixed(4)}
                        <span className="text-[10px] text-muted-foreground">/ UOE</span></p>
                </div>
                <div className="p-4 rounded-2xl bg-primary/10 border border-primary/20">
                    <p className="text-[9px] font-semibold uppercase tracking-widest text-primary mb-1">Projected {latestYear} Rate</p>
                    <p className="text-lg font-semibold text-primary">{data.combined[latestYear]?.combined_rate_per_uoe.toFixed(4)}
                        <span className="text-[10px] text-primary">/ UOE</span></p>
                </div>
                <div className="p-4 rounded-2xl bg-emerald-50/50 border border-emerald-100">
                    <p className="text-[9px] font-semibold uppercase tracking-widest text-emerald-500 mb-1">Projected
                        Collection</p>
                    <p className="text-lg font-semibold text-emerald-700">{formatCurrency(totalProjected)}</p>
                </div>
            </div>

            <div className="flex-1 min-h-[250px]">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={chartData} margin={{top: 4, right: 8, bottom: 0, left: 0}}>
                        <defs>
                            <linearGradient id="nameFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={CHART_SERIES[0]} stopOpacity={0.25}/>
                                <stop offset="100%" stopColor={CHART_SERIES[0]} stopOpacity={0}/>
                            </linearGradient>
                        </defs>
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="name"/>
                        <YAxis {...axisProps} tickFormatter={(v) => formatCurrency(Number(v))}/>
                        <Tooltip {...tooltipProps} formatter={(v: any) => formatCurrency(Number(v))}/>
                        <Area
                            type="monotone"
                            dataKey="Required Collection"
                            stroke={CHART_SERIES[0]}
                            strokeWidth={2}
                            fill="url(#nameFill)"
                            dot={false}
                            isAnimationActive
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </div>

            <div className="mt-8 pt-6 border-t border-border">
                <div className="flex flex-wrap gap-4">
                    {years.map(yr => (
                        <div key={yr} className="flex flex-col">
                            <span
                                className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">FY {yr} Admin/Sinking</span>
                            <span className="text-xs font-bold text-foreground">
                {data.admin[yr].required_rate_per_uoe.toFixed(4)} / {data.sinking[yr].required_rate_per_uoe.toFixed(4)}
              </span>
                        </div>
                    ))}
                </div>
            </div>
        </motion.div>
    );
};

export default LevyProjectionPremium;
