"use client";
import React, {createContext, useCallback, useContext, useEffect, useRef, useState} from 'react';
import {useAuth} from './AuthContext';
// ── Session ID ────────────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: getOrCreateSessionId
 * Path: frontend/src/contexts/NavigationContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getOrCreateSessionId(): string {
    if (typeof window === "undefined") return "";
    const key = "nav_session_id";
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const id = crypto.randomUUID();
    sessionStorage.setItem(key, id);
    return id;
}

const SESSION_ID = typeof window !== "undefined" ? getOrCreateSessionId() : "";

// ── Types ─────────────────────────────────────────────────────────────────────
export interface NavItem {
    id: string;
    label: string;
    route: string;
    icon: string;
    feature_flag: string;
    permission_flag: string;
    badge_source: string;
    priority: number;
    discovery_hint?: string;
    nudge_trigger?: string;
    isPinned?: boolean;
    isHidden?: boolean;
    isNew?: boolean;
    firstSeenAt?: number | null; // timestamp (ms) of first visit — null = not yet visited
}

export interface NavBadges {
    requests_overdue: number;
    requests_new: number;
    notices_unread: number;
    parcels_waiting: number;
    proposals_open_vote: number;
    approvals_pending: number;
    compliance_overdue: number;
    sla_breached: number;
    levy_due_soon: number;
    tenant_approvals_pending: number;
}

export interface NudgePayload {
    feature_id: string;
    type: "spotlight" | "tooltip" | "coachmark";
    message: string;
    discovery_hint: string;
    target_route: string;
    action: "discover" | "suggest_pin";
}

export type NavMode = "simple" | "advanced" | "classic";

export interface NavigationContextValue {
    mode: NavMode;
    simpleItems: NavItem[];
    advancedItems: NavItem[];
    pinnedItems: NavItem[];
    allItems: NavItem[];        // simple + advanced merged — used in classic mode
    badges: NavBadges;
    pendingNudge: NudgePayload | null;
    isLoading: boolean;
    hasUnseenAdvancedFeatures: boolean;

    toggleMode: () => void;
    setMode: (mode: NavMode) => void;
    pinItem: (itemId: string) => void;
    unpinItem: (itemId: string) => void;
    hideItem: (itemId: string) => void;
    restoreItem: (itemId: string) => void;
    reorderItems: (orderedIds: string[]) => void;
    markFeatureSeen: (featureId: string) => void;
    dismissNudge: () => void;
    trackNavigation: (featureId: string, route: string) => void;
    refreshBadges: () => void;
    setNudgeCooldown: (days: number) => void;
}

const EMPTY_BADGES: NavBadges = {
    requests_overdue: 0, requests_new: 0, notices_unread: 0, parcels_waiting: 0,
    proposals_open_vote: 0, approvals_pending: 0, compliance_overdue: 0,
    sla_breached: 0, levy_due_soon: 0, tenant_approvals_pending: 0,
};

const NEW_TAG_DURATION_MS = 36 * 60 * 60 * 1000; // 36 hours

