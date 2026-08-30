// @featuretrace:dashboard-feed — Activity Feed component: renders live community activity for owner dashboard.
// Layer: frontend
// Data flow: OwnerDashboard.tsx → /analytics/activities → ActivityFeed.tsx (display only).
// Related: frontend/src/pages/dashboard/OwnerDashboard.tsx
//           backend/routers/analytics.py  (/analytics/activities endpoint)
// Scope: (building-scoped)

"use client"

import * as React from "react"
import {useRouter} from "next/navigation"
import {cn} from "@/lib/utils"
import {Card, CardContent, CardHeader, CardTitle} from "../ui/card"
import {Badge} from "../ui/badge"
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "../ui/tooltip"
import {motion} from "framer-motion"
import {
    Megaphone,
    FileText,
    Wrench,
    Vote,
    ShoppingBag,
    UserPlus,
    Clock,
    Activity,
    ArrowRight,
} from "lucide-react"
import {formatDistanceToNow, format} from "date-fns"

export interface ActivityItem {
    id: string
    type: string
    title: string
    created_at: string
    entity_id?: string
}

export interface ActivityFeedProps {
    activities?: ActivityItem[]
    loading?: boolean
    delay?: number
}
/**
 * @generated FunctionHeader
 * Function: ActivityIcon
 * Path: frontend/src/components/dashboard/ActivityFeed.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ActivityIcon: React.FC<{ type: string }> = ({type}) => {
    switch (type) {
        case "announcement":
            return <Megaphone size={16} className="text-blue-500"/>
        case "document":
            return <FileText size={16} className="text-purple-500"/>
        case "maintenance":
            return <Wrench size={16} className="text-orange-500"/>
        case "vote":
            return <Vote size={16} className="text-emerald-500"/>
        case "marketplace":
            return <ShoppingBag size={16} className="text-cyan-500"/>
        case "resident":
            return <UserPlus size={16} className="text-pink-500"/>
        default:
            return <Clock size={16} className="text-slate-400"/>
    }
}
/**
 * @generated FunctionHeader
 * Function: getBadgeClasses
 * Path: frontend/src/components/dashboard/ActivityFeed.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const getBadgeClasses = (type: string) => {
    switch (type) {
        case "announcement":
            return "bg-blue-50 text-blue-600 border-blue-100"
        case "document":
            return "bg-purple-50 text-purple-600 border-purple-100"
        case "maintenance":
            return "bg-orange-50 text-orange-600 border-orange-100"
        case "vote":
            return "bg-emerald-50 text-emerald-600 border-emerald-100"
        case "marketplace":
            return "bg-cyan-50 text-cyan-600 border-cyan-100"
        case "resident":
            return "bg-pink-50 text-pink-600 border-pink-100"
        default:
            return "bg-slate-50 text-slate-500 border-slate-100"
    }
}
/**
 * @generated FunctionHeader
 * Function: ActivityFeed
 * Path: frontend/src/components/dashboard/ActivityFeed.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ActivityFeed: React.FC<ActivityFeedProps> = ({
                                                       activities = [],
                                                       loading = false,
                                                       delay = 0,
                                                   }) => {
    const router = useRouter()
    /**
     * @generated FunctionHeader
     * Function: getLink
     * Path: frontend/src/components/dashboard/ActivityFeed.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getLink = (type: string, entityId?: string) => {
        switch (type) {
            case "announcement":
                return entityId ? `/community/notices?id=${entityId}` : "/community/notices"
            case "document":
                // Deep-link when we know which document. Previously this always
                // returned the bare list, so clicking "Levy Notice …" dropped the
                // user on a page of 240 documents to find it themselves.
                return entityId ? `/documents?doc=${entityId}` : "/documents"
            case "maintenance":
                return entityId
                    ? `/maintenance/${entityId}`
                    : "/maintenance"
            case "vote":
                return "/governance/agm"
            case "marketplace":
                return "/community/marketplace"
            case "resident":
                return "/community/directory"
            default:
                return null
        }
    }

    return (
        <motion.div
            initial={{opacity: 0, x: 20}}
            animate={{opacity: 1, x: 0}}
            transition={{duration: 0.4, delay}}
            className="h-full"
        >
            <Card
                className="h-full border-none shadow-[0_4px_20px_rgba(0,0,0,0.03)] bg-white overflow-hidden transition-all duration-300 hover:shadow-[0_20px_40px_rgba(0,0,0,0.06)] group">
                <CardHeader className="border-b border-slate-50 bg-slate-50/30 pb-4">
                    <CardTitle className="text-xl font-black text-slate-900 tracking-tight flex items-center gap-3">
                        <div className="p-2 rounded-xl bg-slate-900 text-white">
                            <Activity size={18} strokeWidth={2.5}/>
                        </div>
                        Community Pulse
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0 relative">
                    <TooltipProvider>
                        {/* Vertical Timeline Line */}
                        <div className="absolute left-[34px] top-0 bottom-0 w-0.5 bg-slate-100 z-0"/>

                        {loading ? (
                            <div className="p-12 text-center">
                                <div
                                    className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"/>
                                <p className="text-sm font-medium text-slate-400">
                                    Syncing activity...
                                </p>
                            </div>
                        ) : activities.length === 0 ? (
                            <div className="p-12 text-center">
                                <Clock size={32} className="text-slate-200 mx-auto mb-4"/>
                                <p className="text-sm font-medium text-slate-400">Quiet for now</p>
                            </div>
                        ) : (
                            <div
                                className="divide-y divide-slate-50 max-h-[500px] overflow-y-auto relative z-10 scrollbar-hide">
                                {activities.map((activity, index) => (
                                    <motion.div
                                        key={activity.id}
                                        initial={{opacity: 0, x: -10}}
                                        animate={{opacity: 1, x: 0}}
                                        transition={{delay: delay + index * 0.05}}
                                        className={cn(
                                            "p-5 hover:bg-slate-50/80 transition-all duration-300 relative group/item focus-within:z-20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                                            getLink(activity.type, activity.entity_id)
                                                ? "cursor-pointer"
                                                : "cursor-default"
                                        )}
                                        onClick={() => {
                                            const link = getLink(activity.type, activity.entity_id)
                                            if (link) router.push(link)
                                        }}
                                        onKeyDown={(e) => {
                                            const link = getLink(activity.type, activity.entity_id)
                                            if (link && (e.key === "Enter" || e.key === " ")) {
                                                e.preventDefault()
                                                router.push(link)
                                            }
                                        }}
                                        whileTap={
                                            getLink(activity.type, activity.entity_id)
                                                ? {scale: 0.98}
                                                : undefined
                                        }
                                        role={
                                            getLink(activity.type, activity.entity_id)
                                                ? "button"
                                                : "article"
                                        }
                                        tabIndex={
                                            getLink(activity.type, activity.entity_id) ? 0 : undefined
                                        }
                                        aria-label={
                                            getLink(activity.type, activity.entity_id)
                                                ? `View ${activity.type}: ${activity.title}`
                                                : `${activity.type}: ${activity.title}`
                                        }
                                    >
                                        <div className="flex gap-4">
                                            <div className="shrink-0 z-10">
                                                <div
                                                    className="p-2.5 rounded-2xl bg-white shadow-sm border border-slate-100 group-hover/item:scale-110 group-hover/item:shadow-md transition-all duration-300">
                                                    <ActivityIcon type={activity.type}/>
                                                </div>
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center justify-between mb-1">
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <p className="text-sm font-bold text-slate-900 group-hover/item:text-primary transition-colors truncate pr-2 cursor-help focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                                               tabIndex={0} role="button">

                                                                {activity.title}
                                                            </p>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            <p className="max-w-xs">{activity.title}</p>
                                                        </TooltipContent>
                                                    </Tooltip>
                                                    <ArrowRight
                                                        size={14}
                                                        className="text-primary opacity-0 group-hover/item:opacity-100 group-hover/item:translate-x-1 group-focus-visible/item:opacity-100 group-focus-visible/item:translate-x-1 transition-all"
                                                    />
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <Badge
                                                        variant="outline"
                                                        className={cn(
                                                            "text-[9px] h-4 font-black uppercase tracking-tighter",
                                                            getBadgeClasses(activity.type)
                                                        )}
                                                    >
                                                        {activity.type}
                                                    </Badge>
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                            <span
                                className="text-[10px] font-bold text-slate-400 cursor-help focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                tabIndex={0} role="button">

                              •{" "}
                                {formatDistanceToNow(new Date(activity.created_at), {
                                    addSuffix: true,
                                })}
                            </span>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            <p>
                                                                {format(new Date(activity.created_at), "PPP p")}
                                                            </p>
                                                        </TooltipContent>
                                                    </Tooltip>
                                                </div>
                                            </div>
                                        </div>
                                    </motion.div>
                                ))}
                            </div>
                        )}
                    </TooltipProvider>
                </CardContent>
            </Card>
        </motion.div>
    )
}

export default ActivityFeed
