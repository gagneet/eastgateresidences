"use client";
import React, {useEffect, useMemo, useState} from 'react';
import Link from 'next/link';
import {usePathname} from 'next/navigation';
import {useNavigation} from '../../contexts/NavigationContext';
import {NavBadges, resolveBadge} from '../../lib/navBadges';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../ui/dropdown-menu';
import {cn} from '../../lib/utils';
import * as LucideIcons from 'lucide-react';
import {
    ChevronDown,
    ChevronRight,
    EyeOff,
    LayoutDashboard,
    MoreVertical,
    Pin,
    PinOff,
    Search,
    Settings2,
    X
} from 'lucide-react';

// ── Semantic search: strata-domain synonym expansion ─────────────────────────
// Defined at module level so it is never recreated across renders.
const SEARCH_SYNONYMS: Record<string, string[]> = {
    pay: ["levies", "finance", "payment"],
    bill: ["levies", "finance"],
    money: ["finance", "levies", "budget"],
    financial: ["finance", "levies"],
    fix: ["maintenance", "repair"],
    repair: ["maintenance"],
    broken: ["maintenance"],
    trade: ["maintenance", "contractors"],
    vote: ["proposals", "voting"],
    ballot: ["proposals"],
    bylaw: ["by-laws", "rules"],
    rules: ["by-laws"],
    regulation: ["by-laws", "compliance"],
    chat: ["community", "messages"],
    message: ["community"],
    mail: ["email"],
    notice: ["notices", "announcements"],
    announcement: ["notices"],
    meeting: ["meetings"],
    agm: ["meetings"],
    doc: ["documents"],
    file: ["documents"],
    upload: ["documents"],
    user: ["users", "residents"],
    resident: ["users"],
    parcel: ["parcels", "delivery"],
    delivery: ["parcels"],
    package: ["parcels"],
    visitor: ["visitors"],
    guest: ["visitors"],
    book: ["bookings"],
    reserve: ["bookings"],
    event: ["events"],
    budget: ["finance"],
    cert: ["certificates"],
    certificate: ["rental-certificates"],
    risk: ["risk-index", "intelligence"],
    audit: ["audit-logs"],
    log: ["audit-logs"],
    toggle: ["feature-toggles"],
    feature: ["feature-toggles"],
    settings: ["settings", "configuration"],
    config: ["settings"],
    savings: ["savings-tracker"],
    volunteer: ["volunteer-credits"],
    invoice: ["invoices"],
    po: ["purchase-orders"],
    work: ["work-orders", "maintenance"],
    insurance: ["insurance"],
    compliance: ["compliance", "safety"],
    safety: ["compliance"],
    report: ["reports", "analytics"],
    market: ["marketplace"],
    listing: ["marketplace"],
    sell: ["marketplace"],
    suburb: ["suburb-radar"],
    passport: ["my-passport"],
    key: ["key-register"],
    move: ["move-in-out"],
};
/**
 * @generated FunctionHeader
 * Function: scoreItem
 * Path: frontend/src/components/layout/Sidebar.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function scoreItem(item: import('../../contexts/NavigationContext').NavItem, query: string): number {
    const q = query.toLowerCase().trim();
    if (!q) return 0;

    const label = item.label.toLowerCase();
    const route = item.route.toLowerCase();
    const hint = (item.discovery_hint ?? "").toLowerCase();

    if (label === q) return 100;
    if (label.startsWith(q)) return 85;
    if (label.includes(q)) return 70;
    if (route.includes(q)) return 55;
    if (hint.includes(q)) return 40;

    const qTokens = q.split(/\s+/);
    const labelTokens = label.split(/\s+/);
    const overlapCount = qTokens.filter(t => labelTokens.some(lt => lt.includes(t))).length;
    if (overlapCount > 0) return 30 + (overlapCount / qTokens.length) * 20;

    const synonymExpansions = qTokens.flatMap(t => SEARCH_SYNONYMS[t] ?? []);
    for (const exp of synonymExpansions) {
        if (label.includes(exp) || route.includes(exp) || hint.includes(exp)) return 25;
    }

    return 0;
}
// Dynamic icon resolution from lucide-react
/**
 * @generated FunctionHeader
 * Function: NavIcon
 * Path: frontend/src/components/layout/Sidebar.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function NavIcon({name, className}: { name: string; className?: string }) {
    const Icon = (LucideIcons as unknown as Record<string, React.ComponentType<{ className?: string }>>)[name];
    if (!Icon) {
        const {LayoutDashboard} = LucideIcons;
        return <LayoutDashboard className={className}/>;
    }
    return <Icon className={className}/>;
}

// Badge severity colour mapping
const BADGE_COLORS = {
    error: "bg-red-500 text-white",
    warning: "bg-amber-500 text-white",
    info: "bg-blue-500 text-white",
    none: "",
};

interface NavItemRowProps {
    item: import('../../contexts/NavigationContext').NavItem;
    badges: NavBadges;
    isActive: boolean;
    isAdvanced?: boolean;
    collapsed?: boolean;
    onPin?: (id: string) => void;
    onUnpin?: (id: string) => void;
    onHide?: (id: string) => void;
    onOpenCustomise?: () => void;
    canPin?: boolean;
    onNavigate?: (featureId: string, route: string) => void;
}
/**
 * @generated FunctionHeader
 * Function: NavItemRow
 * Path: frontend/src/components/layout/Sidebar.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function NavItemRow({
                        item, badges, isActive, isAdvanced = false, collapsed = false,
                        onPin, onUnpin, onHide, canPin = true, onNavigate
                    }: NavItemRowProps) {
    const [showMenu, setShowMenu] = useState(false);
    const badge = resolveBadge(item.badge_source, badges);

    // Active item: blue left border + blue bg tint
    const activeClasses = isActive
        ? "border-l-2 border-blue-500 bg-blue-50 text-blue-700"
        : "border-l-2 border-transparent";

    // Non-active text: advanced items use violet, simple items use strong foreground
    const itemTextColor = isActive
        ? "" // handled by activeClasses
        : isAdvanced
            ? "text-violet-800"
            : "text-slate-900";

    const hoverClasses = isAdvanced
        ? "hover:bg-violet-50"
        : "hover:bg-slate-100";

    // Icon colour
    const iconClass = isActive
        ? "text-blue-600"
        : isAdvanced
            ? "text-violet-600"
            : "text-slate-600";

    return (
        <div
            className={cn("group relative flex items-center", showMenu && "bg-slate-100")}
            onMouseEnter={() => setShowMenu(true)}
            onMouseLeave={() => setShowMenu(false)}
        >
            <Link
                href={item.route}
                onClick={() => onNavigate?.(item.id, item.route)}
                className={cn(
                    "flex flex-1 items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    activeClasses, itemTextColor, hoverClasses
                )}
            >
                <NavIcon
                    name={item.icon}
                    className={cn("h-4 w-4 shrink-0", iconClass)}
                />
                {!collapsed && (
                    <>
                        <span className="flex-1 truncate">{item.label}</span>

                        {/* "New" tag */}
                        {item.isNew && (
                            <span
                                className="ml-1 rounded-full bg-emerald-500 px-1.5 py-0.5 text-[10px] font-semibold text-white leading-none">
                                New
                            </span>
                        )}

                        {/* Badge */}
                        {badge.severity !== "none" && badge.count > 0 && (
                            <span className={cn(
                                "ml-auto rounded-full px-1.5 py-0.5 text-[10px] font-semibold leading-none",
                                BADGE_COLORS[badge.severity]
                            )}>
                                {badge.label ?? badge.count}
                            </span>
                        )}
                    </>
                )}
            </Link>

            {/* Context menu — visible on hover. z-[200] ensures it renders above the fixed sidebar. */}
            {!collapsed && (onPin || onUnpin || onHide) && (
                <div className={cn(
                    "absolute right-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity",
                    showMenu && "opacity-100"
                )}>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                className="rounded p-0.5 hover:bg-slate-200"
                                aria-label={`Menu options for ${item.label}`}
                            >
                                <MoreVertical className="h-3 w-3 text-slate-500"/>
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-36 z-[200]">
                            {onPin && !item.isPinned && canPin && (
                                <DropdownMenuItem onClick={() => onPin(item.id)}>
                                    <Pin className="mr-2 h-3 w-3"/> Pin to simple
                                </DropdownMenuItem>
                            )}
                            {onUnpin && item.isPinned && (
                                <DropdownMenuItem onClick={() => onUnpin(item.id)}>
                                    <PinOff className="mr-2 h-3 w-3"/> Unpin
                                </DropdownMenuItem>
                            )}
                            {onHide && (
                                <DropdownMenuItem onClick={() => onHide(item.id)}>
                                    <EyeOff className="mr-2 h-3 w-3"/> Hide
                                </DropdownMenuItem>
                            )}
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
            )}
        </div>
    );
}

