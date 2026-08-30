// @featuretrace:community-hub — Single source for the Building Pulse / health score in the UI.
// Layer: frontend
// Data flow: dashboard -> useBuildingPulse() -> GET /community-dashboard/health-score
//            -> health_score_service.compute_building_health_score -> score|insufficient_data (building-scoped).
// Related: backend/services/health_score_service.py
//          backend/routers/community_dashboard.py
//          frontend/src/pages/dashboard/ManagerDashboard.jsx
//          frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx

import {useCallback, useEffect, useState} from 'react';
import {useAuth} from '../contexts/AuthContext';

/**
 * The Building Pulse score, from the backend, for every dashboard.
 *
 * ## Why this exists
 *
 * On 2026-08-24 the two management dashboards showed **different scores for the
 * same building on the same data** — 26/100 on `/dashboard` and 31/100 on
 * `/management/classic`. Both were computed in the browser, by different
 * formulas, and neither used the canonical backend service. Reproduced exactly:
 *
 *     old:  0*0.35 + 0*0.30 + 100*0.20 + 95*0.10 + 24*0.05  = 31   (weighted)
 *     new:  (0 + 0 + 100 + 0 + 30) / 5                       = 26   (unweighted mean)
 *
 * Four independent divergences produced that gap:
 *
 * | Axis | old page | new page |
 * |---|---|---|
 * | Maintenance penalty | `min(40, open*2)` — floor 60 | `min(100, open*5)` — floor 0 |
 * | Governance | `max(50, 95 - pending*5)` — floor **50** | compliance% minus penalties — floor 0 |
 * | Community | `activities * 8` | `activities * 10` |
 * | Aggregate | weighted 35/30/20/10/5 | `finance_health.score` ?? unweighted mean |
 *
 * Two of those are worth calling out beyond the arithmetic:
 *
 * - The old page's governance axis **cannot score below 50**, whatever the
 *   building is actually doing. That is the same "absence scores well" bug that
 *   made an empty building rate 75/100 in the backend.
 * - The new page falls back to `finance_health.score` — a *finance* metric
 *   rendered under a *building health* label. The codebase already has a hard
 *   rule about exactly this (Collection Rate vs Fund Health must never share a
 *   label); this is the same mistake in a different place.
 *
 * **Neither page was right.** CLAUDE.md is explicit that frontends render
 * backend-calculated view models and no page computes a metric independently of
 * the one canonical service. That service is
 * `health_score_service.compute_building_health_score`, reached through
 * `GET /community-dashboard/health-score`.
 *
 * ## What you get
 *
 * `{score, grade, status, components, unavailableComponents, coverage, loading, error, reload}`
 *
 * `score` is `null` when the backend could not measure enough of the building to
 * publish one. **Render that as "not enough data" — never as 0.** A zero draws a
 * full red gauge and reads as "this building is failing" when the truth is "we
 * have nothing to go on".
 */
export function useBuildingPulse() {
    const {api} = useAuth();
    const [state, setState] = useState({
        score: null,
        grade: null,
        status: null,
        components: {},
        unavailableComponents: [],
        coverage: 0,
        loading: true,
        error: null,
    });

    const load = useCallback(async () => {
        setState((s) => ({...s, loading: true, error: null}));
        try {
            const res = await api.get('/community-dashboard/health-score');
            const d = res.data || {};
            setState({
                // Deliberately NOT `?? 0`. Null means unmeasurable, and coercing
                // it to zero is the bug this hook exists to stop.
                score: typeof d.score === 'number' ? d.score : null,
                grade: d.grade ?? null,
                status: d.status ?? null,
                components: d.components || {},
                unavailableComponents: d.unavailable_components || [],
                coverage: d.coverage ?? 0,
                loading: false,
                error: null,
            });
        } catch (e) {
            // Fail to "unknown", never to a number. A failed request must not
            // be indistinguishable from a measured low score.
            setState({
                score: null,
                grade: null,
                status: 'error',
                components: {},
                unavailableComponents: [],
                coverage: 0,
                loading: false,
                error: e?.message || 'Failed to load building health',
            });
        }
    }, [api]);

    useEffect(() => {
        load();
    }, [load]);

    return {...state, reload: load};
}

/**
 * Turn the hook's component map into the `HealthAxis[]` PulseScoreCard expects.
 *
 * An unavailable component is passed through as `v: null` rather than 0 so the
 * card can render "no data" for that axis. Callers must handle null — see
 * BuildingHealthPage for the pattern.
 */
export function pulseAxesFrom(components) {
    const palette = {
        financial: '#16A34A',
        maintenance: '#F59E0B',
        compliance: '#0EA5E9',
        engagement: '#E11D48',
        dispute: '#7C3AED',
    };
    const labels = {
        financial: 'Financial',
        maintenance: 'Maintenance',
        compliance: 'Compliance',
        engagement: 'Engagement',
        dispute: 'Disputes',
    };
    return Object.keys(labels).map((key) => ({
        k: labels[key],
        v: components?.[key] ?? null,
        color: palette[key],
    }));
}

export default useBuildingPulse;
