"use client"

import * as React from "react"
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "../ui/card"
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from "../ui/dialog"
import {Button} from "../ui/button"
import {ResponsiveContainer} from "recharts"
import {motion} from "framer-motion"
import {ArrowRight, TrendingUp} from "lucide-react"

export interface ChartCardProps {
    title: string
    description?: string
    children: React.ReactElement
    delay?: number
    className?: string
    onClick?: () => void
    detailContent?: React.ReactNode
    detailTitle?: string
    detailDescription?: string
    actionLabel?: string
}
/**
 * @generated FunctionHeader
 * Function: ChartCard
 * Path: frontend/src/components/dashboard/ChartCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ChartCard: React.FC<ChartCardProps> = ({
                                                 title,
                                                 description,
                                                 children,
                                                 delay = 0,
                                                 className = "",
                                                 onClick,
                                                 detailContent,
                                                 detailTitle,
                                                 detailDescription,
                                                 actionLabel = "Open details",
                                             }) => {
    const [detailOpen, setDetailOpen] = React.useState(false)
    const hasPreview = Boolean(detailContent)
    const isInteractive = Boolean(onClick || hasPreview)
    /**
     * @generated FunctionHeader
     * Function: openCard
     * Path: frontend/src/components/dashboard/ChartCard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCard = () => {
        if (hasPreview) {
            setDetailOpen(true)
            return
        }
        onClick?.()
    }
    /**
     * @generated FunctionHeader
     * Function: runAction
     * Path: frontend/src/components/dashboard/ChartCard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const runAction = () => {
        setDetailOpen(false)
        onClick?.()
    }

    return (
        <>
            <motion.div
                initial={{opacity: 0, y: 20}}
                animate={{opacity: 1, y: 0}}
                transition={{duration: 0.4, delay}}
                whileHover={isInteractive ? {y: -8, scale: 1.005} : {y: -4}}
                whileTap={isInteractive ? {scale: 0.99} : undefined}
                className={`${className} ${
                    isInteractive
                        ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
                        : ""
                }`}
                onClick={isInteractive ? openCard : undefined}
                role={isInteractive ? "button" : "region"}
                aria-label={hasPreview ? `Open ${title} summary` : title}
                tabIndex={isInteractive ? 0 : undefined}
                onKeyDown={(e) => {
                    if (isInteractive && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault()
                        openCard()
                    }
                }}
            >
                <Card
                    className="h-full bg-card border border-border shadow-sm transition-shadow duration-300 hover:shadow-md overflow-hidden group">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-base font-semibold text-foreground tracking-tight">
                                {title}
                            </CardTitle>
                            {isInteractive && (
                                <div
                                    className="p-2 rounded-lg bg-muted text-muted-foreground group-hover:bg-primary group-hover:text-primary-foreground transition-colors duration-300">
                                    <TrendingUp size={16}/>
                                </div>
                            )}
                        </div>
                        {description && (
                            <CardDescription className="text-sm text-muted-foreground mt-1">
                                {description}
                            </CardDescription>
                        )}
                    </CardHeader>
                    <CardContent className="pt-4">
                        <div className="h-[320px] w-full">
                            <ResponsiveContainer width="100%" height="100%" minHeight={300} minWidth={0}>
                                {children}
                            </ResponsiveContainer>
                        </div>
                    </CardContent>
                </Card>
            </motion.div>

            {hasPreview && (
                <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
                    <DialogContent className="sm:max-w-[620px]">
                        <DialogHeader>
                            <DialogTitle className="text-xl font-semibold text-foreground tracking-tight">
                                {detailTitle || title}
                            </DialogTitle>
                            {(detailDescription || description) && (
                                <DialogDescription className="text-sm text-muted-foreground">
                                    {detailDescription || description}
                                </DialogDescription>
                            )}
                        </DialogHeader>
                        <div className="rounded-xl bg-muted border border-border p-5">
                            {detailContent}
                        </div>
                        <div className="flex justify-end gap-3">
                            <Button variant="outline" onClick={() => setDetailOpen(false)}>
                                Close
                            </Button>
                            {onClick && (
                                <Button onClick={runAction} className="gap-2">
                                    {actionLabel}
                                    <ArrowRight size={14}/>
                                </Button>
                            )}
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </>
    )
}

export default ChartCard
