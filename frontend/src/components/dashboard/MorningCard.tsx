"use client";
import React, {useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {useAuth} from "../../contexts/AuthContext";

const URGENCY_STYLES: Record<string, string> = {
    critical: "bg-red-50 border-red-300 text-red-900",
    action: "bg-amber-50 border-amber-300 text-amber-900",
    savings: "bg-green-50 border-green-300 text-green-900",
    social: "bg-blue-50 border-blue-300 text-blue-900",
    insight: "bg-gray-50 border-gray-200 text-gray-700",
};

const URGENCY_BTN: Record<string, string> = {
    critical: "bg-red-600 hover:bg-red-700 text-white",
    action: "bg-amber-600 hover:bg-amber-700 text-white",
    savings: "bg-green-600 hover:bg-green-700 text-white",
    social: "bg-blue-600 hover:bg-blue-700 text-white",
    insight: "bg-gray-600 hover:bg-gray-700 text-white",
};

interface MorningCardData {
    urgency: string;
    title: string;
    description: string;
    cta_label: string;
    cta_link: string;
    card_type: string;
}
/**
 * @generated FunctionHeader
 * Function: MorningCard
 * Path: frontend/src/components/dashboard/MorningCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MorningCard() {
    const router = useRouter();
    const {api} = useAuth();
    const [card, setCard] = useState<MorningCardData | null>(null);
    const [dismissed, setDismissed] = useState(false);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const dismissKey = `morning_card_dismissed_${new Date().toDateString()}`;
        if (typeof window !== "undefined" && sessionStorage.getItem(dismissKey)) {
            setDismissed(true);
            setLoading(false);
            return;
        }
        api
            .get("/engagement/morning-card")
            .then((r: any) => {
                setCard(r.data);
                setLoading(false);
            })
            .catch(() => setLoading(false));
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleDismiss
     * Path: frontend/src/components/dashboard/MorningCard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDismiss = () => {
        const dismissKey = `morning_card_dismissed_${new Date().toDateString()}`;
        if (typeof window !== "undefined") sessionStorage.setItem(dismissKey, "1");
        setDismissed(true);
    };

    if (loading || dismissed || !card) return null;

    const urgency = card.urgency || "insight";

    return (
        <div
            className={`relative rounded-xl border-2 p-4 mb-6 flex items-center gap-4 ${URGENCY_STYLES[urgency] || URGENCY_STYLES.insight}`}
        >
            <div className="flex-1 min-w-0">
                <p className="font-semibold text-base">{card.title}</p>
                <p className="text-sm opacity-80 mt-0.5 truncate">{card.description}</p>
            </div>
            <button
                onClick={() => router.push(card.cta_link)}
                className={`shrink-0 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${URGENCY_BTN[urgency] || URGENCY_BTN.insight}`}
            >
                {card.cta_label}
            </button>
            <button
                onClick={handleDismiss}
                className="absolute top-2 right-2 text-current opacity-40 hover:opacity-70 text-lg leading-none"
                aria-label="Dismiss"
            >
                ×
            </button>
        </div>
    );
}