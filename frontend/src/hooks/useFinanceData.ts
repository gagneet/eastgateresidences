"use client";
import {useCallback, useEffect, useRef, useState} from 'react';

export interface FinanceState {
    health: any | null;
    forecasts: any[];
    anomalies: any[];
    categories: any[];
    lotSummaries: any[];
    cashflow: any | null;
    levyProjection: any | null;
    loading: boolean;
    error: any | null;
}
/**
 * React hook that fetches the "finance health" cluster of dashboard data
 * (health score, forecasts, anomalies, levy categories, per-lot summaries,
 * cashflow, levy projection) for a given year and keeps it on an
 * auto-refresh timer.
 *
 * IMPORTANT — this hook is NOT wired to the canonical
 * `/finance/calculation-registry` contract (see
 * backend/services/finance_calculation_registry.py) or to the
 * `finance_route_cutover_service` Postgres-primary policy list. It calls a
 * separate family of endpoints (`/finance/health`, `/finance/forecast`,
 * `/finance/anomalies`, `/levy-categories`, `/finance/lot-summary`,
 * `/finance/cashflow`, `/finance/levy-projection`) that read from MongoDB
 * only. Pages consuming this hook (see
 * docs/finances/financial-data-consolidation-map.md) are therefore on the
 * legacy/Mongo-only path, not the 8-route Postgres-aware path used by
 * FinancePage's core summary/kpi-contract calls.
 *
 * @param api - axios-like client exposing `.get(path)`.
 * @param year - levy/financial year to fetch data for; re-fetches on change.
 * @param options.refreshInterval - ms between auto-refreshes; 0 disables the timer.
 * @returns `{ ...FinanceState, refresh }` — current state plus a manual refetch function.
 */
export function useFinanceData(api: any, year: string | number, {refreshInterval = 300000} = {}) {
    const [state, setState] = useState<FinanceState>({
        health: null,
        forecasts: [],
        anomalies: [],
        categories: [],
        lotSummaries: [],
        cashflow: null,
        levyProjection: null,
        loading: false,
        error: null,
    });

    // Keep latest api/year in a ref so the interval callback always sees current values
    // without being listed as a dependency (prevents interval teardown on every render).
    const paramsRef = useRef({api, year});
    useEffect(() => {
        paramsRef.current = {api, year};
    }, [api, year]);

    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const fetchAll = useCallback(async () => {
        const {api: currentApi, year: currentYear} = paramsRef.current;
        if (!currentApi || !currentYear) return;

        setState(prev => ({...prev, loading: true, error: null}));

        const [healthRes, forecastRes, anomalyRes, categoryRes, lotRes, cashflowRes, projectionRes] =
            await Promise.allSettled([
                currentApi.get(`/finance/health?year=${currentYear}`),
                currentApi.get(`/finance/forecast?year=${currentYear}`),
                currentApi.get(`/finance/anomalies?year=${currentYear}&resolved=false`),
                currentApi.get(`/levy-categories?year=${currentYear}`),
                currentApi.get(`/finance/lot-summary?year=${currentYear}`),
                currentApi.get(`/finance/cashflow?year=${currentYear}`),
                currentApi.get(`/finance/levy-projection?year=${currentYear}`),
            ]);

        const rawForecasts = forecastRes.status === 'fulfilled' ? (forecastRes.value.data?.forecasts || []) : [];
        const rawCategories = categoryRes.status === 'fulfilled'
            ? (categoryRes.value.data?.categories || categoryRes.value.data || [])
            : [];
        const rawLotSummaries = lotRes.status === 'fulfilled' ? (lotRes.value.data?.summaries || []) : [];
        const rawAnomalies = anomalyRes.status === 'fulfilled' ? (anomalyRes.value.data?.anomalies || []) : [];

        setState({
            health: healthRes.status === 'fulfilled' ? healthRes.value.data : null,
            // Filter out malformed items that are missing required fields to prevent component crashes
            forecasts: Array.isArray(rawForecasts)
                ? rawForecasts.filter(f => f && f.category != null)
                : [],
            anomalies: Array.isArray(rawAnomalies) ? rawAnomalies : [],
            categories: Array.isArray(rawCategories)
                ? rawCategories.filter(c => c && (c.name != null || c.category != null))
                : [],
            lotSummaries: Array.isArray(rawLotSummaries) ? rawLotSummaries : [],
            cashflow: cashflowRes.status === 'fulfilled' ? cashflowRes.value.data : null,
            levyProjection: projectionRes.status === 'fulfilled' ? projectionRes.value.data : null,
            loading: false,
            error: null,
        });
    }, []); // stable — reads from paramsRef at call time

    // Re-fetch when year changes
    useEffect(() => {
        fetchAll();
    }, [year, fetchAll]);

    // Auto-refresh interval (only set once, stable across re-renders)
    useEffect(() => {
        if (refreshInterval <= 0) return;
        intervalRef.current = setInterval(fetchAll, refreshInterval);
        return () => {
            if (intervalRef.current) {
                clearInterval(intervalRef.current);
                intervalRef.current = null;
            }
        };
    }, [fetchAll, refreshInterval]);

    return {...state, refresh: fetchAll};
}
