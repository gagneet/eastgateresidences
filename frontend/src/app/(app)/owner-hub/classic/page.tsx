// @ts-nocheck
"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {useActiveUnit} from "@/hooks/useActiveUnit";
import { ArrowLeft, Sparkles } from "lucide-react";
import { motion } from "framer-motion";
import { Skeleton } from "@/components/ui/skeleton";
import YearSelector from "@/components/widgets/YearSelector";
import dynamic from "next/dynamic";

// Import the upgraded classic owner dashboard from the pages layer.
// It keeps the classic route while carrying the newer dashboard components and data cards.
const LegacyOwnerDashboard = dynamic(
    () => import("@/pages/dashboard/OwnerDashboard"),
    { ssr: false,
 loading: () => (
        <div className="space-y-6 animate-in fade-in duration-500">
            <Skeleton className="h-64 rounded-3xl w-full"/>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32 rounded-2xl"/>)}
            </div>
        </div>
    )}
);
/**
 * @generated FunctionHeader
 * Function: OwnerOldPage
 * Path: frontend/src/app/(app)/owner-hub/classic/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function OwnerOldPage() {
    const { user, isAdmin, isManager, isECMember, loading } = useAuth();
    const router = useRouter();

    // Match the current dashboard loader: legacy owner access and data should follow
    // the active unit switcher first, then fall back to the user's primary/owned unit.
    const {activeUnit: activeUnitNumber} = useActiveUnit();
    const isOwner = user?.role === "owner";
    const isManagementWithUnit = (isAdmin() || isManager() || isECMember()) && !!activeUnitNumber;
    const canView = isOwner || isManagementWithUnit;
    const newLayoutHref = isManagementWithUnit ? "/owner-view" : "/dashboard";

    if (loading) {
        return (
            <div className="space-y-6 animate-in fade-in duration-500">
                <Skeleton className="h-64 rounded-3xl w-full"/>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32 rounded-2xl"/>)}
                </div>
            </div>
        );
    }

    if (!canView) {
        router.replace("/dashboard");
        return null;
    }

    return (
        <div className="pb-20">
            <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-6 flex items-center justify-between"
            >
                <button
                    onClick={() => router.push(newLayoutHref)}
                    className="flex items-center gap-2 text-slate-500 hover:text-slate-900 text-sm font-bold transition-colors group"
                    aria-label="Switch to new owner layout"
                >
                    <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" aria-hidden="true"/>
                    New Layout
                    <Sparkles className="w-3.5 h-3.5 text-indigo-400" aria-hidden="true"/>
                </button>
                <div className="flex items-center gap-3">
                    {/* Prominent Levy Year selector for the classic owner view (bound to the shared
                        AuthContext.selectedYear, same as the new layout's top-bar selector). */}
                    <YearSelector/>
                    <span className="text-[10px] font-black text-slate-400 uppercase tracking-[0.18em] bg-slate-100 px-3 py-1 rounded-full">
                        Classic Owner View
                    </span>
                </div>
            </motion.div>

            <LegacyOwnerDashboard/>
        </div>
    );
}
