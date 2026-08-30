"use client"

import * as React from "react"
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "../ui/card"
import {Badge} from "../ui/badge"
import {AlertCircle, ArrowUpRight, Calendar, DollarSign, TrendingDown, TrendingUp} from "lucide-react"
import {formatCurrency} from "../../lib/utils"
import {motion} from "framer-motion"

export interface FinancialData {
    unit_number: string
    admin_fund: {
        closing_balance: number
    }
    sinking_fund: {
        closing_balance: number
    }
    net_balance: number
    yearly_forecast?: {
        next_due_dates?: string[]
    }
}

export interface FinancialSummaryCardProps {
    financialData?: FinancialData
    onClick?: () => void
    delay?: number
}
/**
 * @generated FunctionHeader
 * Function: FinancialSummaryCard
 * Path: frontend/src/components/dashboard/FinancialSummaryCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FinancialSummaryCard: React.FC<FinancialSummaryCardProps> = ({
                                                                       financialData,
                                                                       onClick,
                                                                       delay = 0,
                                                                   }) => {
    if (!financialData) return null

    const {unit_number, admin_fund, sinking_fund, net_balance} = financialData
    /**
     * @generated FunctionHeader
     * Function: getStatusConfig
     * Path: frontend/src/components/dashboard/FinancialSummaryCard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusConfig = () => {
        if (net_balance < 0) {
            return {
                label: "In Credit",
                color: "text-emerald-600 bg-emerald-50",
                icon: TrendingDown,
            }
        } else if (net_balance === 0) {
            return {
                label: "Paid Up",
                color: "text-blue-600 bg-blue-50",
                icon: TrendingUp,
            }
        } else {
            return {
                label: "Payment Due",
                color: "text-amber-600 bg-amber-50",
                icon: AlertCircle,
            }
        }
    }

    const status = getStatusConfig()
    const StatusIcon = status.icon

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.4, delay}}
            whileHover={{y: -8, scale: 1.01}}
            whileTap={{scale: 0.98}}
            className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 rounded-3xl"
            onClick={onClick}
            role="button"
            aria-label={`Unit ${unit_number} Financial Summary: ${
                net_balance >= 0 ? "Due" : "Credit"
            } ${formatCurrency(Math.abs(net_balance))}`}
            tabIndex={0}
            onKeyDown={(e) => {
                if (onClick && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault()
                    onClick()
                }
            }}
        >
            <Card className="h-full border-none card-3d overflow-hidden group">
                <CardHeader className="pb-4">
                    <div className="flex items-start justify-between">
                        <div className="flex items-center gap-3">
                            <div
                                className="p-2.5 rounded-2xl bg-slate-900 text-white group-hover:scale-110 transition-transform duration-300">
                                <DollarSign size={20} strokeWidth={2.5}/>
                            </div>
                            <div>
                                <CardTitle className="text-xl font-black text-slate-900 tracking-tight">
                                    Unit {unit_number}
                                </CardTitle>
                                <CardDescription
                                    className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-0.5">
                                    Property Intelligence
                                </CardDescription>
                            </div>
                        </div>
                        <Badge
                            className={`${status.color} border-none shadow-sm font-black text-[10px] uppercase tracking-tighter px-3 py-1 rounded-full`}
                        >
                            <StatusIcon size={12} className="mr-1.5"/>
                            {status.label}
                        </Badge>
                    </div>
                </CardHeader>
                <CardContent className="space-y-6 pt-2">
                    <div
                        className="p-6 rounded-3xl bg-slate-900 text-white group-hover:bg-slate-800 transition-all duration-500 relative overflow-hidden shadow-[0_20px_40px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.1)]">
                        {/* Dynamic interactive glow */}
                        <div
                            className="absolute -inset-20 bg-gradient-to-br from-primary/40 to-transparent blur-[80px] opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none"/>

                        <div className="relative z-10">
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-[0.2em] mb-2">
                                Net Position
                            </p>
                            <div className="flex items-baseline gap-2">
                                <h3 className="text-4xl font-black tracking-tight">
                                    {formatCurrency(Math.abs(net_balance))}
                                </h3>
                                {net_balance < 0 && (
                                    <span className="text-sm font-bold text-emerald-400 uppercase tracking-wider">
                    Credit
                  </span>
                                )}
                            </div>
                        </div>
                        <ArrowUpRight
                            className="absolute bottom-6 right-6 text-white/20 group-hover:text-white transition-all duration-300 group-hover:translate-x-1 group-hover:-translate-y-1"
                            size={28}
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-6 px-1">
                        <div className="space-y-1.5">
                            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">
                                Admin Fund
                            </p>
                            <p
                                className={`text-lg font-black tracking-tight ${
                                    (admin_fund?.closing_balance || 0) > 0
                                        ? "text-rose-600"
                                        : "text-emerald-600"
                                }`}
                            >
                                {formatCurrency(Math.abs(admin_fund?.closing_balance || 0))}
                                {(admin_fund?.closing_balance || 0) < 0 && (
                                    <span className="text-[10px] ml-1">CR</span>
                                )}
                            </p>
                        </div>
                        <div className="space-y-1.5">
                            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">
                                Sinking Fund
                            </p>
                            <p
                                className={`text-lg font-black tracking-tight ${
                                    (sinking_fund?.closing_balance || 0) > 0
                                        ? "text-rose-600"
                                        : "text-emerald-600"
                                }`}
                            >
                                {formatCurrency(Math.abs(sinking_fund?.closing_balance || 0))}
                                {(sinking_fund?.closing_balance || 0) < 0 && (
                                    <span className="text-[10px] ml-1">CR</span>
                                )}
                            </p>
                        </div>
                    </div>

                    <div
                        className="pt-4 mt-2 border-t border-slate-50 flex items-center justify-between text-[11px] font-bold">
                        <div className="flex items-center gap-2 text-slate-400">
                            <Calendar size={14} className="text-primary"/>
                            <span>
                Next:{" "}
                                {financialData.yearly_forecast?.next_due_dates?.[0]
                                    ? new Date(
                                        financialData.yearly_forecast.next_due_dates[0]
                                    ).toLocaleDateString()
                                    : "TBA"}
              </span>
                        </div>
                        <div
                            className="flex items-center gap-1 text-primary group-hover:translate-x-1 transition-transform duration-300">
                            Details <ArrowUpRight size={14}/>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </motion.div>
    )
}

export default FinancialSummaryCard
