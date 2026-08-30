// @featuretrace:user-management — Client-side auth state: JWT decode, building-switch loop guard, role helpers, feature-toggle access map.
// Layer: frontend
// Data flow: AuthProvider → GET /auth/me → GET /buildings/me → POST /auth/switch-building (when JWT lacks building_id) → JWT (building-scoped).
// Note: /auth/memberships is NOT called here; /buildings/me is the source of the building list.
// @featuretrace:multi-unit-ownership — owns selectedUnit/availableUnits/switchUnit for multi-unit owners.
// Layer: frontend
// Data flow: switchUnit() → POST /auth/switch-unit → re-issued JWT + selectedUnit state → useActiveUnit()
//            → owner-facing page fetches (building-scoped).
// Related: backend/routers/auth.py
//          frontend/src/hooks/useActiveUnit.ts
//          frontend/src/components/layout/UnitSwitcher.tsx
//           frontend/src/auth.ts
//           tests/frontend/unit/contexts/AuthContext.switchBuilding.test.tsx
// Toggle: (controls are inside auth; no single toggle gates this file)
"use client";
import React, {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react';
import axios, {AxiosInstance} from 'axios';
import {SessionProvider, signIn as nextAuthSignIn, signOut as nextAuthSignOut, useSession} from 'next-auth/react';
import {toast} from 'sonner';
import {classifyApiError, deniedSurface} from '@/lib/api-error';
import {DEFAULT_CURRENCY, setActiveCurrency} from '../lib/currency';

export interface User {
    id: string;
    email: string;
    full_name: string;
    role: string;
    tenant_id?: string;
    unit_number?: string;
    unit_type?: string;
    phone?: string;
    phone_home?: string;
    phone_mobile?: string;
    phone_business?: string;
    home_address?: string;
    home_suburb?: string;
    home_state?: string;
    home_postcode?: string;
    postal_same_as_home?: boolean;
    postal_address?: string;
    postal_suburb?: string;
    postal_state?: string;
    postal_postcode?: string;
    is_managing_agent?: boolean;
    is_tenanted?: boolean;
    general_correspondence_email?: boolean;
    general_correspondence_post?: boolean;
    levy_notices_email?: boolean;
    levy_notices_post?: boolean;
    meeting_notices_email?: boolean;
    meeting_notices_post?: boolean;
    strata_roll_consent?: boolean;
    profile_image?: string;
    is_approved: boolean;
    is_elevated?: boolean;
    temp_elevation?: {
        role: string;
        elevated_by: string;
        elevated_at: string;
        expires_at: string;
        duration_days: number;
    } | null;
    permissions?: Record<string, boolean>;
    ec_position?: string;  // CHAIRMAN | TREASURER | SECRETARY | MEMBER
    created_at: string;
    last_login_at?: string;
    last_login_ip?: string;
    // Split by migration 0094. Either may be absent, and absence is meaningful:
    // "no public address was established for this login".
    last_login_public_ip?: string;
    last_login_local_ip?: string;
    co_owner_name?: string;
    co_owner_email?: string;
    primary_email?: string;
    secondary_email?: string;
    unit_owner_name?: string;
    owned_units?: string[];
}

export interface Building {
    id: string;
    name: string;
    address: string;
    building_id: string;
    /** ISO-4217 code from GET /buildings/me, e.g. "AUD". Currency is a per-building
     *  setting: a super_admin switching between an Australian and a New Zealand
     *  scheme must see each one's own money. Absent on a building cached before
     *  this field existed — the formatter falls back to the AUD default. */
    currency_code?: string;
    /** BCP-47 locale that pairs with `currency_code`; drives symbol placement and
     *  grouping/decimal separators. */
    currency_locale?: string;
}

export interface Notification {
    id: string;
    title: string;
    message: string;
    is_read: boolean;
    created_at: string;
    link?: string;
}

export interface AuthContextValue {
    user: User | null;
    token: string | null;
    loading: boolean;
    featureAccess: Record<string, boolean>;
    notifications: Notification[];
    unreadCount: number;
    pendingApprovalsCount: number;
    setPendingApprovalsCount: React.Dispatch<React.SetStateAction<number>>;
    selectedYear: string | null;
    setSelectedYear: (year: string) => void;
    availableYears: string[];
    financialYearStartMonth: number;
    // Multi-tenancy — buildings
    selectedBuilding: Building | null;
    availableBuildings: Building[];
    switchBuilding: (buildingId: string, options?: { redirectTo?: string | false }) => Promise<void>;
    // Multi-unit — owners with more than one unit in the same building
    selectedUnit: string | null;
    availableUnits: string[];
    switchUnit: (unitNumber: string) => Promise<void>;
    addUnit: (unitNumber: string) => Promise<{ owned_units: string[] }>;
    login: (email: string, password: string) => Promise<any>;
    register: (userData: any, buildingId?: string) => Promise<User>;
    logout: () => void;
    updateProfile: (updateData: any) => Promise<User | undefined>;
    updateEmailPreference: (primaryEmail: string) => Promise<User | undefined>;
    hasPermission: (permission: string) => boolean;
    isAdmin: () => boolean;
    isRealAdmin: () => boolean;
    isManager: () => boolean;
    isECMember: () => boolean;
    isStrataAdmin: () => boolean;
    isOwner: () => boolean;
    isTenant: () => boolean;
    isGuest: () => boolean;
    hasFeatureAccess: (featureKey: string) => boolean;
    isImpersonating: boolean;
    impersonate: (userId: string, buildingId?: string) => Promise<boolean>;
    exitImpersonation: () => Promise<void>;
    api: AxiosInstance;
    fetchNotifications: () => Promise<void>;
    markNotificationRead: (notifId: string) => Promise<void>;
    markAllRead: () => Promise<void>;
    fetchPendingApprovalsCount: (force?: boolean) => Promise<any>;
    isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const API_URL = `${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/api`;
const DEFAULT_BUILDING_ID = process.env.NEXT_PUBLIC_DEFAULT_BUILDING_ID || '13195';
/**
 * @generated FunctionHeader
 * Function: AuthProvider
 * Path: frontend/src/contexts/AuthContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const AuthProvider: React.FC<{ children: React.ReactNode, session?: any }> = ({
                                                                                         children,
                                                                                         session: initialSession
                                                                                     }) => {
    return (
        <SessionProvider session={initialSession}>
            <InnerAuthProvider>{children}</InnerAuthProvider>
        </SessionProvider>
    );
};
/**
 * @generated FunctionHeader
 * Function: InnerAuthProvider
 * Path: frontend/src/contexts/AuthContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InnerAuthProvider: React.FC<{ children: React.ReactNode }> = ({children}) => {
    const {data: session, status} = useSession();
    const [user, setUser] = useState<User | null>(null);
    const [token, setToken] = useState<string | null>(null);
    const [adminToken, setAdminToken] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [featureAccess, setFeatureAccess] = useState<Record<string, boolean>>({});
    const [notifications, setNotifications] = useState<Notification[]>([]);
    const [unreadCount, setUnreadCount] = useState(0);
    const [pendingApprovalsCount, setPendingApprovalsCount] = useState(0);

    // Multi-tenancy State
    const [availableBuildings, setAvailableBuildings] = useState<Building[]>([]);
    const [selectedBuilding, setSelectedBuilding] = useState<Building | null>(null);

    // Keep the money formatter in step with the building in context.
    //
    // Done as ONE effect on `selectedBuilding` rather than at each of the seven
    // setSelectedBuilding() call sites: those include localStorage rehydration on
    // boot, the post-login default, a refresh path and the explicit switcher, and a
    // currency that is only updated on some of them is worse than none — it would
    // render one building's money in another's currency after a switch.
    //
    // A building cached before currency_code existed passes undefined, which
    // setActiveCurrency resolves to the AUD default.
    useEffect(() => {
        setActiveCurrency(
            selectedBuilding?.currency_code
                ? {code: selectedBuilding.currency_code, locale: selectedBuilding.currency_locale}
                : DEFAULT_CURRENCY,
        );
    }, [selectedBuilding?.currency_code, selectedBuilding?.currency_locale]);

    // Multi-unit State — owners with multiple units in the same building
    const [selectedUnit, setSelectedUnit] = useState<string | null>(null);
    const [availableUnits, setAvailableUnits] = useState<string[]>([]);

    // Year Selector State
    const [selectedYear, setSelectedYearState] = useState<string | null>(null);
    const [availableYears, setAvailableYears] = useState<string[]>([]);
    const [financialYearStartMonth, setFinancialYearStartMonth] = useState(1);
    const savedYearRef = useRef<string | null>(null);

    const logoutRef = useRef<(() => void) | null>(null);
    const tokenRef = useRef<string | null>(null);
    const sessionStatusRef = useRef(status);
    const lastApprovalsFetchRef = useRef(0);
    const approvalsPromiseRef = useRef<Promise<any> | null>(null);

    // Bootstrap client-only storage values after mount.
    // Initialising these in useState(() => localStorage...) causes a server/client
    // mismatch (server has no window → null; client reads real value → #418 hydration error).
    // Reading here instead ensures the initial render is identical on server and client.
    useEffect(() => {
        try {
            const savedBuilding = localStorage.getItem('selectedBuilding');
            if (savedBuilding) {
                setSelectedBuilding(JSON.parse(savedBuilding));
            } else {
                setSelectedBuilding({id: DEFAULT_BUILDING_ID, name: '', address: '', building_id: DEFAULT_BUILDING_ID});
            }
        } catch (e) {
        }
        try {
            const saved = localStorage.getItem('selectedYear');
            savedYearRef.current = saved;
            setSelectedYearState(saved || String(new Date().getFullYear()));
        } catch (e) {
            setSelectedYearState(String(new Date().getFullYear()));
        }
        try {
            const savedAdminToken = sessionStorage.getItem('admin_token');
            if (savedAdminToken) setAdminToken(savedAdminToken);
        } catch (e) {
        }
    }, []);

    // Keep sessionStatusRef in sync so closures can check current status
    useEffect(() => {
        sessionStatusRef.current = status;
    }, [status]);

    // Memoize API instance to prevent recreation on every render
    const api = useMemo(() => {
        const instance = axios.create({
            baseURL: API_URL,
            headers: {
                'Content-Type': 'application/json',
            },
        });

        // Add token to requests
        instance.interceptors.request.use((config) => {
            const currentToken = tokenRef.current;
            if (currentToken) {
                config.headers.Authorization = `Bearer ${currentToken}`;
            }

            // Inject building context for all requests
            const savedBuilding = typeof window !== 'undefined' ? localStorage.getItem('selectedBuilding') : null;
            if (savedBuilding) {
                try {
                    const b = JSON.parse(savedBuilding);
                    // b.id is the Postgres core.schemes UUID; b.building_id is the legacy plan
                    // number (e.g. "13195") every Mongo tenant-scoped collection actually keys
                    // on. Sending the UUID here made super_admin's X-Building-ID override
                    // resolve to the wrong building_id downstream — see get_current_building()'s
                    // legacy path in backend/utils/auth.py.
                    config.headers['X-Building-ID'] = b.building_id || b.id;
                } catch (e) {
                }
            } else if (!config.headers['X-Building-ID']) {
                // Only apply default fallback if the caller hasn't already set a building header
                // (e.g. the public registration page passes the user's selected building)
                config.headers['X-Building-ID'] = DEFAULT_BUILDING_ID;
            }

            return config;
        });

        // Handle token expiry and rate limiting
        instance.interceptors.response.use(
            (response) => response,
            (error) => {
                if (error.response?.status === 401 && logoutRef.current) {
                    logoutRef.current();
                }
                if (error.response?.status === 429) {
                    const retryAfter = error.response.headers?.['retry-after'];
                    const msg = retryAfter
                        ? `Too many attempts. Please wait ${retryAfter} seconds and try again.`
                        : 'Too many attempts. Please wait a moment and try again.';
                    error.rateLimitMessage = msg;
                }
                error.strataError = classifyApiError(error);

                // Surface a function-scope refusal globally.
                //
                // This is the ONLY error we toast from here, and it is deliberate.
                // Every other failure is a page's own to render. This one is not
                // reachable that way: the backend names the exact surface it
                // refused, but none of the nine routers this gates has a page that
                // renders a classified error - they all fall back to their own
                // "failed to load", so the reason was reaching the browser and
                // dying there. A narrowed levies manager saw a blank insurance page
                // and no way to learn why.
                //
                // Toasting from the interceptor reaches all of them without editing
                // ~40 pages, and keeps working for pages not written yet. When a
                // page does render `error.strataError` properly it will show the
                // same message inline; the toast is additive, not a replacement.
                //
                // Deduped by surface: one page load can fire several parallel
                // requests into the same denied router, and five identical toasts
                // is worse than none. sonner replaces a toast that reuses its id.
                if (error.strataError?.category === 'function_scope_denied') {
                    const surface = deniedSurface(error) || 'this area';
                    toast.error(error.strataError.message, {
                        id: `manager-function-scope:${surface}`,
                        description: error.strataError.suggestedAction,
                    });
                }

                return Promise.reject(error);
            }
        );

        return instance;
    }, []); // Empty dependency array - create once

    const logout = useCallback(() => {
        sessionStorage.removeItem('admin_token');
        localStorage.removeItem('selectedYear');
        localStorage.removeItem('selectedBuilding');
        setToken(null);
        setAdminToken(null);
        setUser(null);
        setFeatureAccess({});
        setNotifications([]);
        setUnreadCount(0);
        setPendingApprovalsCount(0);
        setSelectedYearState(null);
        setSelectedBuilding(null);
        setAvailableBuildings([]);
    }, []);

    // Store logout in ref for interceptor access
    useEffect(() => {
        logoutRef.current = logout;
    }, [logout]);

    const fetchNotifications = useCallback(async () => {
        if (!tokenRef.current) return;
        try {
            const countRes = await api.get('/notifications/unread-count');
            setUnreadCount(countRes.data.unread_count);

            // Fetch latest 10 notifications (read and unread) for the dropdown
            const notifRes = await api.get('/notifications/history?limit=10');
            setNotifications(notifRes.data);
        } catch (error) {
            console.error('Failed to fetch notifications:', error);
        }
    }, [api]);

    const markNotificationRead = useCallback(async (notifId: string) => {
        try {
            await api.put(`/notifications/${notifId}/read`);
            setNotifications(prev => prev.map(n => n.id === notifId ? {...n, is_read: true} : n));
            setUnreadCount(prev => Math.max(0, prev - 1));
        } catch (error) {
            console.error('Failed to mark notification as read:', error);
        }
    }, [api]);

    const markAllRead = useCallback(async () => {
        try {
            await api.put('/notifications/read-all');
            setNotifications(prev => prev.map(n => ({...n, is_read: true})));
            setUnreadCount(0);
        } catch (error) {
            console.error('Failed to mark all notifications as read:', error);
        }
    }, [api]);

    const setSelectedYear = useCallback((year: string) => {
        setSelectedYearState(year);
        if (typeof window !== 'undefined') {
            try {
                localStorage.setItem('selectedYear', year);
            } catch (e) {
                toast.error('Failed to save year selection. Your choice may not be sticky.');
            }
        }
    }, []);

    const fetchUser = useCallback(async (tokenToUse?: string) => {
        const activeToken = tokenToUse || tokenRef.current;
        if (!activeToken) {
            if (sessionStatusRef.current !== 'loading') {
                setLoading(false);
            }
            return;
        }

        try {
            // Fetch user data
            const response = await api.get('/auth/me', {
                headers: tokenToUse ? {Authorization: `Bearer ${tokenToUse}`} : {}
            });
            const userData = response.data;
            setUser(userData);

            // Seed multi-unit state from the user profile
            if (userData?.owned_units?.length) {
                setAvailableUnits(userData.owned_units);
                setSelectedUnit(prev => prev ?? userData.unit_number ?? null);
            }

            // Fetch available buildings for this user
            const buildingsRes = await api.get('/buildings/me');
            const buildings = buildingsRes.data;
            setAvailableBuildings(buildings);

            // If no building selected, or current one not in list, handle redirect/auto-select
            if (buildings.length > 0) {
                // selectedBuilding state may still be null when fetchUser runs (bootstrap useEffect
                // fires after the session effect). Read localStorage directly as fallback — same
                // pattern the axios interceptor uses — so the saved building is found even on the
                // first render before the bootstrap sets state.
                let currentId = selectedBuilding?.id ?? null;
                // building_id is the human-readable plan number (e.g. "13195") stored in JWTs;
                // id is the scheme UUID used for UI lookups — they must be compared separately.
                let currentBuildingId: string | null = selectedBuilding?.building_id ?? null;
                if (!currentId && typeof window !== 'undefined') {
                    try {
                        const saved = localStorage.getItem('selectedBuilding');
                        if (saved) {
                            const parsed = JSON.parse(saved);
                            currentId = parsed.id ?? null;
                            currentBuildingId = parsed.building_id ?? null;
                        }
                    } catch { /* ignore */
                    }
                }
                const exists = buildings.find((b: Building) => b.id === currentId);

                // Check whether the current JWT already carries the right building_id claim
                const currentToken = tokenToUse || tokenRef.current;
                const currentPayload = currentToken ? (() => {
                    try {
                        return JSON.parse(atob(currentToken.split('.')[1]));
                    } catch {
                        return {};
                    }
                })() : {};
                const jwtBuildingId: string | null = currentPayload.building_id || null;

                if (!exists) {
                    if (buildings.length === 1) {
                        // Auto-select and get scoped JWT — avoids extra /select-building round-trip
                        const defaultBuilding = buildings[0];
                        setSelectedBuilding(defaultBuilding);
                        localStorage.setItem('selectedBuilding', JSON.stringify(defaultBuilding));

                        // Only switch if the current token doesn't already carry this building_id.
                        // JWT carries building_id = plan number (e.g. "13195"), not the UUID.
                        if (!jwtBuildingId || jwtBuildingId !== defaultBuilding.building_id) {
                            try {
                                const switchRes = await api.post('/auth/switch-building', {building_id: defaultBuilding.id}, {
                                    headers: tokenToUse ? {Authorization: `Bearer ${tokenToUse}`} : {}
                                });
                                const {token: scopedToken} = switchRes.data;
                                if (scopedToken) {
                                    tokenRef.current = scopedToken;
                                    setToken(scopedToken);
                                }
                            } catch (switchErr: any) {
                                // 404 → stale building reference (localStorage carries an id no longer in core.schemes,
                                // typically post-cutover). Clear and force re-selection so the JWT can be scoped.
                                if (switchErr?.response?.status === 404) {
                                    try {
                                        localStorage.removeItem('selectedBuilding');
                                    } catch { /* ignore */
                                    }
                                    if (typeof window !== 'undefined' && !window.location.pathname.includes('/select-building')) {
                                        try {
                                            sessionStorage.setItem('staleBuildingRedirect', '1');
                                        } catch { /* ignore */
                                        }
                                        window.location.href = '/select-building';
                                        return;
                                    }
                                }
                                console.warn('Auto building switch failed:', switchErr);
                            }
                        }
                    } else {
                        // More than one building and none selected -> go to selection page
                        if (typeof window !== 'undefined' && !window.location.pathname.includes('/select-building')) {
                            window.location.href = '/select-building';
                        }
                    }
                } else if (exists && (!jwtBuildingId || jwtBuildingId !== currentBuildingId)) {
                    // Building is in localStorage but JWT does NOT carry the correct building_id claim.
                    // This happens after token expiry + re-login when the new JWT is unscoped.
                    // Re-issue a scoped JWT silently so all subsequent API calls use the correct building.
                    // Pass the UUID (currentId) — backend accepts both UUID and plan number.
                    try {
                        const switchRes = await api.post('/auth/switch-building', {building_id: currentId}, {
                            headers: tokenToUse ? {Authorization: `Bearer ${tokenToUse}`} : {}
                        });
                        const {token: scopedToken, building: refreshedBuilding} = switchRes.data;
                        if (scopedToken) {
                            tokenRef.current = scopedToken;
                            setToken(scopedToken);
                        }
                        if (refreshedBuilding) {
                            setSelectedBuilding(refreshedBuilding);
                            localStorage.setItem('selectedBuilding', JSON.stringify(refreshedBuilding));
                        }
                    } catch (switchErr: any) {
                        // 404 → cached building no longer exists (e.g. archived or post-cutover).
                        // Clear and force re-selection rather than leave the JWT unscoped.
                        if (switchErr?.response?.status === 404) {
                            try {
                                localStorage.removeItem('selectedBuilding');
                            } catch { /* ignore */
                            }
                            if (typeof window !== 'undefined' && !window.location.pathname.includes('/select-building')) {
                                window.location.href = '/select-building';
                                return;
                            }
                        }
                        console.warn('Silent building re-scope failed:', switchErr);
                    }
                }
            }

            // Re-read the JWT — auto-switch above may have rotated tokenRef to a scoped token.
            const tokenAfterSwitch = tokenRef.current;
            const payloadAfterSwitch = tokenAfterSwitch ? (() => {
                try {
                    return JSON.parse(atob(tokenAfterSwitch.split('.')[1]));
                } catch {
                    return {};
                }
            })() : {};
            const hasBuildingScope: boolean = !!payloadAfterSwitch.building_id;

            // Tenant-scoped fetches: only fire when the JWT actually carries a building_id.
            // For super-admins on /select-building (or anyone whose token has not yet been
            // scoped), `/years`, `/settings`, `/notifications/unread-count` 403 because they
            // require building context. Calling them anyway used to thrash render state with
            // 403s on every fetchUser → looked like a redirect loop.
            if (hasBuildingScope) {
                fetchNotifications();

                const [yearsRes, settingsRes] = await Promise.all([
                    api.get('/years').catch(err => {
                        console.error('Failed to fetch years:', err);
                        return {data: []};
                    }),
                    api.get('/settings').catch(err => {
                        console.error('Failed to fetch settings:', err);
                        return {data: {}};
                    })
                ]);

                const years = Array.isArray(yearsRes.data) ? yearsRes.data : (yearsRes.data?.years || []);
                setAvailableYears(years);
                if (years.length > 0) {
                    // Prefer the actual current calendar year when it's present in the list.
                    // years[0] (newest) can be a still-forming next-FY budget doc (e.g. imported
                    // early via scraper with status "partial_actual") that sorts above the real
                    // current year, silently defaulting every dashboard widget to next year's
                    // partial rates. Only fall back to years[0] when the current year isn't listed.
                    const currentCalendarYear = String(new Date().getFullYear());
                    const defaultYear = years.includes(currentCalendarYear) ? currentCalendarYear : years[0];
                    setSelectedYearState(prev => {
                        const savedYear = savedYearRef.current;
                        savedYearRef.current = null;
                        if (!prev && savedYear && years.includes(savedYear)) return savedYear;
                        // No stored value or stored value not in available list → use the default
                        if (!prev || !years.includes(prev)) return defaultYear;
                        return prev;
                    });
                } else {
                    // Building has no annual levy data yet (e.g. demo buildings).
                    // Keep any existing selectedYear or fall back to the current FY so that
                    // fetchDashboardData does not early-return on !selectedYear, leaving the
                    // management dashboard stuck on the loading spinner forever.
                    setSelectedYearState(prev => prev ?? String(new Date().getFullYear()));
                }

                setFinancialYearStartMonth(settingsRes.data?.financial_year_start_month || 1);

                // Update selectedBuilding from settings if it's the default and we have more info
                if (selectedBuilding?.id === DEFAULT_BUILDING_ID && settingsRes.data?.building_name && settingsRes.data?.building_name !== 'Our Residences') {
                    const updatedBuilding = {
                        ...selectedBuilding,
                        name: settingsRes.data.building_name,
                        address: settingsRes.data.building_address || selectedBuilding.address
                    };
                    setSelectedBuilding(updatedBuilding);
                    localStorage.setItem('selectedBuilding', JSON.stringify(updatedBuilding));
                }
            }

            // Fetch feature access for user
            try {
                const featureResponse = await api.get('/feature-toggles/access-summary/me');
                const accessMap: Record<string, boolean> = {};

                interface FeatureAccessEntry {
                    feature_key: string;
                    effective_access: boolean;
                }

                (featureResponse.data as FeatureAccessEntry[]).forEach((feature) => {
                    if (feature.feature_key) {
                        accessMap[feature.feature_key] = !!feature.effective_access;
                    }
                });
                setFeatureAccess(accessMap);
            } catch (featureError) {
                console.warn('Failed to fetch feature access:', featureError);
                setFeatureAccess({});
            }
        } catch (error) {
            console.error('Failed to fetch user:', error);
            setToken(null);
            tokenRef.current = null;
            setUser(null);
            setFeatureAccess({});
        } finally {
            setLoading(false);
        }
    }, [api, fetchNotifications]);

    useEffect(() => {
        fetchUser();
    }, [fetchUser]);

    useEffect(() => {
        if (token) {
            fetchUser(token);
        }
    }, [token, fetchUser]);

    useEffect(() => {
        if (session) {
            // NextAuth Session is augmented via next-auth.d.ts to include accessToken + user.data
            const extSession = session as typeof session & { accessToken?: string; user?: { data?: User } };
            const accessToken = extSession.accessToken;
            tokenRef.current = accessToken ?? null;

            // If the session user data has building_id, we might need to handle it
            const userData = extSession.user?.data || (session.user as unknown as User);
            setUser(userData ?? null);
            setToken(accessToken ?? null);
        } else if (status !== 'loading') {
            tokenRef.current = null;
            setUser(null);
            setToken(null);
            setLoading(false);
        }
    }, [session, status]);

    // Periodically fetch notifications
    useEffect(() => {
        if (token) {
            const interval = setInterval(fetchNotifications, 60000); // Every minute
            return () => clearInterval(interval);
        }
    }, [token, fetchNotifications]);

    const impersonate = useCallback(async (userId: string, buildingId?: string) => {
        try {
            const targetBuildingId = buildingId || selectedBuilding?.building_id;

            if (!targetBuildingId) {
                toast.error('No building selected for impersonation');
                return false;
            }

            const response = await api.post('/auth/impersonate', {
                user_id: userId,
                building_id: targetBuildingId
            });
            const data: { token?: string; user?: User } = response.data ?? {};

            // Validate response shape before trusting it
            if (!data.token || typeof data.token !== 'string' || !data.user?.id || !data.user?.full_name) {
                throw new Error('Invalid impersonation response from server');
            }

            const {token: impersonationToken, user: impersonatedUser} = data;

            // Store current admin token securely in sessionStorage
            const currentAdminToken = token;
            if (currentAdminToken) {
                setAdminToken(currentAdminToken);
                sessionStorage.setItem('admin_token', currentAdminToken);
            }

            // Switch to impersonated user
            tokenRef.current = impersonationToken;
            setToken(impersonationToken);
            setUser(impersonatedUser);

            toast.success(`Now impersonating ${impersonatedUser.full_name}`);
            return true;
        } catch (error: unknown) {
            console.error('Impersonation failed:', error);
            const axiosError = error as { response?: { data?: { detail?: string } }; message?: string };
            toast.error(axiosError.response?.data?.detail || axiosError.message || 'Impersonation failed');
            return false;
        }
    }, [api, token]);

    const exitImpersonation = useCallback(async () => {
        if (!adminToken) return;

        try {
            const restoredToken = adminToken;

            // Restore admin token
            tokenRef.current = restoredToken;
            setToken(restoredToken);

            // Clear impersonation state
            setAdminToken(null);
            sessionStorage.removeItem('admin_token');

            // Refetch admin profile
            await fetchUser(restoredToken);

            toast.info('Returned to administrator session');
        } catch (error) {
            console.error('Failed to exit impersonation:', error);
            logout();
        }
    }, [adminToken, fetchUser, logout]);

    const login = useCallback(async (email: string, password: string) => {
        const result = await nextAuthSignIn('credentials', {
            email,
            password,
            redirect: false,
        });
        if (result?.error === 'CallbackRouteError') {
            // Backend returned 429 — auth.ts re-throws the error so NextAuth surfaces
            // "CallbackRouteError" instead of "CredentialsSignin". Show a clear message.
            throw new Error('Too many login attempts. Please wait a moment and try again.');
        }
        if (result?.error || result?.ok === false) {
            throw new Error('Incorrect email or password. Please check your details and try again.');
        }
        // Check for pending_approval session (set when backend returns 403 pending_approval).
        // Sign out the stub session immediately so the user is not left in a broken state.
        const pendingSession = typeof window !== 'undefined'
            ? await fetch('/api/auth/session').then(r => r.json()).catch(() => null)
            : null;
        if (pendingSession?.is_pending_approval) {
            await nextAuthSignOut({redirect: false});
            const msg = pendingSession.pending_message ||
                'Your account is pending approval by the Strata Manager. You will receive an email once reviewed.';
            throw new Error(`PENDING_APPROVAL:${msg}`);
        }
        return result;
    }, []);

    const register = useCallback(async (userData: any, buildingId?: string) => {
        try {
            const config = buildingId ? {headers: {'X-Building-ID': buildingId}} : {};
            const response = await api.post('/auth/register', userData, config);
            const {token: newToken, user: newUser} = response.data;

            tokenRef.current = newToken;
            setToken(newToken);
            setUser(newUser);

            return newUser;
        } catch (error: any) {
            if (error.response?.status === 429) {
                throw new Error(error.rateLimitMessage || 'Too many registration attempts. Please try again in a minute.');
            }
            throw error;
        }
    }, [api]);

    const switchBuilding = useCallback(async (buildingId: string, options?: { redirectTo?: string | false }) => {
        try {
            const response = await api.post('/auth/switch-building', {building_id: buildingId});
            const {token: newToken, user: newUser, building} = response.data;

            // Update everything with new tenant context
            localStorage.setItem('selectedBuilding', JSON.stringify(building));
            tokenRef.current = newToken;
            setToken(newToken);
            setUser(newUser);
            setSelectedBuilding(building);
            // Reset unit context when building changes
            setSelectedUnit(null);
            setAvailableUnits([]);

            toast.success(`Switched to ${building.name}`);

            // Refresh browser to ensure all contexts reset correctly. Existing
            // callers keep the dashboard default; workflow pages can opt into
            // returning to their own route after the JWT/building context swap.
            const redirectTo = options?.redirectTo;
            if (redirectTo !== false) {
                window.location.href = redirectTo || '/dashboard';
            }
        } catch (error: any) {
            console.error('Failed to switch building:', error);
            // 404 → the selected building no longer exists in core.schemes (archived,
            // deleted, or stale list). Clear and bounce to /select-building.
            if (error?.response?.status === 404) {
                try {
                    localStorage.removeItem('selectedBuilding');
                } catch { /* ignore */
                }
                try {
                    sessionStorage.setItem('staleBuildingRedirect', '1');
                } catch { /* ignore */
                }
                toast.error('That building is no longer available. Please choose another.');
                if (typeof window !== 'undefined') {
                    window.location.href = '/select-building';
                }
                return;
            }
            toast.error('Failed to switch building');
        }
    }, [api]);

    const switchUnit = useCallback(async (unitNumber: string) => {
        try {
            const response = await api.post('/auth/switch-unit', {unit_number: unitNumber});
            const {token: newToken, user: newUser} = response.data;
            tokenRef.current = newToken;
            setToken(newToken);
            setUser(newUser);
            setSelectedUnit(unitNumber);
            if (newUser?.owned_units?.length) setAvailableUnits(newUser.owned_units);
            toast.success(`Switched to Unit ${unitNumber}`);
        } catch (error) {
            console.error('Failed to switch unit:', error);
            toast.error('Failed to switch unit');
        }
    }, [api]);

    const addUnit = useCallback(async (unitNumber: string) => {
        const response = await api.post('/auth/add-unit', {unit_number: unitNumber});
        const {owned_units, user: newUser} = response.data;
        if (newUser) setUser(newUser);
        if (owned_units?.length) setAvailableUnits(owned_units);
        return {owned_units: owned_units ?? []};
    }, [api]);

    const updateProfile = useCallback(async (updateData: any) => {
        if (!user) throw new Error('Not authenticated');

        const response = await api.put(`/users/${user.id}`, updateData);
        setUser(response.data);
        return response.data;
    }, [api, user]);

    const updateEmailPreference = useCallback(async (primaryEmail: string) => {
        if (!user) throw new Error('Not authenticated');

        const response = await api.post('/auth/email-preference', {primary_email: primaryEmail});
        setUser(response.data);
        return response.data;
    }, [api, user]);

    const hasPermission = useCallback((permission: string) => {
        // Super admins have all permissions
        if (user?.role === 'super_admin') return true;

        if (!user?.permissions) return false;
        return user.permissions[permission] === true;
    }, [user]);

    const isAdmin = useCallback(() => {
        return user?.role === 'super_admin';
    }, [user]);

    const isRealAdmin = useCallback(() => {
        if (adminToken) return true;
        return user?.role === 'super_admin';
    }, [user, adminToken]);

    const isImpersonating = !!adminToken;

    const isManager = useCallback(() => {
        return user?.role === 'super_admin' || user?.role === 'strata_admin' || user?.role === 'strata_manager' || user?.role === 'ec_member';
    }, [user]);

    const isECMember = useCallback(() => {
        return user?.role === 'ec_member' || user?.role === 'super_admin' || user?.role === 'strata_admin';
    }, [user]);

    /** True for the Strata Management Company admin role (or super_admin). */
    const isStrataAdmin = useCallback(() => {
        return user?.role === 'strata_admin' || user?.role === 'super_admin';
    }, [user]);

    /**
     * True when the authenticated user is a unit owner.
     * Also returns true for manager/EC users who may be acting on
     * behalf of an owner unit (i.e. they have a unit_number on their account).
     * Use `user?.role === 'owner'` directly if you need the raw role check only.
     */
    const isOwner = useCallback(() => {
        if (user?.role === 'owner') return true;
        // Management/EC users who also own a unit.
        if (user?.unit_number && isManager()) return true;
        return false;
    }, [user, isManager]);

    /** True only when the authenticated user holds the tenant role. */
    const isTenant = useCallback(() => {
        return user?.role === 'tenant';
    }, [user]);

    /** True only when the authenticated user holds the guest role. */
    const isGuest = useCallback(() => {
        return user?.role === 'guest';
    }, [user]);

    const hasFeatureAccess = useCallback((featureKey: string) => {
        // 1. Super admins always have access to all features
        if (user?.role === 'super_admin') return true;

        // 2. Check the effective access map from backend (honors elevation & overrides)
        if (featureAccess[featureKey] !== undefined) {
            return featureAccess[featureKey];
        }

        // 3. Fallback for core infrastructure components
        const coreInfrastructure = ['sidebar', 'user_guide', 'profile', 'settings'];
        if (coreInfrastructure.includes(featureKey)) return true;

        // 4. Default to false for specific feature keys not in the map (strict by default)
        return false;
    }, [user, featureAccess]);

    const fetchPendingApprovalsCount = useCallback(async (force = false) => {
        if (!hasPermission('can_manage_finances')) {
            return null;
        }

        if (approvalsPromiseRef.current) {
            return approvalsPromiseRef.current;
        }

        const now = Date.now();
        if (!force && now - lastApprovalsFetchRef.current < 2000) {
            return null;
        }
        lastApprovalsFetchRef.current = now;

        const fetchPromise = api.get('/work-orders/invoices/pending-approvals')
            .then(response => {
                setPendingApprovalsCount(response.data.length);
                approvalsPromiseRef.current = null;
                return response.data;
            })
            .catch(error => {
                console.error('Error fetching pending approvals count:', error);
                approvalsPromiseRef.current = null;
                return null;
            });

        approvalsPromiseRef.current = fetchPromise;
        return fetchPromise;
    }, [api, hasPermission]);

    const value: AuthContextValue = {
        user,
        token,
        loading,
        featureAccess,
        notifications,
        unreadCount,
        pendingApprovalsCount,
        setPendingApprovalsCount,
        selectedYear,
        setSelectedYear,
        availableYears,
        financialYearStartMonth,
        login,
        register,
        logout,
        updateProfile,
        updateEmailPreference,
        hasPermission,
        isAdmin,
        isRealAdmin,
        isManager,
        isECMember,
        isStrataAdmin,
        isOwner,
        isTenant,
        isGuest,
        hasFeatureAccess,
        isImpersonating,
        impersonate,
        exitImpersonation,
        api,
        fetchNotifications,
        markNotificationRead,
        markAllRead,
        fetchPendingApprovalsCount,
        isAuthenticated: !!user,
        // Multi-tenancy — buildings
        selectedBuilding,
        availableBuildings,
        switchBuilding,
        // Multi-unit — owners with multiple units in the same building
        selectedUnit,
        availableUnits,
        switchUnit,
        addUnit,
    };

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
};
/**
 * @generated FunctionHeader
 * Function: useAuth
 * Path: frontend/src/contexts/AuthContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        if (typeof window === 'undefined') {
            const ssrFallback: AuthContextValue = {
                isAuthenticated: false,
                loading: true,
                user: null,
                token: null,
                featureAccess: {},
                notifications: [],
                unreadCount: 0,
                pendingApprovalsCount: 0,

                setPendingApprovalsCount: () => undefined,
                selectedYear: null,

                setSelectedYear: () => undefined,
                availableYears: [],
                financialYearStartMonth: 1,

                isAdmin: () => false,

                isRealAdmin: () => false,

                isManager: () => false,

                isECMember: () => false,

                isStrataAdmin: () => false,

                isOwner: () => false,

                isTenant: () => false,

                isGuest: () => false,

                hasPermission: () => false,

                hasFeatureAccess: () => false,
                isImpersonating: false,

                impersonate: () => Promise.resolve(false),

                exitImpersonation: () => Promise.resolve(),

                login: () => Promise.resolve({}),

                logout: () => undefined,

                register: () => Promise.reject(new Error('Not available during SSR')),

                updateProfile: () => Promise.resolve(undefined),

                updateEmailPreference: () => Promise.resolve(undefined),

                fetchNotifications: () => Promise.resolve(),

                markNotificationRead: () => Promise.resolve(),

                markAllRead: () => Promise.resolve(),

                fetchPendingApprovalsCount: () => Promise.resolve(),
                selectedBuilding: null,
                availableBuildings: [],

                switchBuilding: () => Promise.resolve(),
                selectedUnit: null,
                availableUnits: [],

                switchUnit: (_unitNumber: string) => Promise.resolve(),

                addUnit: (_unitNumber: string) => Promise.resolve({owned_units: []}),
                api: {

                    get: () => Promise.reject(new Error('API not available during SSR')),

                    post: () => Promise.reject(new Error('API not available during SSR')),

                    put: () => Promise.reject(new Error('API not available during SSR')),

                    delete: () => Promise.reject(new Error('API not available during SSR'))
                } as unknown as AuthContextValue['api'],
            };
            return ssrFallback;
        }
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};

export default AuthContext;
