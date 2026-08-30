import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Switch } from '../../../components/ui/switch';
import { Badge } from '../../../components/ui/badge';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../../components/ui/dialog';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../../components/ui/tooltip';
import {
    Activity,
    AlertCircle,
    AlertTriangle,
    AtSign,
    BarChart2,
    Bell,
    BookOpen,
    Bot,
    Brain,
    Briefcase,
    Building2,
    Calculator,
    Calendar,
    CalendarCheck,
    CalendarDays,
    CheckCircle2,
    ChevronDown,
    ChevronRight,
    ClipboardList,
    ClipboardCheck,
    Clock,
    CreditCard,
    Database,
    DollarSign,
    Droplets,
    FileQuestion,
    FileText,
    Filter,
    Gauge,
    GitBranch,
    HandHelping,
    HeartPulse,
    History,
    Home,
    Info,
    KeyRound,
    KeySquare,
    Landmark,
    Layout,
    Leaf,
    Link2,
    Loader2,
    Lock,
    Mail,
    MailCheck,
    MailOpen,
    Map,
    Megaphone,
    MessageCircle,
    Newspaper,
    Package,
    PawPrint,
    PiggyBank,
    Plug,
    RefreshCw,
    Scale,
    Scroll,
    ScrollText,
    Search,
    Send,
    Settings,
    Shield,
    ShieldAlert,
    ShieldCheck,
    ShoppingCart,
    Tag,
    ToggleLeft,
    ToggleRight,
    Trash2,
    TrendingDown,
    TrendingUp,
    Upload,
    UserCheck,
    UserPlus,
    Users,
    UserSearch,
    UserX,
    Vote,
    Wrench,
    XCircle,
    Zap
} from 'lucide-react';
import { toast } from 'sonner';
import { Collapsible, CollapsibleContent, CollapsibleTrigger, } from '../../../components/ui/collapsible';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../../components/ui/select';

// Icon mapping for features
const ICON_MAP = {
    FileText, DollarSign, Calendar, MessageCircle,
    Megaphone, ShoppingCart, CalendarDays, AlertTriangle,
    Wrench, CalendarCheck, Newspaper, Mail, AtSign,
    AlertCircle, Package, Users, Trash2, Bell, BookOpen,
    FileQuestion, ClipboardCheck, Send, Building2, RefreshCw,
    UserPlus, UserX, History, Clock,
    Brain, ShieldCheck, Landmark, Droplets, Layout,
    CreditCard, TrendingUp, TrendingDown, BarChart2, Tag,
    Upload, PawPrint, Scroll, UserCheck, Lock, KeyRound,
    MailOpen, Settings, ScrollText, ShieldAlert, Bot,
    Database, MailCheck, KeySquare, Map, Home, Activity,
    Calculator, Briefcase, HeartPulse, Zap, HandHelping,
    Vote, PiggyBank, Scale, Plug, Gauge, UserSearch, Shield,
    ClipboardList, Leaf
};

const CATEGORY_ALIASES = {
    communications: 'communication'
};

const CATEGORY_ORDER = [
    'core',
    'communication',
    'financial',
    'governance',
    'community',
    'engagement',
    'operations',
    'safety',
    'facilities',
    'maintenance',
    'landlord',
    'intelligence',
    'ai',
    'system'
];

