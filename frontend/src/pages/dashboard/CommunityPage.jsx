// @featuretrace:community-hub — Community Hub page: central building activity dashboard.
// Layer: frontend
// Data flow: CommunityPage.jsx → /community-dashboard/building-summary → db.building_summaries (building-scoped).
// Related: backend/routers/community_dashboard.py
//           backend/services/health_score_service.py
//           frontend/src/pages/dashboard/PetRegisterPage.jsx
//           frontend/src/pages/dashboard/SmartRequestPage.jsx
//           frontend/src/pages/dashboard/BookingsPage.jsx

"use client";
import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Skeleton } from '../../components/ui/skeleton';
import { toast } from 'sonner';
import {
    Users, Wrench, Vote, Star, Bell, MessageCircle,
    Calendar, TrendingUp, Heart, RefreshCw,
    ArrowRight, ShieldCheck, MessageSquare, ClipboardList,
    Dog, BookOpen, PawPrint
} from 'lucide-react';
import Link from 'next/link';

const GRADE_COLOURS = {
    A: 'bg-emerald-100 text-emerald-800 border-emerald-200',
    B: 'bg-blue-100 text-blue-800 border-blue-200',
    C: 'bg-amber-100 text-amber-800 border-amber-200',
    D: 'bg-orange-100 text-orange-800 border-orange-200',
    F: 'bg-red-100 text-red-800 border-red-200',
};

