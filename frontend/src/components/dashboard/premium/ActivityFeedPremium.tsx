"use client";

import React from "react";
import {useRouter} from "next/navigation";
import Link from "next/link";
import {motion} from "framer-motion";
import {ArrowRight, FileText, Megaphone, ShoppingBag, UserPlus, Vote, Wrench} from "lucide-react";
import {formatDistanceToNow} from "date-fns";
import {Tooltip, TooltipContent, TooltipTrigger} from "@/components/ui/tooltip";

interface Activity {
    id?: string | number;
    type: string;
    title: string;
    created_at: string;
    visibility?: string;
    href?: string;
}

interface ActivityFeedProps {
    activities?: Activity[];
    onActivitySelect?: (activity: Activity) => void;
}

const typeConfig: Record<string, any> = {
    announcement: {icon: Megaphone, color: "bg-amber-100 text-amber-600"},
    maintenance: {icon: Wrench, color: "bg-blue-100 text-blue-600"},
    document: {icon: FileText, color: "bg-emerald-100 text-emerald-600"},
    marketplace: {icon: ShoppingBag, color: "bg-indigo-100 text-indigo-600"},
    resident: {icon: UserPlus, color: "bg-rose-100 text-rose-600"},
    vote: {icon: Vote, color: "bg-purple-100 text-purple-600"},
    meeting: {icon: Vote, color: "bg-purple-100 text-purple-600"},
    event: {icon: Megaphone, color: "bg-sky-100 text-sky-600"},
    notice: {icon: FileText, color: "bg-orange-100 text-orange-600"},
    default: {icon: Megaphone, color: "bg-slate-100 text-slate-600"}
};

const container = {
    hidden: {opacity: 0},
    show: {
        opacity: 1,
        transition: {
            staggerChildren: 0.1,
            delayChildren: 0.8,
        },
    },
};

const item = {
    hidden: {opacity: 0, x: 20},
    show: {opacity: 1, x: 0, transition: {type: "spring" as const, stiffness: 100}},
};
/**
 * @generated FunctionHeader
 * Function: ActivityFeedPremium
 * Path: frontend/src/components/dashboard/premium/ActivityFeedPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ActivityFeedPremium = ({activities = [], onActivitySelect}: ActivityFeedProps) => {
    const router = useRouter();
    const displayActivities = activities.length > 0 ? activities : [
        {type: "announcement", title: "Welcome to the new Hub", created_at: new Date().toISOString()}
    ];

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.6, delay: 0.8}}
            whileHover={{y: -5}}
            className="p-8 rounded-[2rem] border border-white/20 bg-white/40 backdrop-blur-xl shadow-2xl h-full flex flex-col group min-h-[400px]"
        >
            <div className="flex justify-between items-center mb-8">
                <h3 className="text-slate-900 text-xl font-black tracking-tight">Community Feed</h3>
                <div className="flex items-center gap-2">
           <span
               className="px-3 py-1 bg-indigo-50 text-indigo-600 rounded-full text-[10px] font-bold uppercase tracking-widest flex items-center gap-2">
             <span className="w-1.5 h-1.5 rounded-full bg-indigo-600 animate-pulse" aria-hidden="true"/>
             Live
           </span>
                    {activities.length > 0 && (
                        <span
                            className="text-[10px] font-black text-slate-400 uppercase">{activities.length} updates</span>
                    )}
                    <Link href="/community"
                          className="text-[10px] font-bold text-indigo-500 hover:text-indigo-700 transition-colors uppercase tracking-widest">View
                        All</Link>
                </div>
            </div>

            <motion.div
                variants={container}
                initial="hidden"
                animate="show"
                className="flex-1 space-y-6"
            >
                {displayActivities.map((activity, idx) => {
                    const config = typeConfig[activity.type] || typeConfig.default;
                    const Icon = config.icon;

                    return (
                        <motion.div
                            key={activity.id || idx}
                            variants={item}
                            className="flex items-start gap-4 group/item cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-xl p-1 -m-1"
                            tabIndex={0}
                            role="button"
                            aria-label={`View ${activity.type}: ${activity.title}`}
                            onClick={(e) => {
                                e.stopPropagation();
                                if (activity.href) {
                                    router.push(activity.href);
                                } else {
                                    onActivitySelect?.(activity);
                                }
                            }}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    if (activity.href) {
                                        router.push(activity.href);
                                    } else {
                                        onActivitySelect?.(activity);
                                    }
                                }
                            }}
                        >
                            <div
                                className={`p-3 rounded-2xl ${config.color} transition-transform group-hover/item:scale-110 shadow-sm`}>
                                <Icon className="w-5 h-5"/>
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-black text-slate-900 truncate group-hover/item:text-indigo-600 transition-colors">{activity.title}</p>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <p
                                            className="text-xs font-bold text-slate-400 uppercase tracking-tighter mt-0.5 cursor-help focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500 rounded px-0.5 -mx-0.5"
                                            tabIndex={0}
                                            onClick={(e) => e.stopPropagation()}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' || e.key === ' ') {
                                                    e.stopPropagation();
                                                }
                                            }}
                                        >
                                            {formatDistanceToNow(new Date(activity.created_at), {addSuffix: true})}
                                        </p>
                                    </TooltipTrigger>
                                    <TooltipContent side="top">
                                        {new Date(activity.created_at).toLocaleString()}
                                    </TooltipContent>
                                </Tooltip>
                            </div>
                            <ArrowRight
                                className="w-4 h-4 text-slate-300 opacity-0 group-hover/item:opacity-100 transition-all -translate-x-2 group-hover/item:translate-x-0"/>
                        </motion.div>
                    );
                })}
            </motion.div>

            <button
                className="mt-8 text-xs font-bold text-slate-500 hover:text-slate-900 flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-lg p-1 -m-1"
                aria-label="Browse activity history"
                onClick={() => router.push('/community')}
            >
                Browse History
                <ArrowRight className="w-3 h-3"/>
            </button>
        </motion.div>
    );
};

export default ActivityFeedPremium;