const NavigationContext = createContext<NavigationContextValue | null>(null);
/**
 * @generated FunctionHeader
 * Function: useNavigation
 * Path: frontend/src/contexts/NavigationContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function useNavigation() {
    const ctx = useContext(NavigationContext);
    if (!ctx) throw new Error("useNavigation must be used inside NavigationProvider");
    return ctx;
}
// ── Helper: add isNew flag to items ──────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: enrichWithNewTag
 * Path: frontend/src/contexts/NavigationContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function enrichWithNewTag(items: NavItem[], visitTimestamps: Record<string, number>): NavItem[] {
    return items.map(item => {
        const ts = visitTimestamps[item.id];
        if (ts === undefined) {
            // Not yet visited — mark as new
            return {...item, isNew: true, firstSeenAt: null};
        }
        // Visited — show "New" for 36h after first visit
        const age = Date.now() - ts;
        return {...item, isNew: age < NEW_TAG_DURATION_MS, firstSeenAt: ts};
    });
}
// ── Provider ──────────────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: NavigationProvider
 * Path: frontend/src/contexts/NavigationContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function NavigationProvider({children}: { children: React.ReactNode }) {
    const {user, api, selectedBuilding, hasFeatureAccess} = useAuth();

    const [mode, setModeState] = useState<NavMode>("simple");
    const [simpleItems, setSimpleItems] = useState<NavItem[]>([]);
    const [advancedItems, setAdvancedItems] = useState<NavItem[]>([]);
    const [pinnedItems, setPinnedItems] = useState<NavItem[]>([]);
    const [hiddenItemIds, setHiddenItemIds] = useState<Set<string>>(new Set());
    const [badges, setBadges] = useState<NavBadges>(EMPTY_BADGES);
    const [pendingNudge, setPendingNudge] = useState<NudgePayload | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [hasUnseenAdvancedFeatures, setHasUnseenAdvancedFeatures] = useState(false);

    // Local preference state (pending -> debounced -> sent)
    const [pendingPrefs, setPendingPrefs] = useState<Record<string, unknown> | null>(null);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Visit timestamps: { featureId: timestamp_ms }
    // Stored in localStorage key: nav_visits_{userId}_{buildingId}
    const [visitTimestamps, setVisitTimestamps] = useState<Record<string, number>>({});

    const buildingId = selectedBuilding?.id ?? "";
    const userId = user?.id ?? "";

    // ── localStorage keys ────────────────────────────────────────────────────
    const modeKey = userId && buildingId ? `nav_mode_${userId}_${buildingId}` : null;
    const visitsKey = userId && buildingId ? `nav_visits_${userId}_${buildingId}` : null;

    // ── Load visit timestamps from localStorage ───────────────────────────────
    useEffect(() => {
        if (!visitsKey) return;
        try {
            const raw = localStorage.getItem(visitsKey);
            setVisitTimestamps(raw ? JSON.parse(raw) : {});
        } catch {
            setVisitTimestamps({});
        }
    }, [visitsKey]);

    // ── Fetch nav config ──────────────────────────────────────────────────────
    const fetchNavConfig = useCallback(async () => {
        if (!user || !buildingId || !api) return;
        try {
            setIsLoading(true);
            const res = await api.get('/navigation/config');
            const data = res.data;
            const customisationEnabled = hasFeatureAccess('nav_customisation');
            const discoveryEnabled = hasFeatureAccess('nav_discovery_nudges');

            // Load mode from localStorage (prefer saved over server)
            if (!customisationEnabled) {
                setModeState("classic");
            } else if (modeKey) {
                const saved = localStorage.getItem(modeKey);
                const validModes: NavMode[] = ["simple", "advanced", "classic"];
                const savedMode = saved && validModes.includes(saved as NavMode) ? saved as NavMode : null;
                setModeState(savedMode || (data.mode as NavMode) || "simple");
            } else {
                setModeState(data.mode || "simple");
            }

            // Get current timestamps for isNew enrichment
            let timestamps: Record<string, number> = {};
            if (visitsKey) {
                try {
                    const raw = localStorage.getItem(visitsKey);
                    timestamps = raw ? JSON.parse(raw) : {};
                } catch {
                    timestamps = {};
                }
            }

            const simple = enrichWithNewTag(data.simple_items || [], timestamps);
            const advanced = enrichWithNewTag(data.advanced_items || [], timestamps);
            const pinned = enrichWithNewTag(data.pinned_items || [], timestamps);

            // Restore hidden IDs from server prefs
            const serverHidden: string[] = data.hidden_item_ids || [];
            setHiddenItemIds(new Set(serverHidden));

            setSimpleItems(simple);
            setAdvancedItems(advanced);
            setPinnedItems(pinned);
            setHasUnseenAdvancedFeatures(customisationEnabled && discoveryEnabled && (data.has_unseen_advanced_features ?? false));

            if (customisationEnabled && discoveryEnabled && data.nudge) {
                setPendingNudge(data.nudge);
            } else {
                setPendingNudge(null);
            }
        } catch {
            // Silently fail — navigation degrades gracefully
        } finally {
            setIsLoading(false);
        }
    }, [user?.id, buildingId, api, modeKey, visitsKey, hasFeatureAccess]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        void fetchNavConfig();
    }, [fetchNavConfig]);

    // ── Fetch badges every 5 minutes ──────────────────────────────────────────
    const fetchBadges = useCallback(async () => {
        if (!user || !buildingId || !api) return;
        try {
            const res = await api.get('/navigation/badges');
            setBadges(res.data || EMPTY_BADGES);
        } catch {
            // keep last badges
        }
    }, [user?.id, buildingId, api]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        void fetchBadges();
        const interval = setInterval(() => void fetchBadges(), 5 * 60 * 1000);
        return () => clearInterval(interval);
    }, [fetchBadges]);

    // ── Debounced preference patch ────────────────────────────────────────────
    const patchPreferences = useCallback(async (prefs: Record<string, unknown>) => {
        if (!api || !buildingId) return;
        try {
            await api.patch('/navigation/preferences', prefs);
        } catch {
            // silently fail
        }
    }, [api, buildingId]);

    useEffect(() => {
        if (!pendingPrefs) return;
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => {
            void patchPreferences(pendingPrefs);
            setPendingPrefs(null);
        }, 800);
        return () => {
            if (debounceRef.current) clearTimeout(debounceRef.current);
        };
    }, [pendingPrefs, patchPreferences]);
    /**
     * @generated FunctionHeader
     * Function: schedulePrefUpdate
     * Path: frontend/src/contexts/NavigationContext.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const schedulePrefUpdate = (update: Record<string, unknown>) => {
        setPendingPrefs(prev => ({...(prev || {}), ...update}));
    };

    // ── Mode toggle ───────────────────────────────────────────────────────────
    const toggleMode = useCallback(() => {
        if (!hasFeatureAccess('nav_customisation')) {
            setModeState("classic");
            return;
        }
        setModeState(prev => {
            // Cycle: simple → advanced → classic → simple
            const next: NavMode = prev === "simple" ? "advanced" : prev === "advanced" ? "classic" : "simple";
            if (modeKey) localStorage.setItem(modeKey, next);
            schedulePrefUpdate({preferred_mode: next});
            return next;
        });
    }, [modeKey, hasFeatureAccess]); // eslint-disable-line react-hooks/exhaustive-deps

    const setMode = useCallback((m: NavMode) => {
        if (!hasFeatureAccess('nav_customisation')) {
            setModeState("classic");
            return;
        }
        setModeState(m);
        if (modeKey) localStorage.setItem(modeKey, m);
        schedulePrefUpdate({preferred_mode: m});
    }, [modeKey, hasFeatureAccess]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Pin / Unpin ───────────────────────────────────────────────────────────
    const pinItem = useCallback((itemId: string) => {
        if (pinnedItems.length >= 2) {
            return; // caller should show toast
        }
        const item = advancedItems.find(i => i.id === itemId);
        if (!item) return;
        const newPinned = [...pinnedItems, {...item, isPinned: true}];
        setPinnedItems(newPinned);
        schedulePrefUpdate({pinned_items: newPinned.map(i => i.id)});
    }, [pinnedItems, advancedItems]); // eslint-disable-line react-hooks/exhaustive-deps

    const unpinItem = useCallback((itemId: string) => {
        const newPinned = pinnedItems.filter(i => i.id !== itemId);
        setPinnedItems(newPinned);
        schedulePrefUpdate({pinned_items: newPinned.map(i => i.id)});
    }, [pinnedItems]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Hide / Restore ────────────────────────────────────────────────────────
    const hideItem = useCallback((itemId: string) => {
        setHiddenItemIds(prev => {
            const next = new Set(prev);
            next.add(itemId);
            schedulePrefUpdate({hidden_items: [...next]});
            return next;
        });
        setSimpleItems(prev => prev.filter(i => i.id !== itemId));
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const restoreItem = useCallback((itemId: string) => {
        setHiddenItemIds(prev => {
            const next = new Set(prev);
            next.delete(itemId);
            schedulePrefUpdate({hidden_items: [...next]});
            return next;
        });
        // Refetch so the restored item appears with correct position
        void fetchNavConfig();
    }, [fetchNavConfig]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Reorder ───────────────────────────────────────────────────────────────
    const reorderItems = useCallback((orderedIds: string[]) => {
        // Empty array means "clear custom order" — refetch with role defaults
        if (!orderedIds || orderedIds.length === 0) {
            schedulePrefUpdate({custom_order: [], pinned_items: [], hidden_items: []});
            void fetchNavConfig();
            return;
        }
        setSimpleItems(prev => {
            const map = Object.fromEntries(prev.map(i => [i.id, i]));
            return orderedIds.map(id => map[id]).filter(Boolean) as NavItem[];
        });
        schedulePrefUpdate({custom_order: orderedIds});
    }, [fetchNavConfig]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Mark feature seen ─────────────────────────────────────────────────────
    const markFeatureSeen = useCallback((featureId: string) => {
        // Record first-visit timestamp in localStorage
        if (visitsKey) {
            try {
                const raw = localStorage.getItem(visitsKey);
                const timestamps: Record<string, number> = raw ? JSON.parse(raw) : {};
                if (!timestamps[featureId]) {
                    timestamps[featureId] = Date.now();
                    localStorage.setItem(visitsKey, JSON.stringify(timestamps));
                    setVisitTimestamps(timestamps);

                    // Re-enrich items with new timestamps
                    setSimpleItems(prev => enrichWithNewTag(prev, timestamps));
                    setAdvancedItems(prev => enrichWithNewTag(prev, timestamps));
                    setPinnedItems(prev => enrichWithNewTag(prev, timestamps));
                }
            } catch {
                // ignore
            }
        }
        // Also tell the server
        schedulePrefUpdate({features_seen: {[featureId]: true}});
    }, [visitsKey]); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Dismiss nudge ─────────────────────────────────────────────────────────
    const dismissNudge = useCallback(() => {
        setPendingNudge(null);
        schedulePrefUpdate({last_nudge_dismissed_at: new Date().toISOString()});
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // ── Track navigation ──────────────────────────────────────────────────────
    const trackNavigation = useCallback((featureId: string, route: string) => {
        if (!api || !buildingId) return;
        // Fire and forget
        void api.post('/navigation/track', {
            feature_id: featureId,
            route,
            event_type: "page_view",
            session_id: SESSION_ID,
        }).catch(() => {
        });
        // Also mark as seen locally
        markFeatureSeen(featureId);
    }, [api, buildingId, markFeatureSeen]);

    // ── Refresh badges ────────────────────────────────────────────────────────
    const refreshBadges = useCallback(() => {
        void fetchBadges();
    }, [fetchBadges]);

    // ── Set nudge cooldown ────────────────────────────────────────────────────
    const setNudgeCooldown = useCallback((days: number) => {
        schedulePrefUpdate({nudge_cooldown_days: days});
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // allItems = simple (with pinned) + advanced, deduped — for classic flat view
    const allItems: NavItem[] = React.useMemo(() => {
        const seen = new Set<string>();
        const combined: NavItem[] = [];
        for (const item of [...simpleItems, ...advancedItems]) {
            if (!seen.has(item.id)) {
                seen.add(item.id);
                combined.push(item);
            }
        }
        return combined;
    }, [simpleItems, advancedItems]);

    return (
        <NavigationContext.Provider value={{
            mode,
            simpleItems,
            advancedItems,
            pinnedItems,
            allItems,
            badges,
            pendingNudge,
            isLoading,
            hasUnseenAdvancedFeatures,
            toggleMode,
            setMode,
            pinItem,
            unpinItem,
            hideItem,
            restoreItem,
            reorderItems,
            markFeatureSeen,
            dismissNudge,
            trackNavigation,
            refreshBadges,
            setNudgeCooldown,
        }}>
            {children}
        </NavigationContext.Provider>
    );
}