// Category metadata
const CATEGORY_INFO = {
    core: {
        label: 'Core Features',
        description: 'Essential functionality for all users',
        color: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
        icon: Shield
    },
    financial: {
        label: 'Financial Management',
        description: 'Finance, budgets, and levy payments',
        color: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300',
        icon: DollarSign
    },
    governance: {
        label: 'Governance & Admin',
        description: 'Meetings, committees, and administration',
        color: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300',
        icon: Users
    },
    communication: {
        label: 'Communication',
        description: 'Chat, messages, and announcements',
        color: 'bg-pink-100 text-pink-800 dark:bg-pink-900 dark:text-pink-300',
        icon: MessageCircle
    },
    community: {
        label: 'Community',
        description: 'Events, marketplace, and directory',
        color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300',
        icon: Users
    },
    safety: {
        label: 'Safety & Emergency',
        description: 'Emergency contacts and safety features',
        color: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300',
        icon: AlertTriangle
    },
    facilities: {
        label: 'Facilities Management',
        description: 'Maintenance, bookings, and parcels',
        color: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-300',
        icon: Wrench
    },
    system: {
        label: 'System Settings',
        description: 'System configuration and maintenance',
        color: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300',
        icon: Shield
    },
    maintenance: {
        label: 'Maintenance Intelligence',
        description: 'Predictive maintenance, asset health and capital planning',
        color: 'bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-300',
        icon: Wrench
    },
    landlord: {
        label: 'OwnerHub & Landlord',
        description: 'Landlord platform, tenancy management and property intelligence',
        color: 'bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-300',
        icon: Home
    },
    intelligence: {
        label: 'Market Intelligence',
        description: 'ACT strata market analysis, building risk index and investor signals',
        color: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-300',
        icon: Map
    },
    engagement: {
        label: 'Resident Engagement',
        description: 'Personalised dashboard experiences and building activity signals',
        color: 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-300',
        icon: Zap
    },
    operations: {
        label: 'Operations & Workflow',
        description: 'Case management, request routing and operational workflows',
        color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300',
        icon: Briefcase
    },
    ai: {
        label: 'AI & Automation',
        description: 'AI assessment, review panels and assisted decision workflows',
        color: 'bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900 dark:text-fuchsia-300',
        icon: Bot
    }
};
/**
 * @generated FunctionHeader
 * Function: getCategoryKey
 * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const getCategoryKey = (category) => CATEGORY_ALIASES[category] || category || 'other';
/**
 * @generated FunctionHeader
 * Function: humanizeCategory
 * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const humanizeCategory = (category) => {
    const key = getCategoryKey(category);
    if (!key || key === 'other') return 'Other Features';
    return key
        .split(/[_-]+/)
        .map(part => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
};
/**
 * @generated FunctionHeader
 * Function: getCategoryInfo
 * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const getCategoryInfo = (category) => {
    const key = getCategoryKey(category);
    return CATEGORY_INFO[key] || {
        label: humanizeCategory(key),
        description: 'Feature group',
        color: 'bg-slate-100 text-slate-800 dark:bg-slate-900 dark:text-slate-300',
        icon: Shield
    };
};
/**
 * @generated FunctionHeader
 * Function: compareCategories
 * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const compareCategories = (a, b) => {
    const ai = CATEGORY_ORDER.indexOf(a);
    const bi = CATEGORY_ORDER.indexOf(b);
    return ( ai === -1 ? 999 : ai ) - ( bi === -1 ? 999 : bi ) || a.localeCompare(b);
};
/**
 * @generated FunctionHeader
 * Function: FeatureTogglesPageV2
 * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FeatureTogglesPageV2 = () => {
    const {api, user} = useAuth();
    const isReadOnly = user?.role !== 'super_admin';
    const isSuperAdmin = user?.role === 'super_admin';
    const [loading, setLoading] = useState(true);
    const [features, setFeatures] = useState([]);
    const [siteSettings, setSiteSettings] = useState(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [categoryFilter, setCategoryFilter] = useState('all');
    const [expandedCategories, setExpandedCategories] = useState({});
    const [updatingFeatures, setUpdatingFeatures] = useState({});
    const [selectedBuildingId, setSelectedBuildingId] = useState('global');
    const [buildings, setBuildings] = useState([]);
    const [cascadeDialog, setCascadeDialog] = useState(null); // {featureKey, featureName, enabledDependents: [{feature_key, feature_name}]}

    // child_key → [parent_key, ...]
    const dependencyMap = useMemo(() => {
        const map = {};
        features.forEach(f => {
            if (f.depends_on?.length) map[ f.feature_key ] = f.depends_on;
        });
        return map;
    }, [features]);

    // parent_key → [child_key, ...]
    const reverseDependencyMap = useMemo(() => {
        const map = {};
        features.forEach(f => {
            ( f.depends_on || [] ).forEach(parent => {
                if (!map[ parent ]) map[ parent ] = [];
                map[ parent ].push(f.feature_key);
            });
        });
        return map;
    }, [features]);

    // feature_key → feature object
    const featureMap = useMemo(() => {
        return Object.fromEntries(features.map(f => [f.feature_key, f]));
    }, [features]);

    // Returns the name of the first disabled parent, or null
    const getBlockedByParent = useCallback((featureKey) => {
        const parents = dependencyMap[ featureKey ] || [];
        for (const parent of parents) {
            if (featureMap[ parent ] && !featureMap[ parent ].is_enabled) {
                return featureMap[ parent ];
            }
        }
        return null;
    }, [dependencyMap, featureMap]);

    const fetchFeatures = React.useCallback(async () => {
        try {
            setLoading(true);
            const buildingParam = selectedBuildingId !== 'global' ? `?building_id=${selectedBuildingId}` : '';
            const [featuresRes, settingsRes] = await Promise.all([
                api.get(`/feature-toggles/${buildingParam}`),
                api.get('/settings')
            ]);
            setFeatures(featuresRes.data);
            setSiteSettings(settingsRes.data);

            // Auto-expand all categories on first load
            const initialExpanded = {};
            const categories = [...new Set(featuresRes.data.map(f => getCategoryKey(f.category)))];
            categories.forEach(cat => {
                initialExpanded[ cat ] = true;
            });
            setExpandedCategories(initialExpanded);
        } catch (error) {
            console.error('Failed to fetch features:', error);
            toast.error('Failed to load feature toggles');
        } finally {
            setLoading(false);
        }
    }, [api, selectedBuildingId]);

    useEffect(() => {
        fetchFeatures();
    }, [fetchFeatures]);

    // Load buildings list for super admin
    useEffect(() => {
        if (!isSuperAdmin) return;
        api.get('/buildings/me').then(res => {
            const buildings = ( res.data || [] ).map(b => ( {
                id: b.id ?? b.building_id,
                building_id: b.building_id ?? b.id,
                name: b.name
            } ));
            setBuildings(buildings);
        }).catch(() => {
            setBuildings([]);
        });
    }, [api, isSuperAdmin]);
    /**
     * @generated FunctionHeader
     * Function: performToggle
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const performToggle = async (featureKey, newValue, extraKeysToDisable = []) => {
        try {
            setUpdatingFeatures(prev => {
                const next = {...prev};
                [featureKey, ...extraKeysToDisable].forEach(k => {
                    next[ k ] = true;
                });
                return next;
            });

            const buildingParam = selectedBuildingId !== 'global' ? `?building_id=${selectedBuildingId}` : '';

            // Toggle the primary feature
            await api.put(`/feature-toggles/${featureKey}${buildingParam}`, {is_enabled: newValue});

            // Cascade-disable dependents if requested
            if (!newValue && extraKeysToDisable.length > 0) {
                await Promise.all(
                    extraKeysToDisable.map(k =>
                        api.put(`/feature-toggles/${k}${buildingParam}`, {is_enabled: false})
                    )
                );
            }

            // Refresh to get accurate state from server
            await fetchFeatures();

            const cascadeNote = extraKeysToDisable.length > 0
                ? ` (+ ${extraKeysToDisable.length} dependent feature${extraKeysToDisable.length > 1 ? 's' : ''})`
                : '';
            toast.success(
                <div className="flex items-center gap-2">
                    {newValue ? <CheckCircle2 className="h-4 w-4"/> : <XCircle className="h-4 w-4"/>}
                    <span>{newValue ? 'Enabled' : `Disabled${cascadeNote}`} successfully</span>
                </div>
            );
        } catch (error) {
            console.error('Failed to toggle feature:', error);
            toast.error('Failed to update feature toggle');
        } finally {
            setUpdatingFeatures(prev => {
                const next = {...prev};
                [featureKey, ...extraKeysToDisable].forEach(k => {
                    delete next[ k ];
                });
                return next;
            });
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleToggle
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleToggle = async (featureKey, currentValue) => {
        if (isReadOnly) {
            toast.info('Only a Super Admin can enable or disable features. Please contact your Super Admin.');
            return;
        }

        const newValue = !currentValue;

        // Turning ON: check if blocked by a disabled parent
        if (newValue) {
            const blockedBy = getBlockedByParent(featureKey);
            if (blockedBy) {
                toast.warning(
                    <div>
                        <p className="font-medium">Cannot enable this feature</p>
                        <p className="text-xs mt-1">Enable <strong>{blockedBy.feature_name}</strong> first.</p>
                    </div>
                );
                return;
            }
            await performToggle(featureKey, true);
            return;
        }

        // Turning OFF: check if any enabled dependents exist
        const childKeys = reverseDependencyMap[ featureKey ] || [];
        const enabledChildren = childKeys
            .map(k => featureMap[ k ])
            .filter(f => f && f.is_enabled);

        if (enabledChildren.length > 0) {
            // Show cascade confirmation dialog
            setCascadeDialog({
                featureKey,
                featureName: featureMap[ featureKey ]?.feature_name || featureKey,
                enabledDependents: enabledChildren.map(f => ( {
                    feature_key: f.feature_key,
                    feature_name: f.feature_name
                } )),
            });
            return;
        }

        await performToggle(featureKey, false);
    };
    /**
     * @generated FunctionHeader
     * Function: handleUpdateSettings
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateSettings = async () => {
        try {
            await api.put('/settings', siteSettings);
            toast.success('System settings updated successfully');
        } catch (error) {
            console.error('Failed to update settings:', error);
            toast.error('Failed to update system settings');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: toggleCategory
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleCategory = (category) => {
        setExpandedCategories(prev => ( {
            ...prev,
            [ category ]: !prev[ category ]
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: toggleAllCategories
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleAllCategories = (expanded) => {
        const newState = {};
        Object.keys(groupedFeatures).forEach(cat => {
            newState[ cat ] = expanded;
        });
        setExpandedCategories(newState);
    };

    // Filtered and grouped features
    const filteredFeatures = useMemo(() => {
        return features.filter(feature => {
            // Search filter
            if (searchQuery) {
                const query = searchQuery.toLowerCase();
                const matchesName = feature.feature_name.toLowerCase().includes(query);
                const matchesDescription = feature.description?.toLowerCase().includes(query);
                const matchesKey = feature.feature_key.toLowerCase().includes(query);
                if (!matchesName && !matchesDescription && !matchesKey) return false;
            }

            // Status filter
            if (statusFilter === 'enabled' && !feature.is_enabled) return false;
            if (statusFilter === 'disabled' && feature.is_enabled) return false;

            // Category filter
            if (categoryFilter !== 'all' && getCategoryKey(feature.category) !== categoryFilter) return false;

            return true;
        });
    }, [features, searchQuery, statusFilter, categoryFilter]);

    const groupedFeatures = useMemo(() => {
        const grouped = {};
        filteredFeatures.forEach(feature => {
            const category = getCategoryKey(feature.category);
            if (!grouped[ category ]) {
                grouped[ category ] = [];
            }
            grouped[ category ].push(feature);
        });
        return grouped;
    }, [filteredFeatures]);

    const availableCategories = useMemo(() => {
        return [...new Set(features.map(feature => getCategoryKey(feature.category)))].sort(compareCategories);
    }, [features]);

    const stats = useMemo(() => {
        const total = features.length;
        const enabled = features.filter(f => f.is_enabled).length;
        const disabled = total - enabled;
        return {total, enabled, disabled};
    }, [features]);
    /**
     * @generated FunctionHeader
     * Function: renderFeatureCard
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderFeatureCard = (feature) => {
        const IconComponent = ICON_MAP[ feature.icon ] || Shield;
        const isEnabled = feature.is_enabled;
        const isUpdating = updatingFeatures[ feature.feature_key ];
        const blockedBy = getBlockedByParent(feature.feature_key);
        const isBlocked = !!blockedBy;
        const dependentCount = ( reverseDependencyMap[ feature.feature_key ] || [] ).length;

        // Border colour: amber if blocked, green if enabled, gray if disabled
        const borderClass = isBlocked
            ? 'border-l-4 border-l-amber-400 opacity-70'
            : isEnabled
                ? 'border-l-4 border-l-green-500'
                : 'border-l-4 border-l-gray-300 opacity-75';

        const iconBgClass = isBlocked
            ? 'bg-amber-50 dark:bg-amber-900/20'
            : isEnabled
                ? 'bg-green-100 dark:bg-green-900/20'
                : 'bg-gray-100 dark:bg-gray-800';

        const iconColourClass = isBlocked
            ? 'text-amber-600 dark:text-amber-400'
            : isEnabled
                ? 'text-green-700 dark:text-green-400'
                : 'text-gray-500 dark:text-gray-400';

        return (
            <TooltipProvider key={feature.id}>
                <Card className={`transition-all duration-200 hover:shadow-md ${borderClass}`}>
                    <CardContent className="p-4">
                        <div className="flex items-start justify-between gap-4">
                            {/* Icon and Info */}
                            <div className="flex items-start gap-3 flex-1 min-w-0">
                                <div className={`p-2.5 rounded-lg flex-shrink-0 ${iconBgClass}`}>
                                    <IconComponent className={`h-5 w-5 ${iconColourClass}`}/>
                                </div>

                                <div className="flex-1 min-w-0 space-y-1.5">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h3 className="font-semibold text-sm">{feature.feature_name}</h3>

                                        {/* Status badge */}
                                        {isBlocked ? (
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Badge variant="outline"
                                                           className="bg-amber-50 text-amber-700 border-amber-300 text-xs cursor-help">
                                                        <AlertTriangle className="h-3 w-3 mr-1"/>
                                                        Blocked
                                                    </Badge>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    <p className="text-xs">Requires <strong>{blockedBy.feature_name}</strong> to
                                                        be enabled</p>
                                                </TooltipContent>
                                            </Tooltip>
                                        ) : isEnabled ? (
                                            <Badge variant="outline"
                                                   className="bg-green-50 text-green-700 border-green-200 text-xs">
                                                Active
                                            </Badge>
                                        ) : (
                                            <Badge variant="outline"
                                                   className="bg-gray-50 text-gray-600 border-gray-200 text-xs">
                                                Inactive
                                            </Badge>
                                        )}

                                        {/* Dependency chain indicator */}
                                        {dependentCount > 0 && (
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Badge variant="secondary"
                                                           className="text-[10px] cursor-help gap-1">
                                                        <Link2 className="h-3 w-3"/>
                                                        {dependentCount} dependent{dependentCount > 1 ? 's' : ''}
                                                    </Badge>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    <p className="text-xs font-medium mb-1">Features that depend on
                                                        this:</p>
                                                    <ul className="text-xs space-y-0.5">
                                                        {( reverseDependencyMap[ feature.feature_key ] || [] ).map(k => (
                                                            <li key={k} className="flex items-center gap-1">
                                                                <GitBranch className="h-2.5 w-2.5 opacity-60"/>
                                                                {featureMap[ k ]?.feature_name || k}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                    <p className="text-[10px] text-muted-foreground mt-1.5">
                                                        Disabling this will block all dependents.
                                                    </p>
                                                </TooltipContent>
                                            </Tooltip>
                                        )}

                                        {/* Parent dependency badge */}
                                        {( feature.depends_on || [] ).length > 0 && !isBlocked && (
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Badge variant="outline"
                                                           className="text-[10px] text-muted-foreground border-dashed cursor-help">
                                                        requires {feature.depends_on.length} parent{feature.depends_on.length > 1 ? 's' : ''}
                                                    </Badge>
                                                </TooltipTrigger>
                                                <TooltipContent>
                                                    <p className="text-xs font-medium mb-1">Depends on:</p>
                                                    <ul className="text-xs space-y-0.5">
                                                        {( feature.depends_on || [] ).map(k => (
                                                            <li key={k} className="flex items-center gap-1">
                                                                <CheckCircle2 className="h-2.5 w-2.5 text-green-500"/>
                                                                {featureMap[ k ]?.feature_name || k}
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </TooltipContent>
                                            </Tooltip>
                                        )}
                                    </div>

                                    <p className="text-xs text-muted-foreground line-clamp-2">
                                        {feature.description}
                                    </p>

                                    {/* Blocked warning */}
                                    {isBlocked && (
                                        <div
                                            className="flex items-center gap-1.5 text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded px-2 py-1">
                                            <AlertTriangle className="h-3 w-3 flex-shrink-0"/>
                                            <span className="text-[11px]">
                        This feature is inactive because <strong>{blockedBy.feature_name}</strong> is disabled.
                      </span>
                                        </div>
                                    )}

                                    {feature.routes && feature.routes.length > 0 && (
                                        <div className="flex flex-wrap gap-1 mt-1.5">
                                            {feature.routes.slice(0, 2).map((route, idx) => (
                                                <Badge key={idx} variant="secondary"
                                                       className="text-[10px] font-mono px-1.5 py-0.5">
                                                    {route}
                                                </Badge>
                                            ))}
                                            {feature.routes.length > 2 && (
                                                <Badge variant="secondary" className="text-[10px] px-1.5 py-0.5">
                                                    +{feature.routes.length - 2} more
                                                </Badge>
                                            )}
                                        </div>
                                    )}

                                    {/* Notification Cleanup Settings */}
                                    {feature.feature_key === 'notification_cleanup' && isEnabled && (
                                        <div
                                            className="mt-3 p-3 bg-orange-50 dark:bg-orange-900/10 rounded-md border border-orange-100 dark:border-orange-900/30">
                                            <div
                                                className="flex items-center gap-2 text-orange-800 dark:text-orange-400 mb-2">
                                                <Clock className="h-3.5 w-3.5"/>
                                                <span className="text-xs font-semibold">Cleanup Configuration</span>
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <Input
                                                    type="number"
                                                    min="1"
                                                    max="366"
                                                    value={siteSettings?.notification_retention_days || 30}
                                                    onChange={(e) => setSiteSettings({
                                                        ...siteSettings,
                                                        notification_retention_days: parseInt(e.target.value) || 30
                                                    })}
                                                    className="w-20 h-8 text-xs"
                                                />
                                                <span className="text-xs text-muted-foreground">days retention</span>
                                                <Button
                                                    size="sm"
                                                    onClick={handleUpdateSettings}
                                                    className="h-7 text-xs ml-auto"
                                                >
                                                    Update
                                                </Button>
                                            </div>
                                            <p className="text-[10px] text-muted-foreground mt-1.5">
                                                Auto-delete notifications after this period or when task completes
                                            </p>
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Toggle Switch */}
                            <div className="flex flex-col items-end gap-2 flex-shrink-0">
                                <div className="relative">
                                    {isUpdating && (
                                        <div
                                            className="absolute inset-0 flex items-center justify-center bg-background/80 rounded">
                                            <Loader2 className="h-4 w-4 animate-spin"/>
                                        </div>
                                    )}
                                    <Switch
                                        checked={isEnabled}
                                        onCheckedChange={() => handleToggle(feature.feature_key, isEnabled)}
                                        disabled={isUpdating || isReadOnly || isBlocked}
                                        className="data-[state=checked]:bg-green-600"
                                    />
                                </div>
                                <span className={`text-[10px] font-medium ${
                                    isBlocked ? 'text-amber-600' : isEnabled ? 'text-green-600' : 'text-gray-500'
                                }`}>
                  {isBlocked ? 'BLOCKED' : isEnabled ? 'ON' : 'OFF'}
                </span>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </TooltipProvider>
        );
    };
    /**
     * @generated FunctionHeader
     * Function: renderCategorySection
     * Path: frontend/src/pages/dashboard/admin/FeatureTogglesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderCategorySection = (category, categoryFeatures) => {
        const info = getCategoryInfo(category);
        const isExpanded = expandedCategories[ category ];
        const enabledCount = categoryFeatures.filter(f => f.is_enabled).length;
        const totalCount = categoryFeatures.length;
        const CategoryIcon = info.icon;

        return (
            <Collapsible
                key={category}
                open={isExpanded}
                onOpenChange={() => toggleCategory(category)}
                className="space-y-3"
            >
                <Card className="border-2 overflow-hidden">
                    <CollapsibleTrigger asChild>
                        <CardHeader className="p-4 cursor-pointer hover:bg-muted/50 transition-colors">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <div
                                        className={`p-2 rounded-lg ${info.color.replace('text-', 'bg-').replace('800', '100').replace('300', '900')}`}>
                                        <CategoryIcon className={`h-5 w-5 ${info.color}`}/>
                                    </div>
                                    <div>
                                        <CardTitle className="text-base font-semibold flex items-center gap-2">
                                            {info.label}
                                            <Badge variant="secondary" className="ml-2">
                                                {enabledCount}/{totalCount}
                                            </Badge>
                                        </CardTitle>
                                        <p className="text-xs text-muted-foreground mt-0.5">
                                            {info.description}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-3">
                                    <div className="text-right">
                                        <div className="text-xs text-muted-foreground">
                                            {enabledCount} active • {totalCount - enabledCount} inactive
                                        </div>
                                    </div>
                                    {isExpanded ? (
                                        <ChevronDown className="h-5 w-5 text-muted-foreground"/>
                                    ) : (
                                        <ChevronRight className="h-5 w-5 text-muted-foreground"/>
                                    )}
                                </div>
                            </div>
                        </CardHeader>
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                        <div className="p-4 pt-0 space-y-2 bg-muted/20">
                            {categoryFeatures.map(renderFeatureCard)}
                        </div>
                    </CollapsibleContent>
                </Card>
            </Collapsible>
        );
    };

    if (loading) {
        return (
            <div className="max-w-7xl mx-auto space-y-6 p-6">
                <div className="flex items-center gap-4">
                    <div className="p-3 rounded-full bg-primary/10">
                        <Shield className="h-8 w-8 text-primary animate-pulse"/>
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold">Feature Toggles</h1>
                        <p className="text-muted-foreground">Loading features...</p>
                    </div>
                </div>
                <div className="grid gap-4">
                    {[1, 2, 3].map((i) => (
                        <Card key={i} className="card-dashboard">
                            <CardContent className="p-6">
                                <div className="skeleton h-24 w-full"/>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            </div>
        );
    }

    const sortedCategories = Object.keys(groupedFeatures).sort(compareCategories);

    return (
        <div className="max-w-7xl mx-auto space-y-6 p-6" data-testid="feature-toggles-page">
            {/* Read-only notice */}
            {isReadOnly && (
                <Card className="border-blue-200 bg-blue-50 dark:bg-blue-900/10 dark:border-blue-900">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <Info className="h-5 w-5 text-blue-600 dark:text-blue-400 flex-shrink-0"/>
                            <p className="text-sm text-blue-800 dark:text-blue-300">
                                <strong>View Only:</strong> You can view feature toggles but only a Super Admin can
                                enable or disable them.
                            </p>
                        </div>
                    </CardContent>
                </Card>
            )}
            {/* Header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-4">
                    <div className="p-3 rounded-full bg-primary/10">
                        <Shield className="h-8 w-8 text-primary"/>
                    </div>
                    <div>
                        <h1 className="text-2xl font-bold">Feature Toggles</h1>
                        <p className="text-sm text-muted-foreground">
                            {selectedBuildingId === 'global'
                                ? 'Global defaults — apply to all buildings unless overridden'
                                : `Per-building overrides for: ${buildings.find(b => b.id === selectedBuildingId)?.name ?? selectedBuildingId}`}
                        </p>
                    </div>
                </div>
                {isSuperAdmin && (
                    <div className="flex items-center gap-2">
                        <Building2 className="h-4 w-4 text-muted-foreground"/>
                        <Select value={selectedBuildingId} onValueChange={setSelectedBuildingId}>
                            <SelectTrigger className="w-52 h-8 text-sm">
                                <SelectValue placeholder="Select building"/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="global">Global Defaults</SelectItem>
                                {buildings.map(b => (
                                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                )}
                <div className="flex gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => toggleAllCategories(false)}
                    >
                        Collapse All
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => toggleAllCategories(true)}
                    >
                        Expand All
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={fetchFeatures}
                    >
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Card className="border-2">
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-muted-foreground font-medium">Total Features</p>
                                <p className="text-3xl font-bold mt-1">{stats.total}</p>
                            </div>
                            <div className="p-3 rounded-full bg-blue-100 dark:bg-blue-900/20">
                                <Shield className="h-6 w-6 text-blue-600 dark:text-blue-400"/>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-2 border-green-200 dark:border-green-900">
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-muted-foreground font-medium">Active</p>
                                <p className="text-3xl font-bold text-green-600 dark:text-green-400 mt-1">
                                    {stats.enabled}
                                </p>
                            </div>
                            <div className="p-3 rounded-full bg-green-100 dark:bg-green-900/20">
                                <ToggleRight className="h-6 w-6 text-green-600 dark:text-green-400"/>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-2 border-gray-200 dark:border-gray-800">
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-muted-foreground font-medium">Inactive</p>
                                <p className="text-3xl font-bold text-gray-600 dark:text-gray-400 mt-1">
                                    {stats.disabled}
                                </p>
                            </div>
                            <div className="p-3 rounded-full bg-gray-100 dark:bg-gray-800">
                                <ToggleLeft className="h-6 w-6 text-gray-600 dark:text-gray-400"/>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Search and Filters */}
            <Card className="border-2">
                <CardContent className="p-4">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="md:col-span-1">
                            <Label htmlFor="search" className="text-xs font-semibold mb-2 block">
                                Search Features
                            </Label>
                            <div className="relative">
                                <Search
                                    className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                <Input
                                    id="search"
                                    type="text"
                                    placeholder="Search by name, description..."
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    className="pl-9"
                                />
                            </div>
                        </div>

                        <div>
                            <Label htmlFor="status-filter" className="text-xs font-semibold mb-2 block">
                                Filter by Status
                            </Label>
                            <Select value={statusFilter} onValueChange={setStatusFilter}>
                                <SelectTrigger id="status-filter">
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All Features ({stats.total})</SelectItem>
                                    <SelectItem value="enabled">Active Only ({stats.enabled})</SelectItem>
                                    <SelectItem value="disabled">Inactive Only ({stats.disabled})</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>

                        <div>
                            <Label htmlFor="category-filter" className="text-xs font-semibold mb-2 block">
                                Filter by Category
                            </Label>
                            <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                                <SelectTrigger id="category-filter">
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All Categories</SelectItem>
                                    {availableCategories.map((key) => (
                                        <SelectItem key={key} value={key}>{getCategoryInfo(key).label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    {( searchQuery || statusFilter !== 'all' || categoryFilter !== 'all' ) && (
                        <div className="mt-3 flex items-center gap-2">
                            <Filter className="h-4 w-4 text-muted-foreground"/>
                            <span className="text-xs text-muted-foreground">
                Showing {filteredFeatures.length} of {features.length} features
              </span>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 text-xs ml-auto"
                                onClick={() => {
                                    setSearchQuery('');
                                    setStatusFilter('all');
                                    setCategoryFilter('all');
                                }}
                            >
                                Clear Filters
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Feature Categories */}
            <div className="space-y-4">
                {sortedCategories.length > 0 ? (
                    sortedCategories.map(category =>
                        renderCategorySection(category, groupedFeatures[ category ])
                    )
                ) : (
                    <Card className="border-2">
                        <CardContent className="p-12 text-center">
                            <Filter className="h-12 w-12 text-muted-foreground mx-auto mb-4 opacity-50"/>
                            <p className="text-muted-foreground font-medium">No features match your filters</p>
                            <p className="text-sm text-muted-foreground mt-1">
                                Try adjusting your search or filter criteria
                            </p>
                            <Button
                                variant="outline"
                                size="sm"
                                className="mt-4"
                                onClick={() => {
                                    setSearchQuery('');
                                    setStatusFilter('all');
                                    setCategoryFilter('all');
                                }}
                            >
                                Clear All Filters
                            </Button>
                        </CardContent>
                    </Card>
                )}
            </div>

            {/* Help Text */}
            <Card className="border-dashed border-2 bg-muted/30">
                <CardContent className="p-4">
                    <div className="flex items-start gap-3">
                        <Shield className="h-5 w-5 text-muted-foreground flex-shrink-0 mt-0.5"/>
                        <div className="space-y-1">
                            <p className="text-sm font-medium">About Feature Toggles</p>
                            <p className="text-xs text-muted-foreground">
                                Disabled features are hidden from all users except Super Admins.
                                Features marked <strong>Blocked</strong> (amber) have a parent feature that is disabled
                                — enable the parent first.
                                Features showing <strong>dependents</strong> will block child features if turned off.
                                Use per-user overrides in <strong>User Features</strong> to grant individual access.
                                Changes take effect immediately.
                            </p>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Cascade disable confirmation dialog */}
            <Dialog open={!!cascadeDialog} onOpenChange={() => setCascadeDialog(null)}>
                <DialogContent className="w-full max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <AlertTriangle className="h-5 w-5 text-amber-500"/>
                            Disable Feature with Dependents
                        </DialogTitle>
                        <DialogDescription>
                            <strong>{cascadeDialog?.featureName}</strong> has {cascadeDialog?.enabledDependents?.length} active
                            dependent feature{cascadeDialog?.enabledDependents?.length > 1 ? 's' : ''} that rely on it.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-3 py-2">
                        <p className="text-sm text-muted-foreground">The following enabled features depend on this one
                            and will be <strong>blocked</strong> (but not automatically disabled) unless you cascade:
                        </p>
                        <div className="rounded-md border bg-muted/40 p-3 space-y-1.5 max-h-48 overflow-y-auto">
                            {( cascadeDialog?.enabledDependents || [] ).map(dep => (
                                <div key={dep.feature_key} className="flex items-center gap-2 text-sm">
                                    <Link2 className="h-3.5 w-3.5 text-amber-500 flex-shrink-0"/>
                                    <span>{dep.feature_name}</span>
                                </div>
                            ))}
                        </div>
                    </div>

                    <DialogFooter className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
                        <Button
                            variant="outline"
                            onClick={() => setCascadeDialog(null)}
                            className="w-full sm:w-auto"
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="outline"
                            className="w-full sm:w-auto border-amber-300 text-amber-700 hover:bg-amber-50"
                            onClick={async () => {
                                const {featureKey} = cascadeDialog;
                                setCascadeDialog(null);
                                await performToggle(featureKey, false, []);
                            }}
                        >
                            Disable only &ldquo;{cascadeDialog?.featureName}&rdquo;
                        </Button>
                        <Button
                            variant="destructive"
                            className="w-full sm:w-auto"
                            onClick={async () => {
                                const {featureKey, enabledDependents} = cascadeDialog;
                                setCascadeDialog(null);
                                await performToggle(featureKey, false, enabledDependents.map(d => d.feature_key));
                            }}
                        >
                            Disable all ({( cascadeDialog?.enabledDependents?.length || 0 ) + 1})
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default FeatureTogglesPageV2;
