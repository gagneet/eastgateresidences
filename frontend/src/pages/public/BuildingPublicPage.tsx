"use client";

import React, {useEffect, useState} from "react";
import {Building2, DollarSign, Minus, ShieldAlert, TrendingDown, TrendingUp, Wrench} from "lucide-react";

interface PublicBuildingReport {
    building_name: string;
    building_address: string;
    slug: string;
    risk_score: number;
    risk_level: string;
    financial_health_summary: string;
    reserve_adequacy_level: string;
    reserve_adequacy_ratio: number | null;
    maintenance_forecast_summary: string;
    predicted_major_repairs: string[];
    capital_replacement_summary: string;
    capital_shock_index: number | null;
    predicted_levy_risk: string | null;
    computed_at: string;
}

const RISK_COLOURS: Record<string, { bg: string; text: string; border: string; badge: string }> = {
    low: {bg: "bg-green-50", text: "text-green-700", border: "border-green-200", badge: "bg-green-100 text-green-800"},
    moderate: {
        bg: "bg-yellow-50",
        text: "text-yellow-700",
        border: "border-yellow-200",
        badge: "bg-yellow-100 text-yellow-800"
    },
    high: {
        bg: "bg-orange-50",
        text: "text-orange-700",
        border: "border-orange-200",
        badge: "bg-orange-100 text-orange-800"
    },
    critical: {bg: "bg-red-50", text: "text-red-700", border: "border-red-200", badge: "bg-red-100 text-red-800"},
};

const RISK_LABELS: Record<string, string> = {
    low: "Low Risk",
    moderate: "Moderate Risk",
    high: "High Risk",
    critical: "Critical Risk",
};

const ADEQUACY_LABELS: Record<string, string> = {
    adequate: "Adequate",
    moderate: "Moderate",
    underfunded: "Underfunded",
};
/**
 * @generated FunctionHeader
 * Function: ScoreArc
 * Path: frontend/src/pages/public/BuildingPublicPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ScoreArc({score}: { score: number }) {
    const clamped = Math.max(0, Math.min(100, score));
    const colour =
        clamped < 25 ? "#16a34a" : clamped < 50 ? "#ca8a04" : clamped < 75 ? "#ea580c" : "#dc2626";

    // SVG arc parameters
    const radius = 80;
    const cx = 100;
    const cy = 100;
    const startAngle = 180;
    const sweepAngle = (clamped / 100) * 180;
    /**
     * @generated FunctionHeader
     * Function: polarToCartesian
     * Path: frontend/src/pages/public/BuildingPublicPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function polarToCartesian(angle: number) {
        const rad = (angle * Math.PI) / 180;
        return {
            x: cx + radius * Math.cos(rad),
            y: cy + radius * Math.sin(rad),
        };
    }

    const start = polarToCartesian(startAngle);
    const end = polarToCartesian(startAngle + sweepAngle);
    const largeArc = sweepAngle > 180 ? 1 : 0;
    const arcPath = `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;

    return (
        <svg viewBox="0 0 200 110" className="w-48 h-24">
            {/* Background track */}
            <path
                d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
                fill="none"
                stroke="#e5e7eb"
                strokeWidth="16"
                strokeLinecap="round"
            />
            {/* Score arc */}
            {sweepAngle > 0 && (
                <path
                    d={arcPath}
                    fill="none"
                    stroke={colour}
                    strokeWidth="16"
                    strokeLinecap="round"
                />
            )}
            {/* Score text */}
            <text x={cx} y={cy + 4} textAnchor="middle" fontSize="28" fontWeight="bold" fill="#111827">
                {Math.round(clamped)}
            </text>
            <text x={cx} y={cy + 20} textAnchor="middle" fontSize="11" fill="#6b7280">
                / 100
            </text>
        </svg>
    );
}

