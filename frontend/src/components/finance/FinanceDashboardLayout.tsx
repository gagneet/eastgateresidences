"use client";
import React from "react";

// ─────────────────────────────────────────────────────────────────────────────
// KPIStat — single metric tile used across finance dashboards
// ─────────────────────────────────────────────────────────────────────────────

interface KPIStatProps {
    label: string;
    value: string | number;
    sub?: string;
    trend?: "up" | "down" | "neutral";
    trendValue?: string;
    color?: "emerald" | "red" | "amber" | "blue" | "gray";
    loading?: boolean;
}

const COLOR_MAP = {
    emerald: {bg: "bg-emerald-50", text: "text-emerald-700", badge: "text-emerald-600 bg-emerald-100"},
    red: {bg: "bg-red-50", text: "text-red-700", badge: "text-red-600 bg-red-100"},
    amber: {bg: "bg-amber-50", text: "text-amber-700", badge: "text-amber-600 bg-amber-100"},
    blue: {bg: "bg-blue-50", text: "text-blue-700", badge: "text-blue-600 bg-blue-100"},
    gray: {bg: "bg-gray-50", text: "text-gray-700", badge: "text-gray-600 bg-gray-100"},
};

const TREND_ICONS = {
    up: "↑",
    down: "↓",
    neutral: "→",
};

const TREND_COLORS = {
    up: "text-emerald-600",
    down: "text-red-600",
    neutral: "text-gray-500",
};
/**
 * @generated FunctionHeader
 * Function: KPIStat
 * Path: frontend/src/components/finance/FinanceDashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const KPIStat: React.FC<KPIStatProps> = ({
                                                    label,
                                                    value,
                                                    sub,
                                                    trend,
                                                    trendValue,
                                                    color = "gray",
                                                    loading = false,
                                                }) => {
    const cfg = COLOR_MAP[color];

    if (loading) {
        return (
            <div className={`rounded-xl p-4 ${cfg.bg} animate-pulse`}>
                <div className="h-3 w-20 bg-gray-200 rounded mb-2"/>
                <div className="h-7 w-28 bg-gray-300 rounded"/>
            </div>
        );
    }

    return (
        <div className={`rounded-xl p-4 ${cfg.bg}`}>
            <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{label}</div>
            <div className={`text-2xl font-black ${cfg.text} leading-tight`}>
                {typeof value === "number" && value > 1000
                    ? value.toLocaleString("en-AU")
                    : value}
            </div>
            {(sub || (trend && trendValue)) && (
                <div className="flex items-center gap-2 mt-1">
                    {sub && <span className="text-xs text-gray-500">{sub}</span>}
                    {trend && trendValue && (
                        <span className={`text-xs font-semibold ${TREND_COLORS[trend]}`}>
              {TREND_ICONS[trend]} {trendValue}
            </span>
                    )}
                </div>
            )}
        </div>
    );
};

// ─────────────────────────────────────────────────────────────────────────────
// ResponsiveChartContainer — enforces consistent chart height across breakpoints
// ─────────────────────────────────────────────────────────────────────────────

interface ResponsiveChartContainerProps {
    children: React.ReactNode;
    height?: number;
    className?: string;
}
/**
 * @generated FunctionHeader
 * Function: ResponsiveChartContainer
 * Path: frontend/src/components/finance/FinanceDashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ResponsiveChartContainer: React.FC<ResponsiveChartContainerProps> = ({
                                                                                      children,
                                                                                      height = 280,
                                                                                      className = "",
                                                                                  }) => (
    <div
        className={`w-full ${className}`}
        style={{height: `${height}px`}}
    >
        {children}
    </div>
);

// ─────────────────────────────────────────────────────────────────────────────
// FinanceDashboardLayout — shared wrapper for all finance dashboard views
// ─────────────────────────────────────────────────────────────────────────────

interface FinanceDashboardLayoutProps {
    title: string;
    subtitle?: string;
    icon?: React.ReactNode;
    headerActions?: React.ReactNode;
    kpis?: React.ReactNode;
    children: React.ReactNode;
}
/**
 * @generated FunctionHeader
 * Function: FinanceDashboardLayout
 * Path: frontend/src/components/finance/FinanceDashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const FinanceDashboardLayout: React.FC<FinanceDashboardLayoutProps> = ({
                                                                                  title,
                                                                                  subtitle,
                                                                                  icon,
                                                                                  headerActions,
                                                                                  kpis,
                                                                                  children,
                                                                              }) => (
    <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
            <div className="flex items-start gap-3">
                {icon && (
                    <div className="mt-0.5 p-2 bg-emerald-50 rounded-lg text-emerald-600">
                        {icon}
                    </div>
                )}
                <div>
                    <h1 className="text-2xl font-black text-gray-900">{title}</h1>
                    {subtitle && <p className="text-sm text-gray-500 mt-0.5">{subtitle}</p>}
                </div>
            </div>
            {headerActions && (
                <div className="flex items-center gap-2 flex-wrap">{headerActions}</div>
            )}
        </div>

        {/* KPI row */}
        {kpis && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
                {kpis}
            </div>
        )}

        {/* Main content */}
        <div className="space-y-6">{children}</div>
    </div>
);

