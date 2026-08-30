"use client";

import React, {useEffect, useState} from "react";
import {Minus, ShieldAlert, TrendingDown, TrendingUp} from "lucide-react";

interface RiskComponents {
    asset_health_score: number;
    financial_stability_score: number;
    reserve_adequacy_score: number;
    capital_shock_risk_score: number;
    maintenance_risk_score: number;
}

interface BuildingRiskData {
    building_name: string;
    risk_score: number;
    risk_level: string;
    components: RiskComponents;
    predicted_major_repairs: string[];
    predicted_levy_risk: string | null;
    capital_shock_index: number | null;
    computed_at: string;
}

const RISK_COLOURS: Record<string, { bg: string; text: string; border: string; label: string }> = {
    low: {bg: "bg-green-50", text: "text-green-700", border: "border-green-200", label: "Low Risk"},
    moderate: {bg: "bg-yellow-50", text: "text-yellow-700", border: "border-yellow-200", label: "Moderate Risk"},
    high: {bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200", label: "High Risk"},
    critical: {bg: "bg-red-50", text: "text-red-700", border: "border-red-200", label: "Critical Risk"},
};
/**
 * @generated FunctionHeader
 * Function: RiskGauge
 * Path: frontend/src/components/widgets/BuildingRiskWidget.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function RiskGauge({score}: { score: number }) {
    const clampedScore = Math.max(0, Math.min(100, score));
    const rotation = (clampedScore / 100) * 180 - 90; // -90° (left) to +90° (right)

    return (
        <div className="flex flex-col items-center gap-1">
            <div className="relative w-28 h-16 overflow-hidden">
                {/* Background arc */}
                <div className="absolute inset-0 flex items-end justify-center">
                    <div
                        className="w-28 h-28 rounded-full border-8 border-gray-200 border-b-transparent border-r-transparent rotate-[-135deg]"/>
                </div>
                {/* Coloured arc segments */}
                <div className="absolute inset-0 flex items-end justify-center">
                    <div
                        className="w-28 h-28 rounded-full border-8 border-transparent border-b-transparent"
                        style={{
                            borderTopColor:
                                clampedScore < 25
                                    ? "#16a34a"
                                    : clampedScore < 50
                                        ? "#ca8a04"
                                        : clampedScore < 75
                                            ? "#ea580c"
                                            : "#dc2626",
                            transform: `rotate(${rotation - 45}deg)`,
                            transition: "transform 0.6s ease-out",
                        }}
                    />
                </div>
                <div className="absolute bottom-0 left-1/2 -translate-x-1/2 text-2xl font-bold text-gray-800">
                    {Math.round(clampedScore)}
                </div>
            </div>
            <span className="text-xs text-gray-500">/ 100</span>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: ComponentBar
 * Path: frontend/src/components/widgets/BuildingRiskWidget.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ComponentBar({
                          label,
                          score,
                          weight,
                      }: {
    label: string;
    score: number;
    weight: number;
}) {
    const colour =
        score < 25 ? "bg-green-500" : score < 50 ? "bg-yellow-400" : score < 75 ? "bg-orange-500" : "bg-red-600";

    return (
        <div className="space-y-1">
            <div className="flex justify-between text-xs text-gray-600">
        <span>
          {label}{" "}
            <span className="text-gray-400">({(weight * 100).toFixed(0)}%)</span>
        </span>
                <span className="font-medium">{Math.round(score)}</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-gray-100">
                <div
                    className={`h-1.5 rounded-full ${colour} transition-all duration-500`}
                    style={{width: `${score}%`}}
                />
            </div>
        </div>
    );
}

