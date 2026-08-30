"use client";
import React, {useEffect, useState} from "react";
import {useAuth} from "../../contexts/AuthContext";

interface PulseEvent {
    emoji: string;
    text: string;
    timestamp: string;
    type: string;
}
/**
 * @generated FunctionHeader
 * Function: relativeTime
 * Path: frontend/src/components/dashboard/BuildingPulseWidget.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function relativeTime(ts: string): string {
    try {
        const diff = Date.now() - new Date(ts).getTime();
        const h = Math.floor(diff / 3600000);
        if (h < 1) return "just now";
        if (h < 24) return `${h}h ago`;
        return `${Math.floor(h / 24)}d ago`;
    } catch {
        return "";
    }
}
/**
 * @generated FunctionHeader
 * Function: BuildingPulseWidget
 * Path: frontend/src/components/dashboard/BuildingPulseWidget.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingPulseWidget() {
    const {api} = useAuth();
    const [events, setEvents] = useState<PulseEvent[]>([]);
    const [loading, setLoading] = useState(true);
    /**
     * @generated FunctionHeader
     * Function: fetchPulse
     * Path: frontend/src/components/dashboard/BuildingPulseWidget.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchPulse = () => {
        api
            .get("/engagement/building-pulse")
            .then((r: any) => {
                setEvents(r.data?.events || []);
                setLoading(false);
            })
            .catch(() => setLoading(false));
    };

    useEffect(() => {
        fetchPulse();
        const interval = setInterval(fetchPulse, 5 * 60 * 1000); // 5 min
        return () => clearInterval(interval);
    }, []);

    if (loading) return null;
    if (events.length === 0) return null;

    return (
        <div className="rounded-xl border border-gray-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-gray-700 mb-3">Building Pulse</h3>
            <div className="space-y-2 max-h-48 overflow-y-auto">
                {events.map((e, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                        <span className="shrink-0">{e.emoji}</span>
                        <span className="flex-1 text-gray-700 truncate">{e.text}</span>
                        <span className="shrink-0 text-gray-400 text-xs">{relativeTime(e.timestamp)}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}