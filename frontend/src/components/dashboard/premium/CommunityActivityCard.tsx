"use client";

import React from "react";
import {motion} from "framer-motion";
import {ArrowRight, Megaphone} from "lucide-react";

interface CommunityActivityProps {
    updateCount: number;
    latestUpdate: string;
}
/**
 * @generated FunctionHeader
 * Function: CommunityActivityCard
 * Path: frontend/src/components/dashboard/premium/CommunityActivityCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const CommunityActivityCard = ({
                                          updateCount = 8,
                                          latestUpdate = "AGM 2025 Notice posted"
                                      }: CommunityActivityProps) => {

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.6, delay: 0.3}}
            whileHover={{y: -5}}
            className="relative overflow-hidden p-6 rounded-3xl border border-white/20 bg-white/40 backdrop-blur-xl shadow-2xl shadow-slate-200/50 group"
        >
            <div
                className="absolute -top-10 -right-10 w-32 h-32 bg-amber-500/5 blur-3xl rounded-full group-hover:bg-amber-500/10 transition-colors"/>

            <div className="flex justify-between items-start mb-6">
                <div className="p-3 bg-amber-500/10 rounded-2xl relative">
                    <Megaphone className="w-6 h-6 text-amber-600"/>
                    <span className="absolute -top-1 -right-1 flex h-3 w-3">
            <span
                className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-amber-500 border-2 border-white"></span>
          </span>
                </div>
                <div className="flex flex-col items-end">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Activity Pulse</span>
                    <span className="text-amber-600 font-bold text-sm">Active Now</span>
                </div>
            </div>

            <div className="space-y-4">
                <div className="space-y-1">
                    <h3 className="text-slate-500 text-sm font-medium">New Updates This Week</h3>
                    <div className="flex items-baseline gap-2">
                        <span className="text-4xl font-black text-slate-900">{updateCount}</span>
                        <span className="text-slate-400 font-bold text-xs">Events</span>
                    </div>
                </div>

                <div className="p-3 bg-white/50 border border-white/40 rounded-2xl">
                    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Latest Update</p>
                    <p className="text-xs font-bold text-slate-700 truncate">{latestUpdate}</p>
                </div>
            </div>

            <button
                className="mt-6 w-full py-3 bg-slate-900 text-white rounded-2xl text-xs font-bold flex items-center justify-center gap-2 hover:bg-slate-800 transition-colors">
                View Activity Feed
                <ArrowRight className="w-4 h-4"/>
            </button>
        </motion.div>
    );
};

export default CommunityActivityCard;