interface Props {
    /** Axios instance or token-bearing fetch wrapper from AuthContext */
    axiosInstance?: { get: (url: string) => Promise<{ data: BuildingRiskData }> };
    /** Fallback: raw bearer token string */
    token?: string;
    /** Base URL of the backend API */
    apiBaseUrl?: string;
}
/**
 * @generated FunctionHeader
 * Function: BuildingRiskWidget
 * Path: frontend/src/components/widgets/BuildingRiskWidget.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingRiskWidget({
                                               axiosInstance,
                                               token,
                                               apiBaseUrl = "/api",
                                           }: Props) {
    const [data, setData] = useState<BuildingRiskData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchData
         * Path: frontend/src/components/widgets/BuildingRiskWidget.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchData = async () => {
            try {
                let result: BuildingRiskData;
                if (axiosInstance) {
                    const resp = await axiosInstance.get(
                        `${apiBaseUrl}/intelligence/risk-index`
                    );
                    result = resp.data;
                } else {
                    const resp = await fetch(`${apiBaseUrl}/intelligence/risk-index`, {
                        headers: token ? {Authorization: `Bearer ${token}`} : {},
                    });
                    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                    result = await resp.json();
                }
                setData(result);
            } catch (err) {
                setError("Risk score unavailable");
            } finally {
                setLoading(false);
            }
        };

        fetchData();
    }, [axiosInstance, token, apiBaseUrl]);

    if (loading) {
        return (
            <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm animate-pulse">
                <div className="h-4 w-40 bg-gray-200 rounded mb-3"/>
                <div className="h-16 w-28 bg-gray-200 rounded mx-auto mb-3"/>
                <div className="space-y-2">
                    {[1, 2, 3].map((i) => (
                        <div key={i} className="h-3 bg-gray-200 rounded"/>
                    ))}
                </div>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                <div className="flex items-center gap-2 text-gray-500">
                    <ShieldAlert className="w-4 h-4"/>
                    <span className="text-sm">{error ?? "Risk data unavailable"}</span>
                </div>
            </div>
        );
    }

    const colour = RISK_COLOURS[data.risk_level] ?? RISK_COLOURS.moderate;
    const levyRisk = data.predicted_levy_risk;

    return (
        <div
            className={`rounded-xl border ${colour.border} ${colour.bg} p-4 shadow-sm space-y-4`}
        >
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <ShieldAlert className={`w-5 h-5 ${colour.text}`}/>
                    <h3 className="font-semibold text-gray-800 text-sm">
                        Building Risk Index
                    </h3>
                </div>
                <span
                    className={`text-xs font-medium px-2 py-0.5 rounded-full ${colour.bg} ${colour.text} border ${colour.border}`}
                >
          {colour.label}
        </span>
            </div>

            {/* Gauge */}
            <div className="flex justify-center">
                <RiskGauge score={data.risk_score}/>
            </div>

            {/* Component breakdown */}
            <div className="space-y-2">
                <ComponentBar label="Asset Health" score={data.components.asset_health_score} weight={0.25}/>
                <ComponentBar label="Financial Stability" score={data.components.financial_stability_score}
                              weight={0.20}/>
                <ComponentBar label="Reserve Adequacy" score={data.components.reserve_adequacy_score} weight={0.20}/>
                <ComponentBar label="Capital Shock" score={data.components.capital_shock_risk_score} weight={0.20}/>
                <ComponentBar label="Maintenance Risk" score={data.components.maintenance_risk_score} weight={0.15}/>
            </div>

            {/* Levy risk */}
            {levyRisk && (
                <div className="flex items-start gap-2 rounded-lg bg-white/60 px-3 py-2 text-xs text-gray-700">
                    {data.risk_score < 25 ? (
                        <TrendingDown className="w-3.5 h-3.5 mt-0.5 text-green-600 shrink-0"/>
                    ) : data.risk_score < 75 ? (
                        <Minus className="w-3.5 h-3.5 mt-0.5 text-yellow-600 shrink-0"/>
                    ) : (
                        <TrendingUp className="w-3.5 h-3.5 mt-0.5 text-red-600 shrink-0"/>
                    )}
                    <span>{levyRisk}</span>
                </div>
            )}

            {/* Predicted major repairs */}
            {data.predicted_major_repairs && data.predicted_major_repairs.length > 0 && (
                <div className="rounded-lg bg-white/60 px-3 py-2">
                    <p className="text-xs font-medium text-gray-700 mb-1">
                        Major repairs within 5 years
                    </p>
                    <ul className="space-y-0.5">
                        {data.predicted_major_repairs.slice(0, 3).map((repair, i) => (
                            <li key={i} className="text-xs text-gray-600 flex items-start gap-1">
                                <span className="mt-1 w-1 h-1 rounded-full bg-orange-400 shrink-0"/>
                                {repair}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {/* Footer */}
            <p className="text-[10px] text-gray-400 text-right">
                Updated {new Date(data.computed_at).toLocaleDateString()}
            </p>
        </div>
    );
}
