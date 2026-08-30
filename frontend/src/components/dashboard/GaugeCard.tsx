"use client"

import * as React from "react"
import {Card, CardContent, CardHeader, CardTitle} from "../ui/card"
import {Badge} from "../ui/badge"
import {buildStyles, CircularProgressbar} from "react-circular-progressbar"
import "react-circular-progressbar/dist/styles.css"
import {motion} from "framer-motion"

export interface GaugeCardProps {
    title: string
    value: string | number
    percentage: number
    color: string
    subtitle?: string
    delay?: number
    onClick?: () => void
}
/**
 * @generated FunctionHeader
 * Function: GaugeCard
 * Path: frontend/src/components/dashboard/GaugeCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const GaugeCard: React.FC<GaugeCardProps> = ({
                                                 title,
                                                 value,
                                                 percentage,
                                                 color,
                                                 subtitle,
                                                 delay = 0,
                                                 onClick,
                                             }) => {
    return (
        <motion.div
            initial={{opacity: 0, scale: 0.95}}
            animate={{opacity: 1, scale: 1}}
            transition={{duration: 0.4, delay}}
            whileHover={onClick ? {scale: 1.02, y: -4} : {y: -2}}
            className={onClick ? "cursor-pointer" : ""}
            onClick={onClick}
            role={onClick ? "button" : "article"}
            aria-label={`${title}: ${value}${subtitle ? `, ${subtitle}` : ""}`}
            tabIndex={onClick ? 0 : undefined}
            onKeyDown={(e) => {
                if (onClick && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault()
                    onClick()
                }
            }}
        >
            <Card className="h-full border-none card-3d overflow-hidden group">
                {/* Deep background glow */}
                <div
                    className="absolute -inset-20 blur-3xl opacity-0 group-hover:opacity-10 transition-opacity duration-1000"
                    style={{backgroundColor: color}}
                />

                <CardHeader className="pb-2 relative z-10">
                    <CardTitle className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">
                        {title}
                    </CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col items-center justify-center pb-8 relative z-10">
                    <div className="w-36 h-36 mb-6 relative">
                        <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
              <span
                  className="text-3xl font-black text-slate-900 group-hover:scale-110 transition-transform duration-300">
                {value}
              </span>
                        </div>
                        <CircularProgressbar
                            value={percentage}
                            strokeWidth={10}
                            styles={buildStyles({
                                pathColor: color,
                                trailColor: "#F1F5F9",
                                strokeLinecap: "round",
                                pathTransitionDuration: 1.5,
                            })}
                        />
                        {/* 3D Glass overlay on the gauge */}
                        <div
                            className="absolute inset-2 rounded-full border border-white/40 shadow-[0_8px_32px_rgba(0,0,0,0.05)] backdrop-blur-[2px] pointer-events-none"/>
                        <div
                            className="absolute inset-6 rounded-full shadow-[inset_0_2px_10px_rgba(0,0,0,0.05)] pointer-events-none"/>
                    </div>
                    {subtitle && (
                        <Badge
                            variant="outline"
                            className="border-slate-100 text-[10px] font-bold text-slate-500 tracking-tighter px-3 py-0.5 rounded-full bg-slate-50/50"
                        >
                            {subtitle}
                        </Badge>
                    )}
                </CardContent>
            </Card>
        </motion.div>
    )
}

export default GaugeCard
