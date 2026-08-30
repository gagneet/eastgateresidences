"use client"

import * as React from "react"
import {Card, CardContent, CardHeader, CardTitle} from "../ui/card"
import {motion} from "framer-motion"
import {Calendar, DollarSign, Home, MapPin, TrendingUp} from "lucide-react"

export interface MarketSnapshotData {
    suburb: string
    median_price: number
    growth_yoy: number
    rental_yield: number
    days_on_market: number
}

export interface MarketSnapshotCardProps {
    data?: MarketSnapshotData
    delay?: number
    onClick?: () => void
}
/**
 * @generated FunctionHeader
 * Function: MarketSnapshotCard
 * Path: frontend/src/components/dashboard/MarketSnapshotCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MarketSnapshotCard: React.FC<MarketSnapshotCardProps> = ({
                                                                   data,
                                                                   delay = 0,
                                                                   onClick,
                                                               }) => {
    if (!data) return null

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.4, delay}}
            whileHover={onClick ? {y: -8, scale: 1.01} : {y: -4}}
            className={onClick ? "cursor-pointer" : ""}
            onClick={onClick}
            role={onClick ? "button" : "region"}
            aria-label={`Market Snapshot for ${data.suburb}`}
            tabIndex={onClick ? 0 : undefined}
            onKeyDown={(e) => {
                if (onClick && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault()
                    onClick()
                }
            }}
        >
            <Card
                className="border-none shadow-[0_4px_20px_rgba(0,0,0,0.03)] bg-white overflow-hidden h-full transition-all duration-300 hover:shadow-[0_20px_40px_rgba(0,0,0,0.06)] group">
                <CardHeader className="pb-4 bg-slate-900 text-white relative overflow-hidden">
                    {/* Decorative background circle */}
                    <div
                        className="absolute -right-4 -top-4 w-24 h-24 bg-white/10 rounded-full blur-2xl group-hover:scale-150 transition-transform duration-700"/>

                    <CardTitle
                        className="text-sm font-black flex items-center gap-2 relative z-10 uppercase tracking-widest">
                        <MapPin size={16} className="text-primary-foreground/70"/>
                        Market Pulse: {data.suburb}
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    <div className="grid grid-cols-2 divide-x divide-slate-50 border-b border-slate-50">
                        <div className="p-6 hover:bg-slate-50/50 transition-colors">
                            <div className="flex items-center gap-2 text-slate-400 mb-2">
                                <Home size={14} strokeWidth={2.5}/>
                                <span className="text-[10px] font-black uppercase tracking-tighter">
                  Median Price
                </span>
                            </div>
                            <p className="text-2xl font-black text-slate-900">
                                ${(data.median_price / 1000).toFixed(0)}k
                            </p>
                        </div>

                        <div className="p-6 hover:bg-slate-50/50 transition-colors">
                            <div className="flex items-center gap-2 text-slate-400 mb-2">
                                <TrendingUp size={14} strokeWidth={2.5}/>
                                <span className="text-[10px] font-black uppercase tracking-tighter">
                  Annual Growth
                </span>
                            </div>
                            <p className="text-2xl font-black text-emerald-500">
                                +{data.growth_yoy}%
                            </p>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 divide-x divide-slate-50">
                        <div className="p-6 hover:bg-slate-50/50 transition-colors">
                            <div className="flex items-center gap-2 text-slate-400 mb-2">
                                <DollarSign size={14} strokeWidth={2.5}/>
                                <span className="text-[10px] font-black uppercase tracking-tighter">
                  Rental Yield
                </span>
                            </div>
                            <p className="text-2xl font-black text-slate-900">
                                {data.rental_yield}%
                            </p>
                        </div>

                        <div className="p-6 hover:bg-slate-50/50 transition-colors">
                            <div className="flex items-center gap-2 text-slate-400 mb-2">
                                <Calendar size={14} strokeWidth={2.5}/>
                                <span className="text-[10px] font-black uppercase tracking-tighter">
                  Avg. Days
                </span>
                            </div>
                            <p className="text-2xl font-black text-slate-900">
                                {data.days_on_market}
                            </p>
                        </div>
                    </div>

                    <div
                        className="p-4 bg-slate-50/30 text-[9px] text-center text-slate-400 uppercase tracking-[0.2em] font-black border-t border-slate-50 group-hover:bg-slate-50 transition-colors">
                        Source: Domain / AllHomes Indices
                    </div>
                </CardContent>
            </Card>
        </motion.div>
    )
}

export default MarketSnapshotCard