export default FinanceDashboardLayout;

// ─────────────────────────────────────────────────────────────────────────────
// DashboardSection — titled grouping with optional description
// ─────────────────────────────────────────────────────────────────────────────

interface DashboardSectionProps {
    title: string;
    description?: string;
    children: React.ReactNode;
    className?: string;
    "data-testid"?: string;
}
/**
 * @generated FunctionHeader
 * Function: DashboardSection
 * Path: frontend/src/components/finance/FinanceDashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const DashboardSection: React.FC<DashboardSectionProps> = ({
                                                                      title,
                                                                      description,
                                                                      children,
                                                                      className = "",
                                                                      "data-testid": testId,
                                                                  }) => (
    <section className={`space-y-3 ${className}`} data-testid={testId}>
        <div className="border-b border-gray-100 pb-2">
            <h2 className="text-base font-bold text-gray-800">{title}</h2>
            {description && <p className="text-xs text-gray-500 mt-0.5">{description}</p>}
        </div>
        {children}
    </section>
);

// ─────────────────────────────────────────────────────────────────────────────
// DashboardCard — consistent card wrapper (no inline styles)
// ─────────────────────────────────────────────────────────────────────────────

interface DashboardCardProps {
    children: React.ReactNode;
    className?: string;
    onClick?: () => void;
    "data-testid"?: string;
}
/**
 * @generated FunctionHeader
 * Function: DashboardCard
 * Path: frontend/src/components/finance/FinanceDashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const DashboardCard: React.FC<DashboardCardProps> = ({
                                                                children,
                                                                className = "",
                                                                onClick,
                                                                "data-testid": testId,
                                                            }) => (
    <div
        className={`bg-white border border-gray-200 rounded-xl shadow-sm p-4 ${
            onClick ? "cursor-pointer hover:shadow-md transition-shadow" : ""
        } ${className}`}
        onClick={onClick}
        data-testid={testId}
    >
        {children}
    </div>
);

// ─────────────────────────────────────────────────────────────────────────────
// KPIGrid — responsive grid for KPI tiles
// ─────────────────────────────────────────────────────────────────────────────

interface KPIGridProps {
    children: React.ReactNode;
    cols?: 2 | 3 | 4 | 6;
    "data-testid"?: string;
}

const COL_CLASSES: Record<number, string> = {
    2: "grid-cols-1 sm:grid-cols-2",
    3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
    4: "grid-cols-2 sm:grid-cols-2 lg:grid-cols-4",
    6: "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6",
};
/**
 * @generated FunctionHeader
 * Function: KPIGrid
 * Path: frontend/src/components/finance/FinanceDashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const KPIGrid: React.FC<KPIGridProps> = ({
                                                    children,
                                                    cols = 4,
                                                    "data-testid": testId,
                                                }) => (
    <div
        className={`grid ${COL_CLASSES[cols] ?? COL_CLASSES[4]} gap-3`}
        data-testid={testId}
    >
        {children}
    </div>
);
