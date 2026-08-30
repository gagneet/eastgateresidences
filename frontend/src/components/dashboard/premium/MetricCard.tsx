"use client"

import React from "react"
import {Card, CardContent} from "@/components/ui/card"
import {motion} from "framer-motion"
import {LucideIcon} from "lucide-react"
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger} from "@/components/ui/tooltip"
import CountUp from "./CountUp"

interface MetricCardProps {
    title: string
    value: string | number
    subtitle?: string
    icon: LucideIcon
    colorClass?: string
    delay?: number
    onClick?: () => void
    info?: React.ReactNode
    tooltip?: string
}
/**
 * @generated FunctionHeader
 * Function: MetricCard
 * Path: frontend/src/components/dashboard/premium/MetricCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function MetricCard({
                               title,
                               value,
                               subtitle,
                               icon: Icon,
                               colorClass = "bg-indigo-50 text-indigo-600",
                               delay = 0,
                               onClick,
                               info,
                               tooltip
                           }: MetricCardProps) {
    const content = (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.5, delay}}
            whileHover={{y: -5}}
            whileTap={onClick ? {scale: 0.98} : undefined}
            className={(onClick || tooltip) ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 rounded-[1.5rem]" : ""}
            onClick={onClick}
            tabIndex={(onClick || tooltip) ? 0 : undefined}
            role={(onClick || tooltip) ? "button" : undefined}
            onKeyDown={onClick ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onClick();
                }
            } : undefined}
        >
            <Card
                className="transition-all duration-500 shadow-xl hover:shadow-2xl rounded-[1.5rem] border border-white/40 bg-white/60 backdrop-blur-md group overflow-hidden h-full">
                <div
                    className="absolute -bottom-6 -right-6 w-24 h-24 bg-slate-500/5 blur-2xl rounded-full group-hover:bg-indigo-500/10 transition-colors pointer-events-none"/>

                <CardContent className="p-6 flex flex-col h-full">
                    <div className="flex justify-between items-start mb-6">
                        <div className={`p-3 rounded-2xl ${colorClass} shadow-sm`}>
                            <Icon className="w-5 h-5"/>
                        </div>
                        {info && (
                            <div className="relative z-20" onClick={(e) => e.stopPropagation()}>
                                {info}
                            </div>
                        )}
                    </div>

                    <div className="mt-auto">
                        <p className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] mb-1">{title}</p>
                        <p className="text-3xl font-black text-slate-900 tracking-tight tabular-nums leading-none mb-2">
                            {typeof value === 'number' ? <CountUp to={value}/> : value}
                        </p>
                        {subtitle && (
                            <p className="text-xs font-bold text-slate-500 line-clamp-1">{subtitle}</p>
                        )}
                    </div>
                </CardContent>
            </Card>
        </motion.div>
    );

    if (tooltip) {
        return (
            <TooltipProvider>
                <Tooltip>
                    <TooltipTrigger asChild>
                        {content}
                    </TooltipTrigger>
                    <TooltipContent>
                        <p>{tooltip}</p>
                    </TooltipContent>
                </Tooltip>
            </TooltipProvider>
        );
    }

    return content;
}

export default MetricCard;