interface SidebarNavProps {
    collapsed?: boolean;
    onOpenCustomise?: () => void;
}
/**
 * @generated FunctionHeader
 * Function: SidebarNav
 * Path: frontend/src/components/layout/Sidebar.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function SidebarNav({collapsed = false, onOpenCustomise}: SidebarNavProps) {
    const {
        mode, simpleItems, advancedItems, pinnedItems, allItems, badges,
        hasUnseenAdvancedFeatures, pinItem, unpinItem, hideItem,
        pendingNudge, dismissNudge, trackNavigation
    } = useNavigation();

    const pathname = usePathname();

    // advancedOpen controls whether the Advanced section is expanded.
    // Initialized from the current mode (NavigationContext defaults `mode` to
    // "simple" synchronously — see NavigationContext.tsx's useState<NavMode>("simple")
    // — so this is correct on the very first render, not just after an effect
    // corrects it a frame later). Menu-simplification Phase 2: Simple mode must
    // not show Advanced items expanded on first paint — see
    // docs/features/capability_consolidation_analysis.md section 3.2/Phase 2.
    // The effect below keeps this in sync when the user explicitly changes mode
    // via the Customise drawer, or when the real saved preference finishes
    // loading asynchronously and differs from the "simple" default.
    const [advancedOpen, setAdvancedOpen] = useState(() => mode !== "simple");
    const [searchQuery, setSearchQuery] = useState("");
    useEffect(() => {
        setAdvancedOpen(mode !== "simple");
        // Clear search when mode changes so the normal view is restored cleanly
        setSearchQuery("");
    }, [mode]);

    // Semantic search: score all available items against the query
    const searchResults = useMemo(() => {
        if (!searchQuery.trim()) return null;
        return allItems
            .map(item => ({item, score: scoreItem(item, searchQuery)}))
            .filter(({score}) => score > 0)
            .sort((a, b) => b.score - a.score)
            .map(({item}) => item);
    }, [searchQuery, allItems]);
    /**
     * @generated FunctionHeader
     * Function: isActive
     * Path: frontend/src/components/layout/Sidebar.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const isActive = (route: string) => {
        // Nav routes may carry a query string for tab deep-links
        // (e.g. "/maintenance?tab=work-orders"); match on the path only,
        // since usePathname() never includes the query string.
        const path = route.split("?")[0];
        if (path === "/dashboard") return pathname === "/dashboard";
        return pathname?.startsWith(path) ?? false;
    };

    // "advanced" mode: all items flat, no section separation
    // "classic" mode: ALL items in one flat list (simple + advanced merged), like old sidebar
    const isAdvancedMode = mode === "advanced";
    const isClassicMode = mode === "classic";

    // Items to show in simple section (pinned first, then simple)
    const pinnedIds = new Set(pinnedItems.map(i => i.id));
    const simpleVisible = [
        ...pinnedItems,
        ...simpleItems.filter(i => !pinnedIds.has(i.id))
    ];

    const canPin = pinnedItems.length < 2;

    // Classic mode: merge simpleVisible + advancedItems into one flat deduped list.
    // Computed directly here from the same variables that power Simple/Advanced modes,
    // so it's guaranteed to be populated whenever those modes work.
    const advancedItemIds = useMemo(() => new Set(advancedItems.map(i => i.id)), [advancedItems]);
    const classicItems = useMemo(() => {
        const seen = new Set<string>();
        const out: import('../../contexts/NavigationContext').NavItem[] = [];
        for (const item of [...simpleVisible, ...advancedItems]) {
            if (!seen.has(item.id)) {
                seen.add(item.id);
                out.push(item);
            }
        }
        return out;
    }, [simpleVisible, advancedItems]); // eslint-disable-line react-hooks/exhaustive-deps

    return (
        <div className="flex flex-col gap-1 px-2">

            {/* ── SEARCH BOX: visible in all modes when sidebar is not collapsed ── */}
            {!collapsed && (
                <div className="relative mb-2 px-1" role="search">
                    <Search
                        className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400 pointer-events-none"
                        aria-hidden="true"
                    />
                    <input
                        type="search"
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        onKeyDown={e => {
                            if (e.key === 'Escape') {
                                setSearchQuery("");
                            }
                        }}
                        placeholder="Search menu…"
                        aria-label="Search navigation menu"
                        autoComplete="off"
                        className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-7 text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-400"
                    />
                    {searchQuery && (
                        <button
                            onClick={() => setSearchQuery("")}
                            aria-label="Clear search"
                            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
                        >
                            <X className="h-3.5 w-3.5"/>
                        </button>
                    )}
                </div>
            )}

            {/* ── SEARCH RESULTS: replaces mode-specific list while query is active ── */}
            {searchResults !== null ? (
                searchResults.length > 0 ? (
                    searchResults.map(item => (
                        <div key={item.id} onClick={() => setSearchQuery("")}>
                            <NavItemRow
                                item={item}
                                badges={badges}
                                isActive={isActive(item.route)}
                                isAdvanced={advancedItemIds.has(item.id)}
                                collapsed={collapsed}
                                onPin={!item.isPinned ? (id) => pinItem(id) : undefined}
                                onUnpin={item.isPinned ? (id) => unpinItem(id) : undefined}
                                onHide={(id) => hideItem(id)}
                                canPin={canPin}
                                onNavigate={trackNavigation}
                            />
                        </div>
                    ))
                ) : (
                    <p className="px-3 py-4 text-center text-xs text-slate-400" role="status">
                        No results for &ldquo;{searchQuery}&rdquo;
                    </p>
                )
            ) : (
                <>
                    {/* ── CLASSIC MODE: flat list of ALL items, no section structure ── */}
                    {isClassicMode ? (
                        <>
                            {classicItems.map(item => (
                                <NavItemRow
                                    key={item.id}
                                    item={item}
                                    badges={badges}
                                    isActive={isActive(item.route)}
                                    isAdvanced={advancedItemIds.has(item.id)}
                                    collapsed={collapsed}
                                    onPin={!item.isPinned ? (id) => pinItem(id) : undefined}
                                    onUnpin={item.isPinned ? (id) => unpinItem(id) : undefined}
                                    onHide={(id) => hideItem(id)}
                                    canPin={canPin}
                                    onNavigate={trackNavigation}
                                />
                            ))}
                        </>
                    ) : (
                        <>
                            {/* Simple section label — only shown in simple mode */}
                            {!isAdvancedMode && simpleVisible.length > 0 && !collapsed && (
                                <p className="px-3 pt-1 pb-0.5 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                                    Simple
                                </p>
                            )}

                            {/* Simple items */}
                            {simpleVisible.map(item => (
                                <NavItemRow
                                    key={item.id}
                                    item={item}
                                    badges={badges}
                                    isActive={isActive(item.route)}
                                    isAdvanced={false}
                                    collapsed={collapsed}
                                    onPin={!item.isPinned ? (id) => pinItem(id) : undefined}
                                    onUnpin={item.isPinned ? (id) => unpinItem(id) : undefined}
                                    onHide={(id) => hideItem(id)}
                                    canPin={canPin}
                                    onNavigate={trackNavigation}
                                />
                            ))}

                            {/* Advanced items */}
                            {advancedItems.length > 0 && (
                                isAdvancedMode ? (
                                    // Advanced mode: no section header, all items shown as flat continuation
                                    advancedItems.map(item => (
                                        <NavItemRow
                                            key={item.id}
                                            item={item}
                                            badges={badges}
                                            isActive={isActive(item.route)}
                                            isAdvanced={false}
                                            collapsed={collapsed}
                                            onPin={(id) => pinItem(id)}
                                            onUnpin={item.isPinned ? (id) => unpinItem(id) : undefined}
                                            canPin={canPin}
                                            onNavigate={trackNavigation}
                                        />
                                    ))
                                ) : (
                                    // Simple mode: collapsible Advanced section with header
                                    <>
                                        <button
                                            onClick={() => setAdvancedOpen(prev => !prev)}
                                            className="mt-3 flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-500 hover:text-slate-700 transition-colors"
                                            aria-expanded={advancedOpen}
                                            aria-controls="advanced-nav-items"
                                        >
                                            {!collapsed && <span className="flex-1 text-left">Advanced</span>}

                                            {hasUnseenAdvancedFeatures && !advancedOpen && (
                                                <span className="relative flex h-2 w-2"
                                                      aria-label="New items available">
                                                    <span
                                                        className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"/>
                                                    <span
                                                        className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"/>
                                                </span>
                                            )}

                                            {advancedOpen ? (
                                                <ChevronDown className="h-3 w-3"/>
                                            ) : (
                                                <ChevronRight className="h-3 w-3"/>
                                            )}
                                        </button>

                                        <div id="advanced-nav-items">
                                            {advancedOpen && advancedItems.map(item => (
                                                <NavItemRow
                                                    key={item.id}
                                                    item={item}
                                                    badges={badges}
                                                    isActive={isActive(item.route)}
                                                    isAdvanced={true}
                                                    collapsed={collapsed}
                                                    onPin={(id) => pinItem(id)}
                                                    onUnpin={item.isPinned ? (id) => unpinItem(id) : undefined}
                                                    canPin={canPin}
                                                    onNavigate={trackNavigation}
                                                />
                                            ))}
                                        </div>
                                    </>
                                )
                            )}
                        </>
                    )}
                </>
            )}

            {/* Customise menu link */}
            {!collapsed && onOpenCustomise && (
                <button
                    onClick={onOpenCustomise}
                    className="mt-3 flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-xs text-slate-400 hover:text-slate-700 transition-colors"
                >
                    <Settings2 className="h-3 w-3"/>
                    Customise menu
                </button>
            )}

            {/* Nudge tooltip */}
            {pendingNudge && !collapsed && pendingNudge.target_route && (
                <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs">
                    <div className="flex items-start justify-between gap-2">
                        <div>
                            <p className="font-medium text-amber-800">
                                {pendingNudge.message || pendingNudge.discovery_hint}
                            </p>
                            {pendingNudge.discovery_hint && pendingNudge.discovery_hint !== pendingNudge.message && (
                                <p className="mt-0.5 text-amber-700">
                                    {pendingNudge.discovery_hint}
                                </p>
                            )}
                        </div>
                        <button
                            onClick={dismissNudge}
                            aria-label="Dismiss suggestion"
                            className="shrink-0 text-amber-500 hover:text-amber-700"
                        >
                            ×
                        </button>
                    </div>
                    <div className="mt-2 flex gap-2">
                        <Link
                            href={pendingNudge.target_route}
                            onClick={() => {
                                trackNavigation(pendingNudge!.feature_id, pendingNudge!.target_route);
                                dismissNudge();
                            }}
                            className="text-amber-700 font-medium hover:underline"
                        >
                            Try it →
                        </Link>
                        <button onClick={dismissNudge} className="text-amber-600 hover:text-amber-800">
                            Not now
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
