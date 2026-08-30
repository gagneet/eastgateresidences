"use client"

import React from "react"
import {
    ActivityFeedPremium,
    CommunityActivityCard,
    MarketPulseCard,
    MetricCard,
    UtilityComparisonCard
} from "@/components/dashboard/premium"
import {Bell, Calendar, Heart, ShoppingBag} from "lucide-react"
import {motion} from "framer-motion"
import {useRouter} from "next/navigation"

interface DashboardProps {
    data: any
    selectedYear?: string
}
/**
 * @generated FunctionHeader
 * Function: TenantDashboard
 * Path: frontend/src/app/(dashboard)/dashboard/TenantDashboard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function TenantDashboard({data}: DashboardProps) {
    const router = useRouter();
    return (
        <div className="space-y-10">
            {/* Section 1: Tenant Pulse */}
            <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <MetricCard
                    title="Active Notices"
                    value={data?.stats?.active_announcements || 3}
                    subtitle="Important updates"
                    icon={Bell}
                    colorClass="bg-amber-50 text-amber-600"
                    delay={0.1}
                    tooltip="View community announcements"
                    onClick={() => router.push('/community/notices')}
                />
                <MetricCard
                    title="Community Tasks"
                    value={2}
                    subtitle="Your action items"
                    icon={Calendar}
                    colorClass="bg-indigo-50 text-indigo-600"
                    delay={0.2}
                    tooltip="View your tasks and schedule"
                    onClick={() => router.push('/maintenance/schedule')}
                />
                <MetricCard
                    title="Marketplace"
                    value={data?.stats?.active_listings || 0}
                    subtitle="Recent listings"
                    icon={ShoppingBag}
                    colorClass="bg-emerald-50 text-emerald-600"
                    delay={0.3}
                    tooltip="Browse community marketplace"
                    onClick={() => router.push('/community/marketplace')}
                />
                <MetricCard
                    title="Resident Perks"
                    value="4"
                    subtitle="Exclusive offers"
                    icon={Heart}
                    colorClass="bg-rose-50 text-rose-600"
                    delay={0.4}
                    tooltip="View resident-only perks and offers"
                />
            </section>

            {/* Section 2: Community Focus (Highlighted) */}
            <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 relative">
                    <div
                        className="absolute -inset-1 bg-gradient-to-r from-indigo-500 to-violet-600 rounded-[2.2rem] blur opacity-20"/>
                    <ActivityFeedPremium/>
                </div>
                <div className="space-y-6">
                    <CommunityActivityCard
                        updateCount={data?.stats?.active_announcements}
                        latestUpdate="New community guidelines posted"
                    />
                    <UtilityComparisonCard/>
                </div>
            </section>

            {/* Section 3: Extra Info */}
            <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <MarketPulseCard
                    suburb={data?.market?.suburb}
                    medianPrice={data?.market?.unit_median}
                    growth={data?.market?.growth_12m}
                />
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.5}}
                    className="p-8 rounded-[2rem] border border-white/20 bg-white/40 backdrop-blur-xl shadow-2xl flex flex-col justify-between"
                >
                    <div>
                        <h3 className="text-slate-900 text-xl font-black tracking-tight mb-1">Building Support</h3>
                        <p className="text-slate-500 text-sm font-medium">Quick links for resident services</p>
                    </div>
                    <div className="mt-6 grid grid-cols-2 gap-4">
                        <button
                            className="p-4 bg-indigo-50 rounded-2xl border border-indigo-100 text-indigo-700 font-bold text-xs hover:bg-indigo-100 transition-colors">
                            Request Maintenance
                        </button>
                        <button
                            className="p-4 bg-slate-50 rounded-2xl border border-slate-100 text-slate-700 font-bold text-xs hover:bg-slate-100 transition-colors">
                            Book Facilities
                        </button>
                        <button
                            className="p-4 bg-slate-50 rounded-2xl border border-slate-100 text-slate-700 font-bold text-xs hover:bg-slate-100 transition-colors">
                            Emergency Contacts
                        </button>
                        <button
                            className="p-4 bg-slate-50 rounded-2xl border border-slate-100 text-slate-700 font-bold text-xs hover:bg-slate-100 transition-colors">
                            Resident Handbook
                        </button>
                    </div>
                </motion.div>
            </section>
        </div>
    )
}

export default TenantDashboard;
