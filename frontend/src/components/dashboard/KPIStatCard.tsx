"use client"

import * as React from "react"
import {Card, CardContent} from "../ui/card"
import {Badge} from "../ui/badge"
import {motion} from "framer-motion"
import {ArrowRight, LucideIcon, TrendingDown, TrendingUp} from "lucide-react"

export interface KPIStatCardProps {
    title: string
    value: string | number
    subtext?: string
    trend?: number
    icon?: LucideIcon
    colorClass?: string
    delay?: number
    onClick?: () => void
}
/**
 * @generated FunctionHeader
 * Function: KPIStatCard
 * Path: frontend/src/components/dashboard/KPIStatCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const KPIStatCard: React.FC<KPIStatCardProps> = ({
                                                     title,
                                                     value,
                                                     subtext,
                                                     trend,
                                                     icon: Icon,
                                                     colorClass,
                                                     delay = 0,
                                                     onClick,
                                                 }) => {
    const trendLabel = trend !== undefined ? (trend > 0 ? "up" : "down") : ""
    const ariaLabel = `${title}: ${value}${
        trend !== undefined ? `, trend ${Math.abs(trend)}% ${trendLabel}` : ""
    }${subtext ? `. ${subtext}` : ""}${onClick ? ". Click to view details." : ""}`

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.4, delay}}
            whileHover={onClick ? {y: -8, scale: 1.01} : {y: -4}}
            whileTap={onClick ? {scale: 0.98} : undefined}
            className={
                onClick
                    ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 rounded-3xl"
                    : ""
            }
            onClick={onClick}
            aria-label={ariaLabel}
            role={onClick ? "button" : "article"}
            tabIndex={onClick ? 0 : undefined}
            onKeyDown={(e) => {
                if (onClick && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault()
                    onClick()
                }
            }}
        >
            <Card className="overflow-hidden border-none card-3d h-full group">
                {/* Animated decorative background gradient */}
                <div
                    className="absolute top-0 right-0 w-40 h-40 bg-gradient-to-br from-primary/5 to-transparent -mr-20 -mt-20 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-700"/>
                <div
                    className="absolute bottom-0 left-0 w-24 h-24 bg-gradient-to-tr from-secondary/5 to-transparent -ml-12 -mb-12 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-700 delay-100"/>

                <CardContent className="p-6 relative z-10">
                    <div className="flex items-start justify-between">
                        <div className="flex-1 min-w-0">
                            <p className="text-[11px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">
                                {title}
                            </p>
                            <h3 className="text-3xl font-black tracking-tight text-slate-900 group-hover:text-primary transition-colors duration-300">
                                {value}
                            </h3>

                            {subtext && (
                                <div className="flex items-center mt-3">
                                    {trend !== undefined && (
                                        <Badge
                                            className={`mr-2 border-none shadow-none font-bold text-[10px] px-1.5 py-0 ${
                                                trend > 0
                                                    ? "bg-emerald-50 text-emerald-600"
                                                    : "bg-rose-50 text-rose-600"
                                            }`}
                                            aria-hidden="true"
                                        >
                                            {trend > 0 ? (
                                                <TrendingUp size={10} className="mr-1"/>
                                            ) : (
                                                <TrendingDown size={10} className="mr-1"/>
                                            )}
                                            {Math.abs(trend)}%
                                        </Badge>
                                    )}
                                    <p className="text-xs font-medium text-slate-500 truncate">
                                        {subtext}
                                    </p>
                                </div>
                            )}
                        </div>

                        <div
                            className={`p-3.5 rounded-2xl shadow-sm transition-all duration-300 group-hover:scale-110 group-hover:rotate-3 ${
                                colorClass || "bg-slate-100 text-slate-600"
                            }`}
                            aria-hidden="true"
                        >
                            {Icon && <Icon size={22} strokeWidth={2.5}/>}
                        </div>
                    </div>

                    {/* Interactive hint for clickable cards */}
                    {onClick && (
                        <div
                            className="mt-4 pt-4 border-t border-slate-50 flex items-center justify-between opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100 transition-opacity duration-300">
              <span className="text-[10px] font-bold text-primary uppercase tracking-wider">
                View Details
              </span>
                            <ArrowRight size={12} className="text-primary"/>
                        </div>
                    )}
                </CardContent>
            </Card>
        </motion.div>
    )
}

export default KPIStatCard
