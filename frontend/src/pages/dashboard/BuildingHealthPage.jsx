// @featuretrace:community-hub — Building Health Score page; renders score, grade and component breakdown.
// Layer: frontend
// Data flow: /intelligence/building-health -> GET /community-dashboard/health-score ->
//            health_score_service.compute_building_health_score -> score|insufficient_data (building-scoped).
// Related: backend/services/health_score_service.py
//          backend/routers/community_dashboard.py
//          frontend/src/app/(app)/intelligence/building-health/page.tsx
import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Activity, AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { healthApi } from '../../lib/api/community-os';
import {PageHeader} from "../../components/shared/PageHeader";

const RADIUS = 72;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const COMPONENT_META = {
    financial: {
        label: 'Financial',
        good: 'Building finances are healthy',
        bad: 'Finances need attention — sinking fund may be underfunded',
    },
    maintenance: {
        label: 'Maintenance',
        good: 'Maintenance is up to date',
        bad: 'Work orders are overdue',
    },
    compliance: {
        label: 'Compliance',
        good: 'All compliance items are current',
        bad: 'Compliance items are overdue',
    },
    engagement: {
        label: 'Engagement',
        good: 'Community is highly engaged',
        bad: 'Low participation in voting and volunteering',
    },
    dispute: {
        label: 'Dispute Resolution',
        good: 'Very few disputes',
        bad: 'Elevated dispute activity',
    },
};
/**
 * @generated FunctionHeader
 * Function: gradeColor
 * Path: frontend/src/pages/dashboard/BuildingHealthPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function gradeColor(grade) {
    if (grade === 'A') return 'bg-green-100 text-green-800 border-green-200';
    if (grade === 'B') return 'bg-lime-100 text-lime-800 border-lime-200';
    if (grade === 'C') return 'bg-amber-100 text-amber-800 border-amber-200';
    return 'bg-red-100 text-red-800 border-red-200';
}
/**
 * @generated FunctionHeader
 * Function: ringColor
 * Path: frontend/src/pages/dashboard/BuildingHealthPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ringColor(score) {
    if (score >= 80) return '#22c55e';
    if (score >= 60) return '#f59e0b';
    return '#ef4444';
}
/**
 * @generated FunctionHeader
 * Function: barColor
 * Path: frontend/src/pages/dashboard/BuildingHealthPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function barColor(score) {
    if (score >= 80) return 'bg-green-500';
    if (score >= 60) return 'bg-amber-400';
    return 'bg-red-500';
}
/**
 * @generated FunctionHeader
 * Function: getExplanation
 * Path: frontend/src/pages/dashboard/BuildingHealthPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getExplanation(key, score) {
    const meta = COMPONENT_META[ key ];
    if (!meta) return '';
    return score >= 80 ? meta.good : score < 60 ? meta.bad : `${meta.label} score is moderate`;
}
/**
 * @generated FunctionHeader
 * Function: BuildingHealthPage
 * Path: frontend/src/pages/dashboard/BuildingHealthPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingHealthPage() {
    const {api} = useAuth();
    const hApi = healthApi(api);

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    /**
     * @generated FunctionHeader
     * Function: load
     * Path: frontend/src/pages/dashboard/BuildingHealthPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await hApi.score();
            setData(res.data);
        } catch (err) {
            if (err?.response?.status === 404) {
                setData(null);
            } else {
                setError('Failed to load building health score.');
            }
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    // A null score means the backend could not measure enough of the building to
    // publish one — it is NOT a score of zero. Coercing it with `?? 0` drew a
    // full red ring at 0/100, which reads as "this building is failing" when the
    // truth is "we have no data". Missing and zero are distinct states.
    const hasScore = typeof data?.score === 'number' && data?.status !== 'insufficient_data';
    const score = hasScore ? data.score : null;
    const grade = hasScore ? ( data?.grade ?? '—' ) : '—';
    const components = data?.components || {};
    const unavailable = data?.unavailable_components || [];
    const dashOffset = hasScore ? CIRCUMFERENCE - ( score / 100 ) * CIRCUMFERENCE : CIRCUMFERENCE;

    return (
        <div className="p-6 max-w-3xl mx-auto space-y-6">
            <PageHeader
                title="Building Health Score"
                icon={<Activity className="h-5 w-5"/>}
                description="Overall health of your strata community"
                actions={
                    <Button variant="outline" size="sm" onClick={load} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`}/>
                        Refresh
                    </Button>
                }
            />

            {loading ? (
                <div className="flex justify-center py-20">
                    <Loader2 className="h-10 w-10 animate-spin text-muted-foreground"/>
                </div>
            ) : error ? (
                <Card>
                    <CardContent className="py-12 text-center text-muted-foreground">
                        <AlertCircle className="h-8 w-8 mx-auto mb-2 text-red-400"/>
                        <p>{error}</p>
                    </CardContent>
                </Card>
            ) : !data ? (
                <Card>
                    <CardContent className="py-12 text-center text-muted-foreground">
                        <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50"/>
                        <p>Health score not yet computed for this building.</p>
                    </CardContent>
                </Card>
            ) : !hasScore ? (
                <Card data-testid="health-score-insufficient-data">
                    <CardContent className="py-12 text-center text-muted-foreground space-y-2">
                        <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50"/>
                        <p className="font-medium text-foreground">Not enough data to score this building yet.</p>
                        <p className="text-sm">
                            A health score needs measurable inputs. Add lots, work orders,
                            compliance items and levy data, then refresh.
                        </p>
                        {unavailable.length > 0 && (
                            <p className="text-xs pt-2">
                                Waiting on: {unavailable.join(', ')}
                            </p>
                        )}
                    </CardContent>
                </Card>
            ) : (
                <>
                    {/* Score ring */}
                    <Card>
                        <CardContent className="pt-8 pb-6 flex flex-col items-center gap-4">
                            <svg width="180" height="180" viewBox="0 0 180 180">
                                <circle cx="90" cy="90" r={RADIUS} fill="none" stroke="#e5e7eb" strokeWidth="14"/>
                                <circle
                                    cx="90" cy="90" r={RADIUS}
                                    fill="none"
                                    stroke={ringColor(score)}
                                    strokeWidth="14"
                                    strokeLinecap="round"
                                    strokeDasharray={CIRCUMFERENCE}
                                    strokeDashoffset={dashOffset}
                                    transform="rotate(-90 90 90)"
                                    style={{transition: 'stroke-dashoffset 1s ease'}}
                                />
                                <text x="90" y="84" textAnchor="middle" fontSize="38" fontWeight="700"
                                      fill="currentColor">{score}</text>
                                <text x="90" y="104" textAnchor="middle" fontSize="14" fill="#6b7280">/ 100</text>
                            </svg>

                            <div className="text-center space-y-1">
                                <Badge className={`text-lg px-4 py-1 font-bold ${gradeColor(grade)}`}>
                                    Grade {grade}
                                </Badge>
                                {data.computed_at && (
                                    <p className="text-xs text-muted-foreground">
                                        Last updated {new Date(data.computed_at).toLocaleDateString('en-AU', {
                                        day: 'numeric',
                                        month: 'long',
                                        year: 'numeric'
                                    })}
                                    </p>
                                )}
                            </div>
                        </CardContent>
                    </Card>

                    {/* Component breakdown */}
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium">Component Scores</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {Object.keys(COMPONENT_META).map((key) => {
                                // null = not measurable. Rendering it as 0 would
                                // show a component failing when it is simply absent.
                                const val = components[ key ];
                                if (val === null || val === undefined) {
                                    return (
                                        <div key={key} className="space-y-1 opacity-60">
                                            <div className="flex justify-between text-sm">
                                                <span>{COMPONENT_META[ key ]?.label ?? key}</span>
                                                <span className="italic">No data</span>
                                            </div>
                                            <div className="h-2 rounded bg-muted"/>
                                        </div>
                                    );
                                }
                                const meta = COMPONENT_META[ key ];
                                return (
                                    <div key={key} className="space-y-1">
                                        <div className="flex justify-between text-sm">
                                            <span className="font-medium">{meta.label}</span>
                                            <span className="font-semibold">{val}</span>
                                        </div>
                                        <div className="h-2.5 w-full rounded-full bg-muted overflow-hidden">
                                            <div className={`h-full rounded-full ${barColor(val)}`}
                                                 style={{width: `${val}%`}}/>
                                        </div>
                                        <p className="text-xs text-muted-foreground">{getExplanation(key, val)}</p>
                                    </div>
                                );
                            })}
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    );
}