interface SectionCardProps {
    icon: React.ReactNode;
    title: string;
    children: React.ReactNode;
}
/**
 * @generated FunctionHeader
 * Function: SectionCard
 * Path: frontend/src/pages/public/BuildingPublicPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SectionCard({icon, title, children}: SectionCardProps) {
    return (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100 bg-gray-50">
                <span className="text-gray-500">{icon}</span>
                <h2 className="font-semibold text-gray-800">{title}</h2>
            </div>
            <div className="px-5 py-4">{children}</div>
        </div>
    );
}

interface Props {
    slug: string;
    apiBaseUrl?: string;
}
/**
 * @generated FunctionHeader
 * Function: BuildingPublicPage
 * Path: frontend/src/pages/public/BuildingPublicPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingPublicPage({slug, apiBaseUrl}: Props) {
    const base =
        apiBaseUrl ||
        process.env.NEXT_PUBLIC_BACKEND_URL ||
        "http://localhost:8003";

    const [report, setReport] = useState<PublicBuildingReport | null>(null);
    const [loading, setLoading] = useState(true);
    const [notFound, setNotFound] = useState(false);

    useEffect(() => {
        fetch(`${base}/api/intelligence/risk-index/public/${encodeURIComponent(slug)}`)
            .then((r) => {
                if (r.status === 404) {
                    setNotFound(true);
                    return null;
                }
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.json();
            })
            .then((data) => {
                if (data) setReport(data);
            })
            .catch(() => setNotFound(true))
            .finally(() => setLoading(false));
    }, [slug, base]);

    if (loading) {
        return (
            <div className="max-w-3xl mx-auto px-4 py-12 space-y-6 animate-pulse">
                <div className="h-8 w-64 bg-gray-200 rounded"/>
                <div className="h-4 w-48 bg-gray-200 rounded"/>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {[1, 2, 3, 4].map((i) => (
                        <div key={i} className="h-32 bg-gray-200 rounded-xl"/>
                    ))}
                </div>
            </div>
        );
    }

    if (notFound || !report) {
        return (
            <div className="max-w-3xl mx-auto px-4 py-20 text-center">
                <Building2 className="w-12 h-12 text-gray-300 mx-auto mb-4"/>
                <h1 className="text-2xl font-bold text-gray-800 mb-2">Building Not Found</h1>
                <p className="text-gray-500">
                    No building report is available for <code className="bg-gray-100 px-1 rounded">{slug}</code>.
                </p>
            </div>
        );
    }

    const colour = RISK_COLOURS[report.risk_level] ?? RISK_COLOURS.moderate;
    const riskLabel = RISK_LABELS[report.risk_level] ?? "Unknown Risk";

    return (
        <div className="max-w-3xl mx-auto px-4 py-10 space-y-8">
            {/* Hero */}
            <div className={`rounded-2xl border ${colour.border} ${colour.bg} p-6 shadow-sm`}>
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-900">{report.building_name}</h1>
                        <p className="text-gray-500 mt-1">{report.building_address}</p>
                    </div>
                    <div className="flex flex-col items-center gap-1">
                        <ScoreArc score={report.risk_score}/>
                        <span className={`text-sm font-semibold px-3 py-1 rounded-full ${colour.badge}`}>
              {riskLabel}
            </span>
                    </div>
                </div>

                {report.predicted_levy_risk && (
                    <div
                        className="mt-4 flex items-center gap-2 text-sm text-gray-700 bg-white/60 rounded-lg px-4 py-2">
                        {report.risk_score < 25 ? (
                            <TrendingDown className="w-4 h-4 text-green-600 shrink-0"/>
                        ) : report.risk_score < 75 ? (
                            <Minus className="w-4 h-4 text-yellow-500 shrink-0"/>
                        ) : (
                            <TrendingUp className="w-4 h-4 text-orange-500 shrink-0"/>
                        )}
                        <span>{report.predicted_levy_risk}</span>
                    </div>
                )}
            </div>

            {/* Financial Health */}
            <SectionCard
                icon={<DollarSign className="w-5 h-5"/>}
                title="Financial Health"
            >
                <p className="text-gray-700 text-sm">{report.financial_health_summary}</p>
                <div className="mt-3 flex items-center gap-2">
                    <span className="text-xs text-gray-500">Reserve status:</span>
                    <span
                        className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                            report.reserve_adequacy_level === "adequate"
                                ? "bg-green-100 text-green-700"
                                : report.reserve_adequacy_level === "moderate"
                                    ? "bg-yellow-100 text-yellow-700"
                                    : "bg-red-100 text-red-700"
                        }`}
                    >
            {ADEQUACY_LABELS[report.reserve_adequacy_level] ?? report.reserve_adequacy_level}
          </span>
                </div>
            </SectionCard>

            {/* Maintenance Forecast */}
            <SectionCard
                icon={<Wrench className="w-5 h-5"/>}
                title="Maintenance Forecast"
            >
                <p className="text-gray-700 text-sm">{report.maintenance_forecast_summary}</p>
            </SectionCard>

            {/* Capital Replacement */}
            <SectionCard
                icon={<ShieldAlert className="w-5 h-5"/>}
                title="Capital Replacement"
            >
                <p className="text-gray-700 text-sm">{report.capital_replacement_summary}</p>

                {report.predicted_major_repairs && report.predicted_major_repairs.length > 0 && (
                    <ul className="mt-3 space-y-1">
                        {report.predicted_major_repairs.map((repair, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                                <span className="mt-2 w-1.5 h-1.5 rounded-full bg-orange-400 shrink-0"/>
                                {repair}
                            </li>
                        ))}
                    </ul>
                )}

                {report.capital_shock_index !== null && report.capital_shock_index !== undefined && (
                    <div className="mt-3 text-xs text-gray-500">
                        Capital Shock Index:{" "}
                        <span className="font-medium text-gray-700">
              {report.capital_shock_index.toFixed(2)}
            </span>
                    </div>
                )}
            </SectionCard>

            {/* Disclaimer */}
            <p className="text-xs text-gray-400 text-center pb-4">
                This report is generated from aggregated building data and does not
                contain personal, financial transaction, or owner information.{" "}
                Last updated {new Date(report.computed_at).toLocaleDateString()}.
            </p>
        </div>
    );
}
