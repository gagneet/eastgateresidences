"use client"

import * as React from "react"
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "../ui/select"
import {useAuth} from "../../contexts/AuthContext"

interface YearSelectorProps {
    /** Controlled selected year. When omitted, falls back to AuthContext.selectedYear. */
    value?: string | null
    /** Controlled change handler. When omitted, falls back to AuthContext.setSelectedYear. */
    onValueChange?: (year: string) => void
    /** Explicit year list. When omitted, falls back to AuthContext.availableYears. */
    years?: Array<string | number>
    /** Hide the "Levy Year:" label (e.g. when the caller already has its own heading). */
    showLabel?: boolean
    /** Override the trigger width/styling to fit a compact header. */
    triggerClassName?: string
}
/**
 * @generated FunctionHeader
 * Function: YearSelector
 * Path: frontend/src/components/widgets/YearSelector.tsx
 *
 * @remarks Shared levy-year selector. Uncontrolled by default (reads/writes AuthContext);
 *          pass value/onValueChange/years to drive it from a page's own local state while
 *          keeping the canonical shadcn styling and formatYear label (e.g. UnitFinanceDetailPage).
 */
const YearSelector: React.FC<YearSelectorProps> = ({
    value,
    onValueChange,
    years,
    showLabel = true,
    triggerClassName,
}) => {
    const ctx = useAuth() as any
    const {financialYearStartMonth} = ctx
    const isControlled = value !== undefined || onValueChange !== undefined || years !== undefined
    const selectedYear = isControlled ? (value ?? null) : ctx.selectedYear
    const setSelectedYear = isControlled ? (onValueChange ?? (() => {})) : ctx.setSelectedYear
    const availableYears: Array<string | number> = (
        years ?? ctx.availableYears ?? []
    )
    /**
     * @generated FunctionHeader
     * Function: formatYear
     * Path: frontend/src/components/widgets/YearSelector.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const formatYear = (year: string | number | null) => {
        if (!year) return ""
        const yearStr = String(year)
        // Default to calendar-year formatting when the building's FY start month
        // hasn't resolved yet (null/undefined) — only an explicit non-January start
        // month uses the "YYYY-YY" span form.
        if (financialYearStartMonth == null || financialYearStartMonth === 1) {
            return yearStr
        }
        const nextYear = parseInt(yearStr) + 1
        const nextYearShort = (nextYear % 100).toString().padStart(2, "0")
        return `${yearStr}-${nextYearShort}`
    }

    if (!selectedYear && availableYears.length === 0) {
        return null
    }

    if (availableYears.length === 0) {
        return (
            <span className="text-sm text-muted-foreground font-bold">
        {formatYear(selectedYear)}
      </span>
        )
    }

    return (
        <div className="flex items-center gap-2">
      {/* "Levy Year" — deliberately NOT "Financial Year". A building's levy year
          defaults to the calendar year (financial_year_start_month=1) and is
          independent of the Australian tax financial year (Jul-Jun), which is
          used correctly elsewhere for council rates/land tax. Conflating the
          two labels here previously made a not-yet-started levy year look like
          an equivalent, selectable "FY" option. */}
      {showLabel && (
      <span className="text-xs font-bold text-slate-400 uppercase tracking-widest hidden sm:inline">
        Levy Year:
      </span>
      )}
            <Select
                value={String(selectedYear || "")}
                onValueChange={setSelectedYear}
            >
                <SelectTrigger
                    className={triggerClassName || "w-[145px] h-10 text-sm font-bold bg-white/50 border-white/20 rounded-xl shadow-sm focus:ring-indigo-500"}>
                    <SelectValue placeholder="Select Year"/>
                </SelectTrigger>
                <SelectContent className="rounded-xl border-white/20 shadow-xl backdrop-blur-lg bg-white/90">
                    {availableYears.map((year) => (
                        <SelectItem
                            key={year}
                            value={String(year)}
                            className="text-sm font-medium focus:bg-indigo-50 focus:text-indigo-600 rounded-lg"
                        >
                            {formatYear(year)}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </div>
    )
}

export default YearSelector