const QUICK_LINKS = [
    {label: 'Notices', href: '/community/notices', icon: Bell, desc: 'Building notices & alerts'},
    {label: 'Events', href: '/community/events', icon: Calendar, desc: 'Upcoming community events'},
    {label: 'Chat', href: '/community/chat', icon: MessageCircle, desc: 'Resident chat groups'},
    {label: 'Proposals', href: '/governance/proposals', icon: Vote, desc: 'Open votes & motions'},
    {label: 'Volunteer', href: '/community/volunteer', icon: Heart, desc: 'Help out & earn credits'},
    {label: 'Smart Request', href: '/requests/new', icon: MessageSquare, desc: 'Submit a request or query'},
    {label: 'Pet Register', href: '/community/pet-register', icon: PawPrint, desc: 'Registered building pets'},
    {label: 'Bookings', href: '/community/bookings', icon: Calendar, desc: 'Amenity & move-in bookings'},
    {label: 'My Requests', href: '/requests', icon: ClipboardList, desc: 'Track all your requests'},
];
/**
 * @generated FunctionHeader
 * Function: StatCard
 * Path: frontend/src/pages/dashboard/CommunityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatCard({icon: Icon, label, value, colour = 'text-gray-700', href, description}) {
    const content = (
        <div
            className={`flex items-center gap-3 rounded-xl border bg-white p-4 shadow-sm transition-all ${href ? 'cursor-pointer hover:border-indigo-300 hover:shadow-md group' : ''}`}
            title={description}
            aria-label={description ? `${label}: ${value}. ${description}` : `${label}: ${value}`}
        >
            <div className="rounded-lg bg-gray-50 p-2">
                <Icon className={`h-5 w-5 ${colour}`} aria-hidden="true"/>
            </div>
            <div className="flex-1">
                <p className="text-2xl font-bold text-gray-900">{value ?? '—'}</p>
                <p className="text-xs text-gray-500">{label}</p>
            </div>
            {href && <ArrowRight
                className="h-3.5 w-3.5 text-slate-300 group-hover:text-indigo-400 transition-colors shrink-0"
                aria-hidden="true"/>}
        </div>
    );
    if (href) return <Link href={href} aria-label={`${label}: ${value} — click to view`}>{content}</Link>;
    return content;
}
/**
 * @generated FunctionHeader
 * Function: CommunityPage
 * Path: frontend/src/pages/dashboard/CommunityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CommunityPage() {
    const {api, isAdmin, isManager, isECMember} = useAuth();
    const [summary, setSummary] = useState(null);
    const [health, setHealth] = useState(null);
    const [loading, setLoading] = useState(true);
    const [recomputing, setRecomputing] = useState(false);

    const canRecompute = isAdmin() || isManager() || isECMember();
    /**
     * @generated FunctionHeader
     * Function: fetchData
     * Path: frontend/src/pages/dashboard/CommunityPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchData = async () => {
        setLoading(true);
        try {
            const [summaryRes, healthRes] = await Promise.all([
                api.get('/community-dashboard/building-summary').catch(() => ( {data: null} )),
                api.get('/community-dashboard/health-score').catch(() => ( {data: null} )),
            ]);
            setSummary(summaryRes.data);
            setHealth(healthRes.data);
        } catch {
            toast.error('Failed to load community data');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, []);
    /**
     * @generated FunctionHeader
     * Function: handleRecompute
     * Path: frontend/src/pages/dashboard/CommunityPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            await api.post('/community-dashboard/recompute');
            await fetchData();
            toast.success('Community stats refreshed');
        } catch {
            toast.error('Recompute failed');
        } finally {
            setRecomputing(false);
        }
    };

    const grade = health?.grade ?? summary?.health_grade ?? '—';
    const score = health?.score ?? summary?.health_score ?? null;
    const gradeColour = GRADE_COLOURS[ grade ] ?? 'bg-gray-100 text-gray-700 border-gray-200';

    return (
        <div className="space-y-8" role="main" aria-label="Community Hub">
            {/* Header */}
            <div className="flex items-end justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-black text-slate-900 tracking-tight">
                        Community <span className="text-indigo-600">Hub</span>
                    </h1>
                    <p className="text-slate-500 mt-1 text-sm">
                        Building health, activity overview, and quick access to all community features
                    </p>
                </div>
                {canRecompute && (
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleRecompute}
                        disabled={recomputing || loading}
                        className="shrink-0"
                        aria-label="Refresh community statistics"
                    >
                        <RefreshCw className={`mr-2 h-3.5 w-3.5 ${recomputing ? 'animate-spin' : ''}`}
                                   aria-hidden="true"/>
                        Refresh stats
                    </Button>
                )}
            </div>

            {/* Health score banner */}
            {loading ? (
                <Skeleton className="h-28 w-full rounded-2xl"/>
            ) : (
                <Card className="rounded-2xl border-0 bg-gradient-to-r from-indigo-50 to-slate-50 shadow-sm"
                      role="region" aria-label="Building Health Score">
                    <CardContent className="flex items-center gap-6 p-6">
                        <div
                            className={`flex h-20 w-20 items-center justify-center rounded-2xl border-2 text-4xl font-black shrink-0 ${gradeColour}`}
                            aria-label={`Grade ${grade}`}
                        >
                            {grade}
                        </div>
                        <div className="flex-1">
                            <p className="text-sm font-semibold text-slate-500 uppercase tracking-wide">Building Health
                                Score</p>
                            <p className="text-5xl font-black text-slate-900">
                                {score !== null ? Math.round(score) : '—'}
                                <span className="text-xl font-medium text-slate-400">/100</span>
                            </p>
                            {health?.summary && (
                                <p className="mt-1 text-sm text-slate-600">{health.summary}</p>
                            )}
                        </div>
                        {health?.factors && (
                            <div className="hidden md:flex flex-col gap-1 text-xs text-slate-500"
                                 aria-label="Health score factors">
                                {Object.entries(health.factors).slice(0, 4).map(([k, v]) => (
                                    <div key={k} className="flex items-center gap-2">
                                        <ShieldCheck className="h-3 w-3 text-indigo-400" aria-hidden="true"/>
                                        <span className="capitalize">{k.replace(/_/g, ' ')}</span>
                                        <span
                                            className="ml-auto font-semibold text-slate-700">{typeof v === 'number' ? Math.round(v) : v}</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Stats grid */}
            {loading ? (
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4" aria-busy="true"
                     aria-label="Loading statistics">
                    {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-20 rounded-xl"/>)}
                </div>
            ) : summary && (
                <section aria-label="Building statistics">
                    <h2 className="text-sm font-bold uppercase tracking-widest text-slate-400 mb-4">Building at a
                        Glance</h2>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                        <StatCard icon={Users} label="Total lots" value={summary.total_lots} colour="text-indigo-500"
                                  href="/community/directory" description="Total registered lots in the building"/>
                        <StatCard icon={Wrench} label="Open maintenance" value={summary.open_maintenance_requests}
                                  colour="text-amber-500" href="/maintenance"
                                  description="Maintenance requests currently being processed"/>
                        <StatCard icon={Vote} label="Open proposals" value={summary.open_proposals}
                                  colour="text-blue-500" href="/governance/proposals"
                                  description="Active votes and proposals awaiting resolution"/>
                        <StatCard icon={Star} label="Volunteer events YTD" value={summary.volunteer_events_ytd}
                                  colour="text-emerald-500" href="/community/volunteer"
                                  description="Completed volunteer events this year"/>
                        {( summary.registered_pets ?? 0 ) >= 0 && (
                            <StatCard icon={PawPrint} label="Registered pets" value={summary.registered_pets ?? 0}
                                      colour="text-pink-500" href="/community/pet-register"
                                      description="Pets registered and approved for the building"/>
                        )}
                        {( summary.open_smart_requests ?? 0 ) >= 0 && (
                            <StatCard icon={MessageSquare} label="Open requests"
                                      value={summary.open_smart_requests ?? 0} colour="text-sky-500"
                                      href="/requests/new"
                                      description="Smart requests currently under review"/>
                        )}
                        {( summary.upcoming_bookings ?? 0 ) >= 0 && (
                            <StatCard icon={Calendar} label="Upcoming bookings" value={summary.upcoming_bookings ?? 0}
                                      colour="text-violet-500" href="/community/bookings"
                                      description="Amenity and facility bookings coming up"/>
                        )}
                        {summary.arrears_lots > 0 && (
                            <StatCard icon={TrendingUp} label="Lots in arrears" value={summary.arrears_lots}
                                      colour="text-red-500" href="/intelligence/debt-recovery"
                                      description="Units with outstanding levy payments"/>
                        )}
                        {summary.overdue_work_orders > 0 && (
                            <StatCard icon={Wrench} label="Overdue work orders" value={summary.overdue_work_orders}
                                      colour="text-orange-500" href="/maintenance?tab=work-orders"
                                      description="Work orders that have exceeded their SLA"/>
                        )}
                    </div>
                </section>
            )}

            {/* Quick links */}
            <section>
                <h2 className="text-sm font-bold uppercase tracking-widest text-slate-400 mb-4">Quick Access</h2>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3" role="list"
                     aria-label="Quick navigation links">
                    {QUICK_LINKS.map(({label, href, icon: Icon, desc}) => (
                        <Link
                            key={href}
                            href={href}
                            role="listitem"
                            className="group flex items-center gap-3 rounded-xl border bg-white p-4 shadow-sm hover:border-indigo-300 hover:shadow-md transition-all focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-1"
                            aria-label={`${label} — ${desc}`}
                            data-testid={`community-link-${label.toLowerCase().replace(/\s+/g, '-')}`}
                        >
                            <div className="rounded-lg bg-indigo-50 p-2 group-hover:bg-indigo-100 transition-colors">
                                <Icon className="h-4 w-4 text-indigo-600" aria-hidden="true"/>
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-semibold text-slate-800">{label}</p>
                                <p className="text-xs text-slate-400 truncate">{desc}</p>
                            </div>
                            <ArrowRight
                                className="h-3.5 w-3.5 text-slate-300 group-hover:text-indigo-400 transition-colors shrink-0"
                                aria-hidden="true"/>
                        </Link>
                    ))}
                </div>
            </section>
        </div>
    );
}
