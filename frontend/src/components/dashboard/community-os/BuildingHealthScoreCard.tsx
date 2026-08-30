"use client";
import React from "react";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {Loader2} from "lucide-react";

interface HealthComponents {
    financial: number;
    maintenance: number;
    compliance: number;
    engagement: number;
    dispute: number;
}

interface BuildingHealthScoreCardProps {
    score?: number;
    grade?: string;
    components?: HealthComponents;
    computedAt?: string;
    loading?: boolean;
}

const RADIUS = 54;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
/**
 * @generated FunctionHeader
 * Function: gradeColor
 * Path: frontend/src/components/community-os/BuildingHealthScoreCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function gradeColor(grade: string): string {
    if (grade === "A") return "bg-green-100 text-green-800";
    if (grade === "B") return "bg-lime-100 text-lime-800";
    if (grade === "C") return "bg-amber-100 text-amber-800";
    return "bg-red-100 text-red-800";
}
/**
 * @generated FunctionHeader
 * Function: barColor
 * Path: frontend/src/components/community-os/BuildingHealthScoreCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function barColor(score: number): string {
    if (score >= 80) return "bg-green-500";
    if (score >= 60) return "bg-amber-400";
    return "bg-red-500";
}

const COMPONENT_LABELS: Record<keyof HealthComponents, string> = {
    financial: "Financial",
    maintenance: "Maintenance",
    compliance: "Compliance",
    engagement: "Engagement",
    dispute: "Dispute Resolution",
};
/**
 * @generated FunctionHeader
 * Function: BuildingHealthScoreCard
 * Path: frontend/src/components/community-os/BuildingHealthScoreCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingHealthScoreCard({
                                                    score = 0,
                                                    grade = "—",
                                                    components,
                                                    computedAt,
                                                    loading = false,
                                                }: BuildingHealthScoreCardProps) {
    const dashOffset = CIRCUMFERENCE - (score / 100) * CIRCUMFERENCE;

    if (loading) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm font-medium">Building Health Score</CardTitle>
                </CardHeader>
                <CardContent className="flex items-center justify-center py-10">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Building Health Score</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center gap-6">
                    <svg width="128" height="128" viewBox="0 0 128 128">
                        <circle cx="64" cy="64" r={RADIUS} fill="none" stroke="#e5e7eb" strokeWidth="10"/>
                        <circle
                            cx="64"
                            cy="64"
                            r={RADIUS}
                            fill="none"
                            stroke={score >= 80 ? "#22c55e" : score >= 60 ? "#f59e0b" : "#ef4444"}
                            strokeWidth="10"
                            strokeLinecap="round"
                            strokeDasharray={CIRCUMFERENCE}
                            strokeDashoffset={dashOffset}
                            transform="rotate(-90 64 64)"
                            style={{transition: "stroke-dashoffset 0.8s ease"}}
                        />
                        <text x="64" y="60" textAnchor="middle" className="text-2xl font-bold" fontSize="26"
                              fontWeight="700" fill="currentColor">
                            {score}
                        </text>
                        <text x="64" y="78" textAnchor="middle" fontSize="11" fill="#6b7280">
                            / 100
                        </text>
                    </svg>
                    <div className="space-y-1">
                        <Badge className={gradeColor(grade)}>Grade {grade}</Badge>
                        {computedAt && (
                            <p className="text-xs text-muted-foreground">
                                Updated {new Date(computedAt).toLocaleDateString("en-AU")}
                            </p>
                        )}
                    </div>
                </div>

                {components && (
                    <div className="space-y-2">
                        {(Object.keys(COMPONENT_LABELS) as Array<keyof HealthComponents>).map((key) => {
                            const val = components[key] ?? 0;
                            return (
                                <div key={key} className="space-y-0.5">
                                    <div className="flex justify-between text-xs">
                                        <span className="text-muted-foreground">{COMPONENT_LABELS[key]}</span>
                                        <span className="font-medium">{val}</span>
                                    </div>
                                    <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                                        <div
                                            className={`h-full rounded-full ${barColor(val)}`}
                                            style={{width: `${val}%`}}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
