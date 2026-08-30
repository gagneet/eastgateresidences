// @featuretrace:sidebar-badges — Dashboard layout: sidebar navigation with live badge counts for notices, compliance, approvals.
// Layer: frontend
// Data flow: DashboardLayout.tsx → /engagement/nav/badges → db.notices, db.compliance_items,
//            db.workflow_requests, db.memberships, db.users (building-scoped).
// Related: frontend/src/contexts/AuthContext.tsx
//           backend/routers/engagement.py  (/nav/badges endpoint)
//           frontend/src/pages/dashboard/NoticeBoardPage.jsx  (@featuretrace:notices)

"use client";
import React, {useEffect, useState, useCallback, useMemo} from 'react';
import Link from 'next/link';
import Image from 'next/image';
import {usePathname, useRouter} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {useIntegrity} from '../../contexts/IntegrityContext';
import {Button} from '../ui/button';
import {Avatar, AvatarFallback, AvatarImage} from '../ui/avatar';
import {ScrollArea} from '../ui/scroll-area';
import {Tooltip, TooltipContent, TooltipTrigger} from '../ui/tooltip';
import {Badge} from '../ui/badge';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import {
    AlertTriangle,
    Banknote,
    BarChart3,
    Bell,
    BookOpen,
    Brain,
    BarChart as ChartBarIcon,
    Building2,
    Briefcase,
    Calculator,
    Calendar,
    CalendarDays,
    ChevronDown,
    ChevronLeft,
    ChevronRight,
    ClipboardCheck,
    ClipboardList,
    Clock,
    CreditCard,
    DollarSign,
    FileCheck,
    FileDown,
    FileQuestion,
    FileText,
    History,
    Home,
    Key,
    LayoutDashboard,
    LogOut,
    Mail,
    Megaphone,
    Menu,
    MessageCircle,
    MessageSquare,
    Newspaper,
    Package,
    Phone,
    RefreshCw,
    ScanText,
    Send,
    Settings,
    Shield,
    ShieldAlert,
    ShieldCheck,
    ShieldPlus,
    ShoppingBag,
    ToggleLeft,
    TrendingUp,
    Truck,
    UserCircle,
    UserCog,
    UserPlus,
    Users,
    UserX,
    Vote,
    Wrench,
    Droplets,
    Receipt,
    Landmark,
    Dog,
    UserSearch,
    Activity,
    BarChart2,
    ClipboardSignature,
    Map,
    Upload,
    Scale,
    PieChart,
    PiggyBank,
    GitBranch,
    Zap,
    Database,
    Layers,
    SlidersHorizontal,
    Search,
    X
,
    Gavel} from 'lucide-react';
import {getInitials, roleDisplayNames, ecPositionDisplayNames} from '../../lib/utils';

// Module-level constant — never recreated across renders of NavContent.
const CLASSIC_SYNONYMS: Record<string, string[]> = {
    pay: ["levies", "finance", "payment"], bill: ["levies", "finance"],
    money: ["finance", "levies", "budget"], financial: ["finance", "levies"],
    fix: ["maintenance", "repair"], repair: ["maintenance"], broken: ["maintenance"],
    vote: ["proposals", "voting"], ballot: ["proposals"],
    bylaw: ["by-laws", "rules"], rules: ["by-laws"], regulation: ["compliance"],
    chat: ["community", "messages"], message: ["community"], mail: ["email"],
    notice: ["notices", "announcements"], announcement: ["notices"],
    meeting: ["meetings"], agm: ["meetings"],
    doc: ["documents"], file: ["documents"], upload: ["documents"],
    ocr: ["ocr", "documents"], scan: ["ocr", "documents"],
    user: ["users", "residents"], resident: ["users"],
    parcel: ["parcels", "delivery"], delivery: ["parcels"], package: ["parcels"],
    visitor: ["visitors"], book: ["bookings"], reserve: ["bookings"], event: ["events"],
    budget: ["finance"], cert: ["certificates"], certificate: ["rental-certificates"],
    risk: ["global-risk", "intelligence"], audit: ["audit-logs"], log: ["audit-logs"],
    toggle: ["feature-toggles"], config: ["settings"],
    savings: ["savings-tracker"], volunteer: ["volunteer-credits"],
    invoice: ["invoices"], po: ["purchase-orders"],
    work: ["work-orders", "maintenance"], compliance: ["compliance", "safety"],
    safety: ["compliance"], report: ["reports", "analytics"],
    market: ["marketplace"], listing: ["marketplace"], sell: ["marketplace"],
    move: ["move-in-out"], key: ["key-register"],
};
import ImpersonateModal from './ImpersonateModal';
import ImpersonationBanner from './ImpersonationBanner';
import BuildingSwitcher from './BuildingSwitcher';
import UnitSwitcher from './UnitSwitcher';
import {signOut as nextAuthSignOut} from 'next-auth/react';
import {NavigationProvider, useNavigation} from '../../contexts/NavigationContext';
import {SidebarNav} from './Sidebar';
import {NavCustomiseDrawer} from './NavCustomiseDrawer';

interface DashboardLayoutProps {
    children: React.ReactNode;
}
/**
 * @generated FunctionHeader
 * Function: SidebarTooltip
 * Path: frontend/src/components/layout/DashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SidebarTooltip = ({children, label, show}: { children: React.ReactNode, label: string, show: boolean }) => {
    if (!show) return <>{children}</>;
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                {children}
            </TooltipTrigger>
            <TooltipContent side="right">
                {label}
            </TooltipContent>
        </Tooltip>
    );
};

const publicNavItems = [
    {href: '/', label: 'Home', icon: Home},
    {href: '/about', label: 'About', icon: Users},
    {href: '/blog', label: 'News', icon: Newspaper},
    {href: '/marketplace', label: 'Marketplace', icon: ShoppingBag},
];

const DEFAULT_BUILDING_ID = process.env.NEXT_PUBLIC_DEFAULT_BUILDING_ID || '13195';
/**
 * @generated FunctionHeader
 * Function: DashboardLayout
 * Path: frontend/src/components/layout/DashboardLayout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const DashboardLayout: React.FC<DashboardLayoutProps> = ({children}) => {
    const {
        user, logout, hasPermission, isAdmin, isRealAdmin, isManager, isECMember,
        isOwner, isTenant, isGuest,
        hasFeatureAccess, notifications, unreadCount, markNotificationRead, markAllRead,
        pendingApprovalsCount, fetchPendingApprovalsCount,
        isImpersonating,
        selectedBuilding, availableBuildings, switchBuilding,
        api
    } = useAuth();
    const [siteSettings, setSiteSettings] = useState<Record<string, string>>({
        ec_email: '',
        ec_contact_phone: '',
        handbook_url: '/user-guides/full_guide.html',
        unit_plan_url: '/user-guides/units_plan.html',
        inclusions_url: '',
    });
    const {ipString} = useIntegrity();
    const pathname = usePathname();
    const router = useRouter();
    const [noticesCount, setNoticesCount] = useState(0);
    const [overdueComplianceCount, setOverdueComplianceCount] = useState(0);
    const [tenantApprovalsCount, setTenantApprovalsCount] = useState(0);
    const [userApprovalsCount, setUserApprovalsCount] = useState(0);
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
    const [impersonateModalOpen, setImpersonateModalOpen] = useState(false);
    const [customiseDrawerOpen, setCustomiseDrawerOpen] = useState(false);

    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
        overview: false,
        intelligence: false,
        financials: false,
        governance: false,
        communication: false,
        community: false,
        maintenance: false,
        ownerhub: false,
        my_tenancy: false,
        management: false,
        building_config: false,
        platform_admin: false,
        help: false,
        personal: false,
        public_site: false,
    });
    /**
     * @generated FunctionHeader
     * Function: toggleGroup
     * Path: frontend/src/components/layout/DashboardLayout.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleGroup = (groupId: string) => {
        if (groupId === 'overview') return;
        setOpenGroups(prev => ({
            ...prev,
            [groupId]: !prev[groupId]
        }));
    };

    const fetchNavBadges = useCallback(async () => {
        if (!api || !user) return;
        try {
            const res = await api.get('/engagement/nav/badges');
            setNoticesCount(res.data?.notices ?? 0);
            setOverdueComplianceCount(res.data?.compliance ?? 0);
            setTenantApprovalsCount(res.data?.tenant_approvals ?? 0);
            setUserApprovalsCount(res.data?.user_approvals ?? 0);
        } catch {
            // silently ignore — badges are non-critical
        }
    }, [api, user]);

    useEffect(() => {
        fetchPendingApprovalsCount();
        fetchNavBadges();
        const interval = setInterval(() => {
            fetchPendingApprovalsCount(true);
            fetchNavBadges();
        }, 60000);
        return () => clearInterval(interval);
    }, [fetchPendingApprovalsCount, fetchNavBadges]);

    useEffect(() => {
        if (!api) return;
        api.get('/settings').then((res: any) => {
            const d = res.data || {};
            setSiteSettings(prev => ({
                ...prev,
                ec_email: d.ec_email ?? '',
                ec_contact_phone: d.ec_contact_phone ?? '',
                ...(d.handbook_url ? {handbook_url: d.handbook_url} : {}),
                ...(d.unit_plan_url ? {unit_plan_url: d.unit_plan_url} : {}),
                ...(d.inclusions_url ? {inclusions_url: d.inclusions_url} : {}),
            }));
        }).catch(() => {/* use defaults */
        });
    }, [api, selectedBuilding?.id]);

    useEffect(() => {
        const link = document.querySelector("link[rel*='icon']");
        if (link) {
            const originalHref = link.getAttribute('href');
            link.setAttribute('href', '/favicon.svg');
            return () => {
                if (originalHref) link.setAttribute('href', originalHref);
            };
        }
    }, []);

    const isApproved = user?.is_approved || user?.role === 'guest' || ['super_admin', 'strata_admin', 'ec_member', 'strata_manager', 'admin_staff', 'real_estate_agent'].includes(user?.role || '') || isImpersonating;
    const hasBuilding = !!selectedBuilding;
    /**
     * @generated FunctionHeader
     * Function: getQuickGuidePath
     * Path: frontend/src/components/layout/DashboardLayout.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getQuickGuidePath = (role: string | undefined) => {
        const roleMap: Record<string, string> = {
            'super_admin': 'admin',
            'strata_admin': 'strata_admin',
            'ec_member': 'ec',
            'admin_staff': 'admin_staff',
            'owner': 'owner',
            'tenant': 'tenant',
            'strata_manager': 'strata_manager',
            'real_estate_agent': 'real_estate'
        };
        return `/user-guides/quick_role_${roleMap[role || 'owner'] || 'owner'}.html`;
    };

    const navItems = [
        {href: '/dashboard', label: 'Dashboard', icon: Home, show: isApproved},
        {
            href: '/owner-view',
            label: 'My Property View',
            icon: Building2,
            show: isApproved && hasBuilding && ['strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: getQuickGuidePath(user?.role),
            label: 'Quick Guide',
            icon: BookOpen,
            show: hasFeatureAccess('user_guide'),
            external: true
        },
        {
            href: siteSettings.unit_plan_url,
            label: 'Unit Plan',
            icon: ClipboardList,
            show: hasFeatureAccess('user_guide') && ['super_admin', 'strata_admin', 'ec_member', 'owner', 'strata_manager'].includes(user?.role || ''),
            external: true
        },
        {
            href: siteSettings.handbook_url,
            label: 'Resident Handbook',
            icon: BookOpen,
            show: hasFeatureAccess('user_guide'),
            external: true
        },
        {
            href: siteSettings.inclusions_url,
            label: 'Unit Inclusions',
            icon: ClipboardList,
            show: hasFeatureAccess('user_guide') && !!siteSettings.inclusions_url,
            external: true
        },
        {
            href: `mailto:${selectedBuilding?.name || 'Executive Committee'} <${siteSettings.ec_email}>`,
            label: 'Contact EC',
            icon: Mail,
            show: isApproved && !!siteSettings.ec_email,
            external: true
        },
        {
            href: '/community/notices',
            label: 'Notice Board',
            icon: ClipboardList,
            show: isApproved && hasFeatureAccess('notices'),
            badge: 'notices' as const,
        },
        {
            href: '/intelligence/levy-fairness',
            label: 'Levy Fairness',
            icon: ChartBarIcon,
            show: isApproved && (
                user?.role === 'super_admin' ||
                ((['strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')) && hasFeatureAccess('levy_fairness')) ||
                (user?.role === 'owner' && hasFeatureAccess('levy_fairness_owner')) ||
                user?.is_elevated ||
                !!user?.temp_elevation
            )
        },
        {
            href: '/intelligence/levy-scenarios',
            label: 'Levy Scenario Modeller',
            icon: SlidersHorizontal,
            show: isApproved && hasFeatureAccess('levy_scenario_modeller') && (
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member', 'owner'].includes(user?.role || '')
            )
        },
        {
            href: '/intelligence/capital-risk',
            label: 'Capital Shock Risk',
            icon: ShieldAlert,
            show: isApproved && (
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '') ||
                user?.is_elevated ||
                !!user?.temp_elevation
            )
        },
        {
            href: '/intelligence/global-risk',
            label: 'Global Risk Index',
            icon: Activity,
            show: isApproved && hasFeatureAccess('building_risk_index') && (
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '') ||
                user?.is_elevated ||
                !!user?.temp_elevation
            )
        },
        {
            href: '/intelligence/occupancy',
            label: 'Occupancy Intelligence',
            icon: Users,
            show: isApproved && hasFeatureAccess('occupancy_intelligence') && (
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '') ||
                user?.is_elevated ||
                !!user?.temp_elevation
            )
        },
        {
            href: '/intelligence/assets',
            label: 'Asset Intelligence',
            icon: Wrench,
            show: isApproved && (
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '') ||
                user?.is_elevated ||
                !!user?.temp_elevation
            )
        },
        {
            href: '/intelligence/market',
            label: 'ACT Market Intelligence',
            icon: Map,
            show: isApproved && hasFeatureAccess('strata_market_intelligence') && ['super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/community/events',
            label: 'Events Calendar',
            icon: CalendarDays,
            show: isApproved && hasFeatureAccess('events')
        },
        {
            href: '/community/chat',
            label: 'Community Chat',
            icon: MessageSquare,
            show: isApproved && hasPermission('can_chat') && hasFeatureAccess('chat')
        },
        {
            href: '/community/messages',
            label: 'Private Messages',
            icon: MessageCircle,
            show: isApproved && hasPermission('can_chat') && hasFeatureAccess('private_messages')
        },
        {
            href: '/community/directory',
            label: 'Resident Directory',
            icon: UserCircle,
            show: isApproved && hasFeatureAccess('directory') && (['owner', 'tenant', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager', 'real_estate_agent', 'admin_staff'].includes(user?.role || ''))
        },
        {
            href: '/documents',
            label: 'Document Library',
            icon: FileText,
            show: isApproved && hasPermission('can_view_documents') && hasFeatureAccess('documents')
        },
        {
            href: '/documents/converter',
            label: 'Document Converter',
            icon: FileDown,
            show: isApproved && hasPermission('can_view_documents')
        },
        {
            href: '/documents/ocr',
            label: 'Document OCR',
            icon: ScanText,
            show: isApproved && hasFeatureAccess('document_ocr') &&
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member', 'owner', 'tenant', 'real_estate_agent', 'admin_staff'].includes(user?.role || '')
        },
        {
            href: '/community/marketplace',
            label: 'Community Marketplace',
            icon: ShoppingBag,
            show: isApproved && hasFeatureAccess('marketplace')
        },
        {
            href: '/community/blog-management',
            label: 'Blog Management',
            icon: Newspaper,
            show: isApproved && hasFeatureAccess('blog_management') && ['owner', 'strata_admin', 'ec_member', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/community/my-listings',
            label: 'My Classifieds',
            icon: ClipboardList,
            show: isApproved && hasPermission('can_create_listings') && hasFeatureAccess('marketplace')
        },
        {
            href: '/community/bookings',
            label: 'Facility Bookings',
            icon: Truck,
            show: isApproved && hasFeatureAccess('bookings')
        },
        {
            href: '/maintenance',
            label: 'Maintenance Tracker',
            icon: Wrench,
            show: isApproved && hasFeatureAccess('maintenance')
        },
        // ── Requests ──────────────────────────────────────────────────────
        // Exactly ONE nav entry per href. navGroups resolves items with
        // navItems.find(i => i.href === ...), which returns the FIRST match — so a
        // second entry for the same href is dead config that only ever showed up
        // as a phantom duplicate when reading this list. There used to be four
        // request entries across two hrefs ('Support Requests' + 'My Request
        // Status' → /requests, 'Smart Requests' + 'Smart Request' → /requests/new).
        // Labels now match each page's own heading.
        {
            href: '/requests',
            label: 'Requests & Applications',
            icon: FileQuestion,
            // Gate deliberately UNCHANGED from the 'Support Requests' entry this
            // replaces. The removed 'My Request Status' entry gated on
            // request_status_tracking, but it shared this href and so never
            // rendered — folding its toggle in here would have widened access as a
            // side effect of a rename. (request_status_tracking also declares
            // depends_on: ['smart_requests'] in the toggle seed, and
            // hasFeatureAccess does not evaluate depends_on, so OR-ing them would
            // not have been equivalent either.) Residents already reach this entry
            // through the role list below.
            show: isApproved && hasFeatureAccess('smart_requests') && (hasPermission('can_manage_requests') || ['owner', 'tenant', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager', 'admin_staff'].includes(user?.role || ''))
        },
        {
            href: '/requests/new',
            label: 'Smart Request',
            icon: Zap,
            show: isApproved && hasFeatureAccess('smart_requests') && ['owner', 'tenant', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/community/by-law-breach',
            label: 'By-law Breaches',
            icon: Gavel,
            show: isApproved && hasFeatureAccess('by_law_breach') && (['owner', 'tenant', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || ''))
        },
        {
            href: '/maintenance/defects',
            label: 'Defect Reporting',
            icon: AlertTriangle,
            show: isApproved && hasFeatureAccess('defects') && (['owner', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || ''))
        },
        {
            href: '/community/parcels',
            label: 'Parcel Tracking',
            icon: Package,
            show: isApproved && hasFeatureAccess('parcels')
        },
        {
            href: '/governance/meetings',
            label: 'Committee Meetings',
            icon: Calendar,
            show: isApproved && hasPermission('can_view_meetings') && hasFeatureAccess('meetings')
        },
        {
            href: '/governance/meetings/notes',
            label: 'Meeting Notes',
            icon: FileText,
            show: isApproved && hasPermission('can_manage_meetings') && hasFeatureAccess('meetings')
        },
        {
            href: '/governance/decisions',
            label: 'Decision Register',
            icon: FileCheck,
            show: isApproved && hasFeatureAccess('decision_register') &&
                ['owner', 'tenant', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/requests/outstanding-issues',
            label: 'Outstanding Issues',
            icon: ClipboardList,
            show: isApproved && hasFeatureAccess('outstanding_issues_register') &&
                ['owner', 'tenant', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/governance/agm',
            label: 'Voting & AGM',
            icon: Vote,
            show: isApproved && hasFeatureAccess('meetings') && (['owner', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || ''))
        },
        {
            href: '/governance/todos',
            label: 'Action Items',
            icon: ClipboardList,
            show: isApproved && hasPermission('can_view_meetings') && hasFeatureAccess('meetings')
        },
        {
            href: '/compliance',
            label: 'Compliance Checklist',
            icon: ClipboardCheck,
            show: isApproved && hasFeatureAccess('compliance') && ['super_admin', 'strata_admin', 'ec_member', 'strata_manager', 'owner'].includes(user?.role || ''),
            badge: 'compliance' as const,
        },
        {
            href: '/community/pet-register',
            label: 'Pet Register',
            icon: Dog,
            show: isApproved
        },
        {
            href: '/building-info',
            label: 'Building Info & Rules',
            icon: Building2,
            show: isApproved
        },
        {
            href: '/requests/rental-certificates',
            label: 'Rental Certificates',
            icon: FileCheck,
            show: isApproved && hasFeatureAccess('rental_certificates') &&
                ['owner', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/governance/proposals',
            label: 'Proposals',
            icon: Vote,
            show: isApproved && hasFeatureAccess('proposals') &&
                ['owner', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/financials/savings',
            label: 'Savings Ledger',
            icon: PiggyBank,
            show: isApproved && hasFeatureAccess('savings') &&
                ['owner', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/community/volunteer',
            label: 'Volunteer',
            icon: Users,
            show: isApproved && hasFeatureAccess('volunteer') &&
                ['owner', 'tenant', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/intelligence/building-health',
            label: 'Building Health',
            icon: Activity,
            show: isApproved && hasFeatureAccess('building_health') &&
                ['owner', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/maintenance/schedule',
            label: 'Service Schedule',
            icon: Clock,
            show: isApproved && hasFeatureAccess('schedule') && (hasPermission('can_view_schedule') || user?.role === 'service_provider')
        },
        {
            href: '/financials/overview',
            label: 'Financial Overview',
            icon: DollarSign,
            show: isApproved && hasPermission('can_view_finances') && hasFeatureAccess('finance')
        },
        {
            href: '/financials/collection-rate',
            label: 'Collection Rate',
            icon: ChartBarIcon,
            show: isApproved && hasFeatureAccess('collection_rate') && ['super_admin', 'strata_manager', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/intelligence/projections',
            label: 'Budget Projections',
            icon: TrendingUp,
            show: isApproved && hasPermission('can_view_finances') && hasFeatureAccess('finance')
        },
        {
            href: '/intelligence/financial',
            label: 'Financial Intelligence',
            icon: Brain,
            show: isApproved && hasPermission('can_view_finances') &&
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/intelligence/bi',
            label: 'BI Analytics',
            icon: Database,
            show: isApproved && hasPermission('can_view_finances') && (
                hasFeatureAccess('bi_analytics_enabled') ||
                hasFeatureAccess('bi_analytics_building_enabled') ||
                hasFeatureAccess('bi_analytics_platform_enabled')
            ) &&
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(
                    (user as any)?.effective_role || user?.role || ''
                )
        },
        // Management hierarchy navigation — self-managed internal manager
        {
            href: '/management/my-building',
            label: 'My Managed Building',
            icon: Database,
            show: isApproved &&
                hasFeatureAccess('management_hierarchy_enabled') &&
                (user as any)?.management_entity_type === 'self_managed_oc' &&
                ['ec_member'].includes((user as any)?.effective_role || user?.role || '')
        },
        // Independent manager portfolio nav
        {
            href: '/management/portfolio',
            label: 'My Managed Buildings',
            icon: Database,
            show: isApproved &&
                hasFeatureAccess('management_hierarchy_enabled') &&
                (user as any)?.management_entity_type === 'independent_manager' &&
                ['strata_manager'].includes((user as any)?.effective_role || user?.role || '')
        },
        {
            href: '/financials/levy-payments',
            label: 'Levy Payments',
            icon: CreditCard,
            show: isApproved && hasFeatureAccess('finance') && (['owner', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || ''))
        },
        {
            href: '/financials/my-finances',
            label: 'My Finances',
            icon: DollarSign,
            show: isApproved && hasFeatureAccess('my_finances') && ['owner', 'tenant', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/intelligence/debt-recovery',
            label: 'Debt Recovery Board',
            icon: AlertTriangle,
            show: isApproved && hasFeatureAccess('finance') && hasPermission('can_manage_finances')
        },
        {
            href: '/financials/matching',
            label: 'Payment Matching',
            icon: GitBranch,
            show: isApproved && hasBuilding && hasFeatureAccess('bank_feeds_sync_enabled') && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin'].includes((user as any)?.effective_role || user?.role || '')
        },
        {
            href: '/financials/reconciliation',
            label: 'Finance Reconciliation',
            icon: ShieldCheck,
            show: isApproved && hasBuilding && hasFeatureAccess('bank_feeds_sync_enabled') && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin', 'ec_member'].includes((user as any)?.effective_role || user?.role || '')
        },
        {
            href: '/financials/historical-reconstruction',
            label: 'Historical Reconstruction',
            icon: History,
            // Page-level gate uses the broader "preparation" toggle/role set (strata_manager
            // can prepare batches); posting/approve/generate/sync/reverse/apply actions are
            // further gated inline by historical_reconstruction_posting + role, once on the page.
            show: isApproved && hasBuilding && hasFeatureAccess('historical_financial_reconstruction') && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin'].includes((user as any)?.effective_role || user?.role || '')
        },
        {
            href: '/admin/financial-onboarding',
            label: 'Financial Onboarding',
            icon: Upload,
            show: isApproved && hasBuilding && hasFeatureAccess('historical_financial_reconstruction') && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin'].includes((user as any)?.effective_role || user?.role || '')
        },
        {
            href: '/financials/trust-reconciliation',
            label: 'Trust Reconciliation',
            icon: RefreshCw,
            show: isApproved && hasBuilding && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin', 'treasurer'].includes(user?.role || '')
        },
        {
            href: '/financials/trust-bank-accounts',
            label: 'Bank Accounts & Interest',
            icon: Landmark,
            show: isApproved && hasFeatureAccess('trust_bank_accounts') && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin', 'ec_member', 'treasurer'].includes(user?.role || '')
        },
        {
            href: '/requests/my-approvals',
            label: 'Approval Queue',
            icon: ClipboardCheck,
            show: isApproved && hasFeatureAccess('approvals') && hasPermission('can_manage_finances'),
            badge: true
        },
        {
            href: '/financials/invoices',
            label: 'Invoices & Quotes',
            icon: Receipt,
            show: isApproved && hasFeatureAccess('invoice_ocr') && hasPermission('can_manage_finances') && isManager()
        },
        {
            href: '/requests/tenant-approvals',
            label: 'Tenant & Guest Approvals',
            icon: UserPlus,
            show: isApproved && hasFeatureAccess('tenant_approvals') && ['owner', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || ''),
            badge: 'tenantApprovals' as const,
        },
        {
            href: '/admin/users',
            label: 'Resident Management',
            icon: Users,
            show: isApproved && hasFeatureAccess('user_management') && isManager(),
            badge: 'userApprovals' as const,
        },
        {
            href: '/admin/staff-users',
            label: 'Staff User Management',
            icon: UserSearch,
            show: isApproved && isAdmin()
        },
        {
            href: '/admin/create-staff-user',
            label: 'Create Staff Account',
            icon: UserPlus,
            show: isApproved && hasBuilding && isManager()
        },
        {
            href: '/admin/owners-units',
            label: 'Owner Details',
            icon: Building2,
            show: isApproved && hasFeatureAccess('owner_units_management') && isManager()
        },
        {
            href: '/admin/change-requests',
            label: 'Administrative Requests',
            icon: RefreshCw,
            show: hasFeatureAccess('change_requests') && isManager()
        },
        {
            href: '/admin/owner-transfers',
            label: 'Ownership Transfers',
            icon: UserPlus,
            show: hasFeatureAccess('owner_transfers') &&
                (isManager() || user?.role === 'owner' || user?.role === 'real_estate_agent')
        },
        {
            href: '/admin/tenant-renewals',
            label: 'Lease Renewals',
            icon: RefreshCw,
            show: hasFeatureAccess('tenant_renewals') && isManager()
        },
        {
            href: '/admin/expired-accounts',
            label: 'Account Expiry',
            icon: UserX,
            show: hasFeatureAccess('expired_accounts') && isManager()
        },
        {
            href: '/admin/audit-logs',
            label: 'Activity Logs',
            icon: History,
            show: hasFeatureAccess('audit_logs') && isManager()
        },
        {
            href: '/admin',
            label: 'Portfolio Dashboard',
            icon: Building2,
            show: isApproved && hasFeatureAccess('portfolio_dashboard') && ['super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/admin/portfolio/onboard',
            label: 'Full Onboarding Wizard',
            icon: ClipboardCheck,
            show: isApproved && hasFeatureAccess('building_onboarding') && ['super_admin', 'strata_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/admin/buildings/onboard',
            label: 'Quick Building Setup',
            icon: Building2,
            show: isApproved && hasFeatureAccess('building_onboarding') && ['super_admin', 'strata_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/profile/passport',
            label: 'My Tenancy Passport',
            icon: FileCheck,
            show: isApproved && hasFeatureAccess('tenancy_passport') && ['tenant', 'owner', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/intelligence/suburb-radar',
            label: 'Suburb Radar',
            icon: Map,
            show: isApproved && hasFeatureAccess('suburb_radar')
        },
        {
            href: '/intelligence/safety-feed',
            label: 'Building Safety',
            icon: Shield,
            show: isApproved && hasFeatureAccess('safety_feed') && ['owner', 'tenant', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        {
            href: '/admin/workflows',
            label: 'Workflow Governance',
            icon: GitBranch,
            show: isApproved && hasFeatureAccess('workflow_governance') && ['super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/powerhouse',
            label: 'Powerhouse',
            icon: Brain,
            // 'chairman' is not a top-level role (see rules/post-compact-critical.md) — a
            // chairman is a user with role='ec_member', which must be listed directly or
            // real chairmen never see this nav item (PowerhouseControlCentrePage.tsx grants
            // them the page itself via isManager(), so the nav link must match).
            show: isApproved && ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/powerhouse/conversations',
            label: 'Conversation Centre',
            icon: MessageSquare,
            show: isApproved && hasFeatureAccess('powerhouse_conversations') && ['super_admin', 'strata_admin', 'strata_manager', 'ec_member', 'owner', 'tenant', 'guest'].includes(user?.role || '')
        },
        {
            href: '/powerhouse/automation',
            label: 'Workflow Automation',
            icon: GitBranch,
            show: isApproved && hasFeatureAccess('powerhouse_automation_rules') && ['super_admin', 'strata_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/powerhouse/inbox-settings',
            label: 'Shared Inbox',
            icon: Mail,
            show: isApproved && hasFeatureAccess('powerhouse_shared_inbox') && ['super_admin', 'strata_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/admin/cutover-status',
            label: 'Cutover Control',
            icon: GitBranch,
            show: isApproved && user?.role === 'super_admin'
        },
        {
            href: '/admin/assets',
            label: 'Asset Register',
            icon: ClipboardList,
            show: hasBuilding && isManager()
        },
        {
            href: '/admin/strata-roll',
            label: 'Strata Roll',
            icon: ClipboardList,
            show: isApproved && hasFeatureAccess('strata_roll') && isManager()
        },
        {
            href: '/admin/communications',
            label: 'Admin Communications',
            icon: Send,
            // isManager() covers super_admin, strata_admin, strata_manager and ec_member.
            // The previous inline list read raw user.role, which breaks twice: it omitted
            // strata_admin entirely, and an ELEVATED ec_member carries role "owner" on
            // their record, so the person most likely to need this page could not see it.
            // CLAUDE.md requires the AuthContext helpers for exactly this reason.
            show: isApproved && hasBuilding && isManager()
        },
        {
            href: '/admin/outbound-queue',
            label: 'Outgoing Messages',
            icon: Send,
            // Same operational audience as Admin Communications. Not gated on a feature
            // toggle: this is the only place held mail can be released, so it must stay
            // reachable even when messaging features are being switched off.
            show: isApproved && hasBuilding && isManager()
        },
        {
            href: '/notifications',
            label: 'System Broadcasts',
            icon: Megaphone,
            show: isApproved && hasFeatureAccess('notifications_management') && hasPermission('can_post_announcements')
        },
        {
            href: '/settings',
            label: 'Building Settings',
            icon: Settings,
            show: isApproved && hasFeatureAccess('site_settings') && ['super_admin', 'strata_manager', 'strata_admin'].includes(user?.role || '')
        },
        {
            href: '/admin/console',
            label: 'Legal & Privacy',
            icon: Shield,
            show: isApproved && hasBuilding && ['super_admin', 'strata_manager', 'strata_admin'].includes(user?.role || '')
        },
        {href: '/admin/feature-toggles', label: 'Feature Toggles', icon: ToggleLeft, show: isAdmin()},
        {href: '/admin/security-ip-logs', label: 'Security & IP Logs', icon: ShieldAlert, show: isAdmin()},
        {href: '/admin/scraper-settings', label: 'Scraper Settings', icon: RefreshCw, show: isAdmin()},
        {href: '/admin/strata-sync', label: 'Strata Portal Sync', icon: Database, show: isAdmin()},
        {
            href: '/admin/scheme-classes',
            label: 'Class A/B Scheme Split',
            icon: Layers,
            show: isApproved && hasFeatureAccess('scheme_class_split') && ['super_admin', 'strata_manager', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/financials/spending-categories',
            label: 'Spending Categories',
            icon: ClipboardList,
            show: isApproved && hasPermission('can_view_finances') && hasFeatureAccess('finance')
        },
        {
            href: '/financials/council-rates',
            label: 'Utilities & Property Services',
            icon: Landmark,
            show: isApproved && hasFeatureAccess('council_rates') && hasPermission('can_view_finances') && ['owner', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {
            href: '/financials/water-bills',
            label: 'Icon Water Bills',
            icon: Droplets,
            show: isApproved && hasFeatureAccess('water_bills') && hasPermission('can_view_finances') && ['owner', 'strata_admin', 'ec_member', 'super_admin', 'strata_manager'].includes(user?.role || '')
        },
        {href: '/admin/financial-data', label: 'Financial Data Upload', icon: DollarSign, show: hasBuilding && isManager()},
        {
            href: '/admin/financial-year-import',
            label: 'Financial Year Import',
            icon: Upload,
            show: isApproved && hasBuilding && ['super_admin', 'strata_manager', 'strata_admin'].includes(user?.role || '')
        },
        {
            href: '/admin/gst-bas-ledger',
            label: 'GST & BAS Ledger',
            icon: Receipt,
            show: isApproved && hasFeatureAccess('gst_bas_ledger') && hasPermission('can_manage_finances') && ['super_admin', 'strata_manager', 'strata_admin'].includes(user?.role || '')
        },
        {href: '/admin/user-permissions', label: 'Access Control', icon: Key, show: isAdmin()},
        {
            href: '/admin/user-feature-permissions',
            label: 'Feature Access Controls',
            icon: UserCog,
            show: isAdmin()
        },
        {href: '/profile', label: 'My Profile', icon: UserCircle, show: isApproved && !isImpersonating},
        {
            href: '/my-communications',
            label: 'Communications',
            icon: Mail,
            show: isApproved && (hasFeatureAccess('email_preferences') || (hasFeatureAccess('webmail') && hasPermission('can_access_email')))
        },
        {
            href: '/about',
            label: 'Manage EC Members',
            icon: Users,
            show: isApproved && hasBuilding && ['super_admin', 'strata_admin'].includes(user?.role || ''),
            external: false
        },
        {
            href: '/emergency-services',
            label: 'Manage Emergency Contacts',
            icon: Phone,
            show: isApproved && hasBuilding && ['super_admin', 'strata_admin', 'ec_member'].includes(user?.role || ''),
            external: false
        },
        // ── OwnerHub / Landlord Platform ───────────────────────────────────
        // chairman and ec_member inherit owner privileges (they own units too)
        // strata_manager and admin_staff are professionals — not property owners/investors
        {
            href: '/owner-hub',
            label: 'OwnerHub Overview',
            icon: Home,
            show: isApproved && hasFeatureAccess('owner_hub') &&
                ['owner', 'real_estate_agent', 'super_admin', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/owner-hub/properties',
            label: 'My Properties',
            icon: Building2,
            show: isApproved && hasFeatureAccess('owner_hub') &&
                ['owner', 'real_estate_agent', 'super_admin', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/owner-hub/ledger',
            label: 'Tenancies & Rent Ledger',
            icon: CreditCard,
            show: isApproved && hasFeatureAccess('owner_hub') &&
                ['owner', 'real_estate_agent', 'super_admin', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/owner-hub/inspections',
            label: 'Inspections',
            icon: ClipboardCheck,
            show: isApproved && hasFeatureAccess('owner_hub') &&
                ['owner', 'real_estate_agent', 'super_admin', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/owner-hub/health-score',
            label: 'Property Health Score',
            icon: Activity,
            show: isApproved && hasFeatureAccess('property_health_score') &&
                ['owner', 'real_estate_agent', 'super_admin', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        {
            href: '/owner-hub/tco',
            label: 'True Cost of Ownership',
            icon: Calculator,
            show: isApproved && hasFeatureAccess('true_cost_ownership') &&
                ['owner', 'real_estate_agent', 'super_admin', 'strata_admin', 'ec_member'].includes(user?.role || '')
        },
        // ── Tenant Maintenance Portal ──────────────────────────────────────
        {
            href: '/maintenance/tenant',
            label: 'Maintenance Requests',
            icon: Wrench,
            show: isApproved && hasFeatureAccess('tenant_maintenance_portal') &&
                ['tenant', 'owner', 'real_estate_agent', 'admin_staff', 'strata_manager', 'strata_admin', 'ec_member', 'super_admin'].includes(user?.role || '')
        },
        // ── Real Estate Agent Portal ───────────────────────────────────────
        {
            href: '/real-estate',
            label: 'Agent Dashboard',
            icon: Briefcase,
            show: isApproved && hasFeatureAccess('real_estate_agent_portal') &&
                ['real_estate_agent', 'super_admin'].includes(user?.role || '')
        },
        // ── Building Financial Stress ──────────────────────────────────────
        {
            href: '/intelligence/building-stress',
            label: 'Building Financial Stress',
            icon: BarChart2,
            show: isApproved && hasFeatureAccess('building_financial_stress') &&
                ['owner', 'strata_admin', 'ec_member', 'strata_manager', 'super_admin'].includes(user?.role || '')
        },
        // ── Phase 2: Investor Intelligence ─────────────────────────────────
        {
            href: '/intelligence/investor',
            label: 'Investor Intelligence',
            icon: TrendingUp,
            show: isApproved && hasFeatureAccess('investor_dashboard') &&
                ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')
        },
        // ── Phase 2: Insurance & Lending Signals ───────────────────────────
        {
            href: '/intelligence/insurance-lending',
            label: 'Insurance & Lending Signals',
            icon: ShieldPlus,
            show: isApproved && hasFeatureAccess('insurance_lending_hooks') &&
                ['super_admin', 'strata_admin', 'strata_manager'].includes(user?.role || '')
        },
        // ── External API Keys ──────────────────────────────────────────────
        {
            href: '/admin/api-keys',
            label: 'External API Keys',
            icon: Key,
            show: isAdmin() && hasFeatureAccess('external_api')
        },
        // ── Technical Documentation — opens in new tab like user guides ──────
        {
            href: '/tech-docs/index.html',
            label: 'Technical Documentation',
            icon: FileText,
            show: isAdmin(),
            external: true,
        },
        // ── SM Organisations (tenant management) ──────────────────────────
        {
            href: '/admin/sm-organisations',
            label: 'SM Organisations',
            icon: Layers,
            show: isAdmin()
        },
        // ── Trial Leads (walkthrough requests from marketing page) ─────────
        {
            href: '/admin/leads',
            label: 'Trial Leads',
            icon: UserPlus,
            show: isAdmin()
        },
        // ── Pages that previously had no menu entry at all ────────────────
        // Each was reachable only by typing its URL. Gates mirror the page's own
        // guard where it has one, and are otherwise deliberately narrow (a nav
        // entry must never be wider than the page it points at).
        {
            href: '/financials/ap-approval',
            label: 'AP Approval Queue',
            icon: Receipt,
            show: isApproved && hasFeatureAccess('ap_automation') && hasPermission('can_manage_finances') && isManager()
        },
        {
            href: '/financials/trust',
            label: 'Trust Accounting',
            icon: Landmark,
            show: isApproved && hasFeatureAccess('trust_accounting') && hasPermission('can_manage_finances') && isManager()
        },
        {
            href: '/financials/levy-kpi',
            label: 'Levy KPI Dashboard',
            icon: BarChart3,
            show: isApproved && hasFeatureAccess('levy_kpi_dashboard') && isManager()
        },
        {
            href: '/financials/reports',
            label: 'Financial Reports',
            icon: FileText,
            show: isApproved && hasFeatureAccess('financial_reporting') && isManager()
        },
        {
            href: '/financials/capital-funding',
            label: 'Capital Funding',
            icon: PiggyBank,
            show: isApproved && hasFeatureAccess('capital_funding_workspace') && isManager()
        },
        {
            href: '/financials/fund-collections-by-type',
            label: 'Fund Collections by Unit Type',
            icon: PieChart,
            show: isApproved && hasFeatureAccess('fund_collections_by_unit_type_report') && isManager()
        },
        {
            href: '/governance/ec-members',
            label: 'Executive Committee',
            icon: Users,
            // ECDashboard has NO client-side guard of its own and pulls building-wide
            // finance summaries, KPIs, sinking-fund forecasts and AGM data. An
            // isApproved gate here would put it in every tenant's sidebar on the
            // assumption that the backend 403s them — an assumption this nav entry
            // should not be making. isECMember() covers ec_member/super_admin/
            // strata_admin, which is who the page is actually for.
            show: isApproved && isECMember()
        },
        {
            href: '/governance/bylaws',
            // Read-only, searchable by-laws viewer with no privileged fetches —
            // every resident legitimately needs this one, so isApproved is correct.
            label: 'By-laws',
            icon: Scale,
            show: isApproved
        },
        // ── Super-admin platform tooling (isAdmin() only) ─────────────────
        {
            href: '/admin/demo-bank-transactions',
            label: 'Demo Bank Transactions',
            icon: Banknote,
            show: isApproved && isAdmin() && hasFeatureAccess('demo_bank_feed_enabled')
        },
        {
            href: '/admin/joint-owner-review',
            label: 'Joint Owner Review',
            icon: Users,
            show: isApproved && isAdmin()
        },
        {
            href: '/admin/jurisdictional-rules',
            label: 'Jurisdictional Rules',
            icon: Scale,
            show: isApproved && hasFeatureAccess('jurisdiction_rules') && (isAdmin() || isManager())
        },
        {
            href: '/admin/letters',
            label: 'Letter Templates',
            icon: Mail,
            show: isApproved && isAdmin()
        },
        {
            href: '/admin/mri-migration',
            label: 'MRI Migration',
            icon: GitBranch,
            show: isApproved && isAdmin()
        },
        {
            href: '/admin/tech-docs',
            label: 'Technical Docs Admin',
            icon: BookOpen,
            show: isApproved && isAdmin()
        },
        {
            href: '/intelligence/bi/platform',
            label: 'Platform BI Analytics',
            icon: BarChart3,
            show: isApproved && isAdmin() && hasFeatureAccess('bi_analytics_platform_enabled')
        },
    ];

    const userMenuShortcutItems = [
        {href: '/profile', label: 'Profile Settings', icon: Settings, show: isApproved && !isImpersonating},
        {href: '/dashboard', label: 'Dashboard', icon: Home, show: isApproved},
        {
            href: '/community/chat',
            label: 'Chat',
            icon: MessageSquare,
            show: isApproved && hasPermission('can_chat') && hasFeatureAccess('chat')
        },
        {
            href: '/documents',
            label: 'Documents',
            icon: FileText,
            show: isApproved && hasPermission('can_view_documents') && hasFeatureAccess('documents')
        },
        {
            href: '/community/my-listings',
            label: 'My Listings',
            icon: ShoppingBag,
            show: isApproved && hasPermission('can_create_listings') && hasFeatureAccess('marketplace')
        },
        {
            href: '/settings',
            label: 'Settings',
            icon: Settings,
            show: isRealAdmin() && isAdmin() && hasFeatureAccess('site_settings')
        },
    ];

    const navGroups = [
        // ── 1. Overview ────────────────────────────────────────────────────
        {
            id: 'overview',
            label: 'Overview',
            icon: LayoutDashboard,
            color: 'text-blue-500',
            bgColor: 'bg-blue-500/10',
            items: [
                navItems.find(i => i.href === '/dashboard'),
                navItems.find(i => i.href === '/owner-view'),
                navItems.find(i => i.label === 'Contact EC'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 2. Intelligence (managers/EC/chair only) ───────────────────────
        {
            id: 'intelligence',
            label: 'Intelligence',
            icon: Brain,
            color: 'text-rose-500',
            bgColor: 'bg-rose-500/10',
            items: [
                {
                    href: '/intelligence/building',
                    label: 'Building Intelligence',
                    icon: Brain,
                    show: isApproved && ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')
                },
                navItems.find(i => i.href === '/intelligence/assets'),
                {
                    href: '/intelligence/capital-planner',
                    label: 'Capital Works Planner',
                    icon: ClipboardList,
                    show: isApproved && ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')
                },
                navItems.find(i => i.href === '/intelligence/levy-fairness'),
                {
                    href: '/intelligence/levy-stability',
                    label: 'Levy Stability Score',
                    icon: Shield,
                    show: isApproved && ['super_admin', 'strata_admin', 'strata_manager', 'ec_member'].includes(user?.role || '')
                },
                navItems.find(i => i.href === '/intelligence/capital-risk'),
                navItems.find(i => i.href === '/intelligence/global-risk'),
                navItems.find(i => i.href === '/intelligence/occupancy'),
                navItems.find(i => i.href === '/intelligence/financial'),
                navItems.find(i => i.href === '/intelligence/bi'),
                navItems.find(i => i.href === '/intelligence/projections'),
                navItems.find(i => i.href === '/intelligence/investor'),
                navItems.find(i => i.href === '/intelligence/insurance-lending'),
                navItems.find(i => i.href === '/intelligence/market'),
                navItems.find(i => i.href === '/intelligence/building-stress'),
                navItems.find(i => i.href === '/intelligence/debt-recovery'),
                navItems.find(i => i.href === '/intelligence/building-health'),
                // Previously defined in navItems but absent from every group, so they
                // only ever appeared in the flat sidebar and in search.
                navItems.find(i => i.href === '/intelligence/levy-scenarios'),
                navItems.find(i => i.href === '/intelligence/safety-feed'),
                navItems.find(i => i.href === '/intelligence/suburb-radar'),
                navItems.find(i => i.href === '/intelligence/bi/platform'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 3. Financials ──────────────────────────────────────────────────
        {
            id: 'financials',
            label: 'Financials',
            icon: DollarSign,
            color: 'text-emerald-500',
            bgColor: 'bg-emerald-500/10',
            items: [
                navItems.find(i => i.href === '/financials/overview'),
                navItems.find(i => i.href === '/financials/my-finances'),
                navItems.find(i => i.href === '/financials/collection-rate'),
                navItems.find(i => i.href === '/financials/levy-payments'),
                navItems.find(i => i.href === '/financials/spending-categories'),
                navItems.find(i => i.href === '/financials/council-rates'),
                navItems.find(i => i.href === '/financials/water-bills'),
                navItems.find(i => i.href === '/financials/trust-reconciliation'),
                navItems.find(i => i.href === '/financials/trust-bank-accounts'),
                navItems.find(i => i.href === '/requests/my-approvals'),
                navItems.find(i => i.href === '/financials/levy-kpi'),
                navItems.find(i => i.href === '/financials/invoices'),
                navItems.find(i => i.href === '/financials/ap-approval'),
                navItems.find(i => i.href === '/financials/matching'),
                navItems.find(i => i.href === '/financials/reconciliation'),
                navItems.find(i => i.href === '/financials/trust'),
                navItems.find(i => i.href === '/financials/capital-funding'),
                navItems.find(i => i.href === '/financials/fund-collections-by-type'),
                navItems.find(i => i.href === '/financials/reports'),
                {
                    href: '/insurance/claims',
                    label: 'Insurance Claims',
                    icon: ShieldPlus,
                    show: isApproved && hasPermission('can_view_finances')
                },
                navItems.find(i => i.href === '/admin/financial-onboarding'),
                navItems.find(i => i.href === '/admin/financial-data'),
                navItems.find(i => i.href === '/admin/financial-year-import'),
                navItems.find(i => i.href === '/admin/gst-bas-ledger'),
                // The historical-reconstruction workbench is the last step of the
                // onboarding financial pipeline (import → reconstruct → post → reconcile);
                // it had no group entry, so it was unreachable from the grouped sidebar.
                navItems.find(i => i.href === '/financials/historical-reconstruction'),
                navItems.find(i => i.href === '/admin/demo-bank-transactions'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 4. Governance ──────────────────────────────────────────────────
        {
            id: 'governance',
            label: 'Governance',
            icon: ShieldCheck,
            color: 'text-purple-500',
            bgColor: 'bg-purple-500/10',
            items: [
                navItems.find(i => i.href === '/governance/meetings'),
                navItems.find(i => i.href === '/requests/outstanding-issues'),
                navItems.find(i => i.href === '/governance/agm'),
                navItems.find(i => i.href === '/governance/todos'),
                navItems.find(i => i.href === '/compliance'),
                navItems.find(i => i.href === '/requests/rental-certificates'),
                navItems.find(i => i.href === '/governance/proposals'),
                navItems.find(i => i.href === '/community/pet-register'),
                navItems.find(i => i.href === '/building-info'),
                navItems.find(i => i.href === '/governance/decisions'),
                navItems.find(i => i.href === '/governance/meetings/notes'),
                navItems.find(i => i.href === '/governance/ec-members'),
                navItems.find(i => i.href === '/governance/bylaws'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 5. Communication ───────────────────────────────────────────────
        {
            id: 'communication',
            label: 'Communication',
            icon: MessageSquare,
            color: 'text-green-500',
            bgColor: 'bg-green-500/10',
            items: [
                navItems.find(i => i.href === '/community/notices'),
                navItems.find(i => i.href === '/community/events'),
                navItems.find(i => i.href === '/community/chat'),
                navItems.find(i => i.href === '/community/messages'),
                navItems.find(i => i.href === '/admin/communications'),
                navItems.find(i => i.href === '/notifications'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 6. Community ───────────────────────────────────────────────────
        {
            id: 'community',
            label: 'Community',
            icon: Users,
            color: 'text-indigo-500',
            bgColor: 'bg-indigo-500/10',
            items: [
                navItems.find(i => i.href === '/community/directory'),
                navItems.find(i => i.href === '/documents'),
                navItems.find(i => i.href === '/documents/converter'),
                navItems.find(i => i.href === '/documents/ocr'),
                navItems.find(i => i.href === '/community/marketplace'),
                navItems.find(i => i.href === '/community/my-listings'),
                navItems.find(i => i.href === '/community/bookings'),
                navItems.find(i => i.href === '/community/blog-management'),
                navItems.find(i => i.href === '/community/volunteer'),
                // Building Health moved to the Intelligence group (2026-08-07) so its
                // menu placement matches its /intelligence/* URL.
                // Smart Request moved to the Maintenance group (2026-08-20) so it sits
                // beside /requests instead of being split across two menu sections.
            ].filter((i): i is any => i !== undefined)
        },
        // ── 7. Maintenance & Facilities ────────────────────────────────────
        {
            id: 'maintenance',
            label: 'Maintenance',
            icon: Wrench,
            color: 'text-orange-500',
            bgColor: 'bg-orange-500/10',
            items: [
                navItems.find(i => i.href === '/maintenance'),
                navItems.find(i => i.href === '/requests'),
                navItems.find(i => i.href === '/requests/new'),
                navItems.find(i => i.href === '/maintenance/defects'),
                navItems.find(i => i.href === '/community/parcels'),
                navItems.find(i => i.href === '/community/by-law-breach'),
                navItems.find(i => i.href === '/maintenance/schedule'),
                navItems.find(i => i.href === '/admin/assets'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 8. OwnerHub / Landlord Platform ───────────────────────────────
        {
            id: 'ownerhub',
            label: 'OwnerHub',
            icon: Home,
            color: 'text-teal-500',
            bgColor: 'bg-teal-500/10',
            items: [
                navItems.find(i => i.href === '/owner-hub'),
                navItems.find(i => i.href === '/owner-hub/properties'),
                navItems.find(i => i.href === '/owner-hub/ledger'),
                navItems.find(i => i.href === '/owner-hub/inspections'),
                navItems.find(i => i.href === '/owner-hub/health-score'),
                navItems.find(i => i.href === '/owner-hub/tco'),
                navItems.find(i => i.href === '/real-estate'),
                navItems.find(i => i.href === '/financials/savings'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 9. My Tenancy (tenant + owner context) ─────────────────────────
        {
            id: 'my_tenancy',
            label: 'My Tenancy',
            icon: ClipboardSignature,
            color: 'text-violet-500',
            bgColor: 'bg-violet-500/10',
            items: [
                navItems.find(i => i.href === '/maintenance/tenant'),
                // Rental Certificates also surfaces here, in the tenant's own context.
                // Gated to `tenant` ONLY: the Governance group's entry already covers
                // owner/strata_admin/ec_member/strata_manager/super_admin, so listing
                // `owner` here as well showed owners the same link in two groups.
                {
                    href: '/requests/rental-certificates',
                    label: 'Rental Certificates',
                    icon: FileCheck,
                    show: isApproved && hasFeatureAccess('rental_certificates') &&
                        user?.role === 'tenant'
                },
            ].filter((i): i is any => i !== undefined)
        },
        // ── 10. Management ─────────────────────────────────────────────────
        {
            id: 'management',
            label: 'Management',
            icon: UserCog,
            color: 'text-slate-500',
            bgColor: 'bg-slate-500/10',
            items: [
                navItems.find(i => i.href === '/requests/tenant-approvals'),
                navItems.find(i => i.href === '/admin/buildings/onboard'),
                navItems.find(i => i.href === '/admin/portfolio/onboard'),
                navItems.find(i => i.href === '/admin/users'),
                navItems.find(i => i.href === '/admin/owners-units'),
                navItems.find(i => i.href === '/admin/strata-roll'),
                navItems.find(i => i.href === '/about'),
                navItems.find(i => i.href === '/emergency-services'),
                navItems.find(i => i.href === '/admin/change-requests'),
                navItems.find(i => i.href === '/admin/owner-transfers'),
                navItems.find(i => i.href === '/admin/tenant-renewals'),
                navItems.find(i => i.href === '/admin/expired-accounts'),
                navItems.find(i => i.href === '/admin/staff-users'),
                navItems.find(i => i.href === '/admin/create-staff-user'),
                navItems.find(i => i.href === '/management/my-building'),
                navItems.find(i => i.href === '/management/portfolio'),
                navItems.find(i => i.href === '/powerhouse'),
                navItems.find(i => i.href === '/powerhouse/conversations'),
                navItems.find(i => i.href === '/powerhouse/automation'),
                navItems.find(i => i.href === '/powerhouse/inbox-settings'),
                navItems.find(i => i.href === '/admin/jurisdictional-rules'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 11. Building Config (chair/strata_manager/super_admin) ─────────
        {
            id: 'building_config',
            label: 'Building Config',
            icon: Settings,
            color: 'text-gray-500',
            bgColor: 'bg-gray-500/10',
            items: [
                navItems.find(i => i.href === '/settings'),
                {
                    href: '/settings/digital-twin',
                    label: 'Digital Twin Architecture',
                    icon: Building2,
                    show: isApproved && ['super_admin', 'strata_admin', 'strata_manager'].includes(user?.role || '')
                },
                navItems.find(i => i.href === '/admin/console'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 12. Platform Admin (super_admin only) ──────────────────────────
        {
            id: 'platform_admin',
            label: 'Platform Admin',
            icon: ShieldAlert,
            color: 'text-red-600',
            bgColor: 'bg-red-600/10',
            items: [
                navItems.find(i => i.href === '/admin'),
                // Onboarding entries deliberately live ONLY in the Management group.
                // They were listed here too, with the identical `show` gate resolved from
                // the same navItems object, so every super_admin saw 'Full Onboarding
                // Wizard' and 'Quick Building Setup' twice in the grouped sidebar.
                navItems.find(i => i.href === '/admin/workflows'),
                {
                    href: '/admin/buildings',
                    label: 'Building Control',
                    icon: Building2,
                    show: isAdmin()
                },
                navItems.find(i => i.href === '/admin/feature-toggles'),
                navItems.find(i => i.href === '/admin/user-permissions'),
                navItems.find(i => i.href === '/admin/user-feature-permissions'),
                navItems.find(i => i.href === '/admin/audit-logs'),
                navItems.find(i => i.href === '/admin/security-ip-logs'),
                navItems.find(i => i.href === '/admin/scraper-settings'),
                navItems.find(i => i.href === '/admin/strata-sync'),
                navItems.find(i => i.href === '/admin/api-keys'),
                navItems.find(i => i.href === '/tech-docs/index.html'),
                navItems.find(i => i.href === '/admin/sm-organisations'),
                navItems.find(i => i.href === '/admin/leads'),
                navItems.find(i => i.href === '/admin/cutover-status'),
                navItems.find(i => i.href === '/admin/scheme-classes'),
                navItems.find(i => i.href === '/admin/joint-owner-review'),
                navItems.find(i => i.href === '/admin/letters'),
                navItems.find(i => i.href === '/admin/mri-migration'),
                navItems.find(i => i.href === '/admin/tech-docs'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 13. Management Entities (super_admin) ─────────────────────────
        ...(isAdmin() && hasFeatureAccess('management_hierarchy_enabled') ? [{
            id: 'management_entities',
            label: 'Management Entities',
            icon: Database,
            color: 'text-teal-600',
            bgColor: 'bg-teal-600/10',
            items: [
                {
                    href: '/admin/management-entities',
                    label: 'All Entities',
                    icon: Database,
                    show: isAdmin(),
                    'data-testid': 'nav-management-entities',
                },
                {
                    href: '/admin/management-entities/agencies',
                    label: 'Agencies',
                    icon: Database,
                    show: isAdmin(),
                },
                {
                    href: '/admin/management-entities/independent',
                    label: 'Independent Managers',
                    icon: Database,
                    show: isAdmin(),
                },
                {
                    href: '/admin/management-entities/self-managed',
                    label: 'Self-Managed OCs',
                    icon: Database,
                    show: isAdmin(),
                },
                {
                    href: '/admin/analytics',
                    label: 'Platform Analytics',
                    icon: Database,
                    show: isAdmin() && (
                        hasFeatureAccess('bi_analytics_platform_enabled') ||
                        hasFeatureAccess('bi_analytics_enabled')
                    ),
                },
            ] as any[],
        }] : []),
        // ── 14. Help & Resources ───────────────────────────────────────────
        {
            id: 'help',
            label: 'Help & Resources',
            icon: BookOpen,
            color: 'text-amber-500',
            bgColor: 'bg-amber-500/10',
            items: [
                navItems.find(i => i.label === 'Quick Guide'),
                navItems.find(i => i.label === 'Resident Handbook'),
                navItems.find(i => i.label === 'Unit Inclusions'),
                navItems.find(i => i.label === 'Unit Plan'),
            ].filter((i): i is any => i !== undefined)
        },
        // ── 14. Personal ───────────────────────────────────────────────────
        {
            id: 'personal',
            label: 'Personal',
            icon: UserCircle,
            color: 'text-cyan-500',
            bgColor: 'bg-cyan-500/10',
            items: [
                navItems.find(i => i.href === '/profile'),
                navItems.find(i => i.href === '/my-communications'),
                navItems.find(i => i.href === '/profile/passport'),
            ].filter((i): i is any => i !== undefined)
        }
    ];
    /**
     * @generated FunctionHeader
     * Function: handleLogout
     * Path: frontend/src/components/layout/DashboardLayout.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleLogout = async () => {
        try {
            await nextAuthSignOut({redirect: false});
        } finally {
            logout();
            router.replace('/');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: UserMenuShortcutItems
     * Path: frontend/src/components/layout/DashboardLayout.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const UserMenuShortcutItems = ({onNavigate}: { onNavigate?: () => void }) => (
        <>
            {userMenuShortcutItems.filter(item => item.show).map(item => (
                <DropdownMenuItem
                    key={item.href}
                    onClick={() => {
                        router.push(item.href);
                        onNavigate?.();
                    }}
                >
                    <item.icon className="mr-2 h-4 w-4"/>
                    {item.label}
                </DropdownMenuItem>
            ))}
        </>
    );
    /**
     * @generated FunctionHeader
     * Function: UserMenuModeItems
     * Path: frontend/src/components/layout/DashboardLayout.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const UserMenuModeItems = () => {
        const {mode, setMode} = useNavigation();
        if (!hasFeatureAccess('progressive_nav') || !hasFeatureAccess('nav_customisation')) {
            return null;
        }

        const menuModes: Array<{ value: "simple" | "advanced" | "classic"; label: string }> = [
            {value: "simple", label: "Simple"},
            {value: "advanced", label: "Advanced"},
            {value: "classic", label: "Classic"},
        ];

        return (
            <>
                <DropdownMenuSeparator/>
                <DropdownMenuLabel>Menu style</DropdownMenuLabel>
                {menuModes.map(menuMode => (
                    <DropdownMenuItem key={menuMode.value} onClick={() => setMode(menuMode.value)}>
                        <LayoutDashboard className="mr-2 h-4 w-4"/>
                        {menuMode.label}
                        {mode === menuMode.value && (
                            <span className="ml-auto text-xs text-primary">Current</span>
                        )}
                    </DropdownMenuItem>
                ))}
                <DropdownMenuItem onClick={() => setCustomiseDrawerOpen(true)}>
                    <Settings className="mr-2 h-4 w-4"/>
                    Customise menu
                </DropdownMenuItem>
            </>
        );
    };
    /**
     * @generated FunctionHeader
     * Function: NavContent
     * Path: frontend/src/components/layout/DashboardLayout.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const NavContent = () => {
        const useRedesignedSidebar = hasFeatureAccess('sidebar');
        const {mode: navMode} = useNavigation();
        const customisationEnabled = hasFeatureAccess('nav_customisation');
        const isClassicMode = !customisationEnabled || navMode === "classic";

        // ── Classic mode semantic search — state lives here so keystrokes
        //    only re-render NavContent, not the parent DashboardLayout.
        //    (Defining NavContent inline means a new fn ref on every parent
        //    render; keeping search state here prevents parent re-renders
        //    during typing, avoiding the unmount→remount→focus-loss cycle.)
        //    CLASSIC_SYNONYMS is defined at module level to avoid recreation.
        const [classicSearchQuery, setClassicSearchQuery] = useState("");
        const classicSearchResults = useMemo(() => {
            const q = classicSearchQuery.toLowerCase().trim();
            if (!q) return null;
            const allVisible = navItems.filter(i => i.show);
            const scored = allVisible.map(item => {
                const label = item.label.toLowerCase();
                const href = item.href?.toLowerCase() ?? "";
                let score = 0;
                if (label === q) score = 100;
                else if (label.startsWith(q)) score = 85;
                else if (label.includes(q)) score = 70;
                else if (href.includes(q)) score = 55;
                else {
                    const qTokens = q.split(/\s+/);
                    const labelTokens = label.split(/\s+/);
                    const overlap = qTokens.filter(t => labelTokens.some(lt => lt.includes(t))).length;
                    if (overlap > 0) score = 30 + (overlap / qTokens.length) * 20;
                    else {
                        const expansions = qTokens.flatMap(t => CLASSIC_SYNONYMS[t] ?? []);
                        if (expansions.some(e => label.includes(e) || href.includes(e))) score = 25;
                    }
                }
                return {item, score};
            });
            return scored.filter(s => s.score > 0).sort((a, b) => b.score - a.score).map(s => s.item);
        }, [classicSearchQuery]);

        return (
            <div className="relative flex flex-col h-full bg-background overflow-visible">
                {/* Logo and Collapse Button */}
                <div className="p-4 border-b border-border">
                    <div className="flex items-center justify-between">
                        <Link href="/"
                              className={`flex items-center gap-2 hover:opacity-80 transition-opacity ${sidebarCollapsed ? 'hidden lg:flex lg:justify-center lg:w-full' : ''}`}>
                            <Building2 className="h-8 w-8 text-primary"/>
                            {!sidebarCollapsed && (
                                <>
                                    <span className="font-bold text-lg">StrataOS</span>
                                    <Image src="/eastgate-logo.png" alt="StrataOS Logo" width={24} height={24}
                                           className="h-6 w-6"/>
                                </>
                            )}
                        </Link>
                        {sidebarCollapsed && (
                            <Link href="/"
                                  className="hidden lg:flex items-center justify-center w-full hover:opacity-80 transition-opacity">
                                <Building2 className="h-8 w-8 text-primary"/>
                            </Link>
                        )}
                        {/* Desktop collapse button */}
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
                                    className="hidden lg:flex h-8 w-8"
                                    data-testid="desktop-sidebar-toggle"
                                    aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                                >
                                    {sidebarCollapsed ? <ChevronRight className="h-5 w-5"/> :
                                        <ChevronLeft className="h-5 w-5"/>}
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent side="right">
                                {sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                            </TooltipContent>
                        </Tooltip>
                    </div>
                </div>

                {/* Building Switcher */}
                {!sidebarCollapsed && (
                    <div className="px-3 py-2 border-b border-border space-y-2">
                        <BuildingSwitcher/>
                        <UnitSwitcher/>
                    </div>
                )}

                {/* Navigation */}
                <ScrollArea className="flex-1 py-4">
                    <nav className="px-3 space-y-1">
                        {/* Progressive Navigation — shows when feature is enabled AND mode is not classic */}
                        {hasFeatureAccess('progressive_nav') && customisationEnabled && !isClassicMode && (
                            <div className="mb-4 pb-4 border-b border-border/50">
                                <SidebarNav
                                    collapsed={sidebarCollapsed}
                                    onOpenCustomise={customisationEnabled ? () => setCustomiseDrawerOpen(true) : undefined}
                                />
                            </div>
                        )}

                        {/* Public Links in Sidebar (Mobile & Desktop) — collapsed by default */}
                        <div className="mb-4 pb-4 border-b border-border/50">
                            {!sidebarCollapsed ? (
                                <button
                                    className="w-full flex items-center justify-between px-3 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1 hover:text-foreground transition-colors"
                                    onClick={() => setOpenGroups(prev => ({...prev, public_site: !prev.public_site}))}
                                >
                                    <span>Public Site</span>
                                    {openGroups.public_site ? <ChevronLeft className="h-3 w-3"/> :
                                        <ChevronRight className="h-3 w-3"/>}
                                </button>
                            ) : <div className="h-2"/>}
                            {(openGroups.public_site || sidebarCollapsed) && publicNavItems.map((item) => (
                                <SidebarTooltip key={item.href} label={item.label} show={sidebarCollapsed}>
                                    <Link
                                        href={item.href}
                                        onClick={() => setSidebarOpen(false)}
                                        className={`nav-item ${pathname === item.href ? 'active' : ''} ${sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''}`}
                                        aria-label={sidebarCollapsed ? item.label : undefined}
                                        data-testid={`sidebar-public-${item.label.toLowerCase()}`}
                                        aria-current={pathname === item.href ? 'page' : undefined}
                                    >
                                        <item.icon className="h-5 w-5 flex-shrink-0"/>
                                        {!sidebarCollapsed && <span>{item.label}</span>}
                                    </Link>
                                </SidebarTooltip>
                            ))}
                        </div>

                        {/* Classic mode OR progressive_nav disabled → show old grouped sections */}
                        {(!hasFeatureAccess('progressive_nav') || isClassicMode) && (<>

                            {/* Search box — hidden when sidebar is collapsed */}
                            {!sidebarCollapsed && (
                                <div className="relative mb-3 px-1" role="search">
                                    <Search
                                        className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none"
                                        aria-hidden="true"
                                    />
                                    <input
                                        type="search"
                                        value={classicSearchQuery}
                                        onChange={e => setClassicSearchQuery(e.target.value)}
                                        onKeyDown={e => {
                                            if (e.key === 'Escape') {
                                                setClassicSearchQuery("");
                                            }
                                        }}
                                        placeholder="Search menu…"
                                        aria-label="Search navigation menu"
                                        autoComplete="off"
                                        className="w-full rounded-md border border-border bg-muted/30 py-1.5 pl-8 pr-7 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 focus:border-ring"
                                    />
                                    {classicSearchQuery && (
                                        <button
                                            onClick={() => setClassicSearchQuery("")}
                                            aria-label="Clear search"
                                            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
                                        >
                                            <X className="h-3.5 w-3.5"/>
                                        </button>
                                    )}
                                </div>
                            )}

                            {/* Search results — replaces grouped nav while query is active */}
                            {classicSearchResults !== null ? (
                                classicSearchResults.length > 0 ? (
                                    <div className="space-y-1">
                                        {classicSearchResults.map(item => (
                                            item.external ? (
                                                <a
                                                    key={item.href}
                                                    href={item.href}
                                                    target={item.href.startsWith('mailto:') ? undefined : "_blank"}
                                                    rel={item.href.startsWith('mailto:') ? undefined : "noopener noreferrer"}
                                                    onClick={() => {
                                                        setSidebarOpen(false);
                                                        setClassicSearchQuery("");
                                                    }}
                                                    className="nav-item text-sm"
                                                    data-testid={`sidebar-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                                                >
                                                    <item.icon className="h-4 w-4 flex-shrink-0"/>
                                                    {!sidebarCollapsed && <span>{item.label}</span>}
                                                </a>
                                            ) : (
                                                <Link
                                                    key={item.href}
                                                    href={item.href}
                                                    onClick={() => {
                                                        setSidebarOpen(false);
                                                        setClassicSearchQuery("");
                                                    }}
                                                    className={`nav-item text-sm ${pathname === item.href ? 'active' : ''}`}
                                                    aria-current={pathname === item.href ? 'page' : undefined}
                                                    data-testid={`sidebar-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                                                >
                                                    <item.icon className="h-4 w-4 flex-shrink-0"/>
                                                    {!sidebarCollapsed && <span>{item.label}</span>}
                                                </Link>
                                            )
                                        ))}
                                    </div>
                                ) : (
                                    <p className="px-3 py-4 text-center text-xs text-muted-foreground" role="status">
                                        No results for &ldquo;{classicSearchQuery}&rdquo;
                                    </p>
                                )
                            ) : (
                                <>
                                    <p className="px-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                                        {!sidebarCollapsed ? 'Dashboard' : ''}
                                    </p>

                                    {useRedesignedSidebar ? (
                                        <div className="space-y-4">
                                            {navGroups.map(group => {
                                                const visibleItems = group.items.filter(item => item.show);
                                                if (visibleItems.length === 0) return null;

                                                const isExpanded = group.id === 'overview' || openGroups[group.id];

                                                return (
                                                    <div key={group.id} className="space-y-1">
                                                        <SidebarTooltip label={group.label} show={sidebarCollapsed}>
                                                            <button
                                                                onClick={() => toggleGroup(group.id)}
                                                                className={`w-full flex items-center justify-between px-3 py-2 rounded-md hover:bg-muted/50 transition-colors ${isExpanded ? 'bg-muted/30' : ''}`}
                                                                aria-label={group.label}
                                                                aria-expanded={isExpanded}
                                                            >
                                                                <div className="flex items-center gap-2">
                                                                    <div className={`p-1 rounded ${group.bgColor}`}>
                                                                        <group.icon
                                                                            className={`h-4 w-4 ${group.color}`}/>
                                                                    </div>
                                                                    {!sidebarCollapsed && (
                                                                        <span
                                                                            className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                                  {group.label}
                                </span>
                                                                    )}
                                                                </div>
                                                                {!sidebarCollapsed && group.id !== 'overview' && (
                                                                    <ChevronDown
                                                                        className={`h-3 w-3 transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`}/>
                                                                )}
                                                            </button>
                                                        </SidebarTooltip>

                                                        {(isExpanded || sidebarCollapsed) && (
                                                            <div
                                                                className={`${sidebarCollapsed ? '' : 'pl-2'} space-y-1`}>
                                                                {visibleItems.map(item => (
                                                                    item.external ? (
                                                                        <SidebarTooltip key={item.href}
                                                                                        label={item.label}
                                                                                        show={sidebarCollapsed}>
                                                                            <a
                                                                                href={item.href}
                                                                                target={item.href.startsWith('mailto:') ? undefined : "_blank"}
                                                                                rel={item.href.startsWith('mailto:') ? undefined : "noopener noreferrer"}
                                                                                onClick={() => setSidebarOpen(false)}
                                                                                className={`nav-item text-sm ${sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''}`}
                                                                                aria-label={sidebarCollapsed ? item.label : undefined}
                                                                                data-testid={`sidebar-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                                                                            >
                                                                                <item.icon
                                                                                    className="h-4 w-4 flex-shrink-0"/>
                                                                                {!sidebarCollapsed &&
                                                                                    <span>{item.label}</span>}
                                                                            </a>
                                                                        </SidebarTooltip>
                                                                    ) : (
                                                                        <SidebarTooltip key={item.href}
                                                                                        label={item.label}
                                                                                        show={sidebarCollapsed}>
                                                                            <Link
                                                                                href={item.href}
                                                                                onClick={() => setSidebarOpen(false)}
                                                                                className={`nav-item text-sm ${pathname === item.href ? 'active' : ''} ${sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''} ${item.badge ? 'relative' : ''}`}
                                                                                aria-label={sidebarCollapsed ? item.label : undefined}
                                                                                data-testid={`sidebar-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                                                                                aria-current={pathname === item.href ? 'page' : undefined}
                                                                            >
                                                                                <item.icon
                                                                                    className="h-4 w-4 flex-shrink-0"/>
                                                                                {!sidebarCollapsed &&
                                                                                    <span>{item.label}</span>}
                                                                                {item.badge && !sidebarCollapsed && (() => {
                                                                                    const count = item.badge === 'notices' ? noticesCount
                                                                                        : item.badge === 'compliance' ? overdueComplianceCount
                                                                                            : item.badge === 'tenantApprovals' ? tenantApprovalsCount
                                                                                                : item.badge === 'userApprovals' ? userApprovalsCount
                                                                                                    : pendingApprovalsCount;
                                                                                    return count > 0 ? (
                                                                                        <Badge
                                                                                            variant="destructive"
                                                                                            className="ml-auto h-4 min-w-4 px-1 flex items-center justify-center text-[9px] font-semibold"
                                                                                            aria-label={`${count} ${item.badge === 'notices' ? 'notices' : item.badge === 'tenantApprovals' ? 'pending approvals' : item.badge === 'userApprovals' ? 'pending user approvals' : 'items'}`}
                                                                                        >
                                                                                            {count > 99 ? '99+' : count}
                                                                                        </Badge>
                                                                                    ) : null;
                                                                                })()}
                                                                            </Link>
                                                                        </SidebarTooltip>
                                                                    )
                                                                ))}
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        navItems.filter(item => item.show).map((item) => (
                                            item.external ? (
                                                <SidebarTooltip key={item.href} label={item.label}
                                                                show={sidebarCollapsed}>
                                                    <a
                                                        href={item.href}
                                                        target={item.href.startsWith('mailto:') ? undefined : "_blank"}
                                                        rel={item.href.startsWith('mailto:') ? undefined : "noopener noreferrer"}
                                                        onClick={() => setSidebarOpen(false)}
                                                        className={`nav-item ${sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''}`}
                                                        aria-label={sidebarCollapsed ? item.label : undefined}
                                                        data-testid={`sidebar-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                                                    >
                                                        <item.icon className="h-5 w-5 flex-shrink-0"/>
                                                        {!sidebarCollapsed && <span>{item.label}</span>}
                                                    </a>
                                                </SidebarTooltip>
                                            ) : (
                                                <SidebarTooltip key={item.href} label={item.label}
                                                                show={sidebarCollapsed}>
                                                    <Link
                                                        href={item.href}
                                                        onClick={() => setSidebarOpen(false)}
                                                        className={`nav-item ${pathname === item.href ? 'active' : ''} ${sidebarCollapsed ? 'lg:justify-center lg:px-2' : ''} ${item.badge ? 'relative' : ''}`}
                                                        aria-label={sidebarCollapsed ? item.label : undefined}
                                                        data-testid={`sidebar-${item.label.toLowerCase().replace(/\s+/g, '-')}`}
                                                        aria-current={pathname === item.href ? 'page' : undefined}
                                                    >
                                                        <item.icon className="h-5 w-5 flex-shrink-0"/>
                                                        {!sidebarCollapsed && <span>{item.label}</span>}
                                                        {item.badge && !sidebarCollapsed && (() => {
                                                            const count = item.badge === 'notices' ? noticesCount
                                                                : item.badge === 'compliance' ? overdueComplianceCount
                                                                    : item.badge === 'tenantApprovals' ? tenantApprovalsCount
                                                                        : item.badge === 'userApprovals' ? userApprovalsCount
                                                                            : pendingApprovalsCount;
                                                            return count > 0 ? (
                                                                <Badge
                                                                    variant="destructive"
                                                                    className="ml-auto h-5 min-w-5 px-1 flex items-center justify-center text-[10px] font-semibold"
                                                                    aria-label={`${count} ${item.badge === 'notices' ? 'notices' : item.badge === 'tenantApprovals' ? 'pending approvals' : item.badge === 'userApprovals' ? 'pending user approvals' : 'items'}`}
                                                                >
                                                                    {count > 99 ? '99+' : count}
                                                                </Badge>
                                                            ) : null;
                                                        })()}
                                                    </Link>
                                                </SidebarTooltip>
                                            )
                                        ))
                                    )}
                                    {/* Classic mode: show "Switch View" button so user can escape to Simple/Advanced */}
                                    {isClassicMode && customisationEnabled && hasFeatureAccess('progressive_nav') && !sidebarCollapsed && (
                                        <div className="mt-3 pt-3 border-t border-border/40">
                                            <button
                                                onClick={() => setCustomiseDrawerOpen(true)}
                                                className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent/50 rounded-md transition-colors"
                                                data-testid="sidebar-switch-view-btn"
                                            >
                                                <LayoutDashboard className="h-3.5 w-3.5 flex-shrink-0"/>
                                                <span>Switch menu view</span>
                                            </button>
                                        </div>
                                    )}
                                </>
                            )} {/* end search results ternary */}
                        </>)}
                    </nav>
                </ScrollArea>

                {/* User section */}
                <div className="p-4 border-t border-border">
                    {sidebarCollapsed ? (
                        <>
                            <div className="hidden lg:flex flex-col items-center gap-3 mb-3">
                                <Avatar className="h-10 w-10">
                                    <AvatarImage src={user?.profile_image} alt={user?.full_name}/>
                                    <AvatarFallback className="bg-primary text-primary-foreground">
                                        {getInitials(user?.full_name)}
                                    </AvatarFallback>
                                </Avatar>
                            </div>
                            <SidebarTooltip label="Emergency" show={sidebarCollapsed}>
                                <Link
                                    href="/emergency-services"
                                    className="hidden lg:flex w-full justify-center items-center p-2 mb-2 rounded-md text-orange-600 hover:bg-orange-50 transition-colors"
                                    data-testid="sidebar-emergency-collapsed"
                                >
                                    <Phone className="h-4 w-4"/>
                                </Link>
                            </SidebarTooltip>
                            <SidebarTooltip label="Sign Out" show={sidebarCollapsed}>
                                <Button
                                    variant="outline"
                                    size="icon"
                                    className="hidden lg:flex w-full justify-center text-destructive hover:text-destructive hover:bg-destructive/10"
                                    onClick={handleLogout}
                                    data-testid="sidebar-logout-btn"
                                >
                                    <LogOut className="h-4 w-4"/>
                                </Button>
                            </SidebarTooltip>
                        </>
                    ) : (
                        <>
                            <div className="flex items-center gap-3 mb-3">
                                <Avatar className="h-10 w-10">
                                    <AvatarImage src={user?.profile_image} alt={user?.full_name}/>
                                    <AvatarFallback className="bg-primary text-primary-foreground">
                                        {getInitials(user?.full_name)}
                                    </AvatarFallback>
                                </Avatar>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium truncate">{user?.full_name}</p>
                                    <p className="text-xs text-muted-foreground truncate">
                                        {user?.role ? roleDisplayNames[user.role as keyof typeof roleDisplayNames] : ''}
                                        {user?.ec_position && user.ec_position !== 'MEMBER' && ecPositionDisplayNames[user.ec_position]
                                            ? ` · ${ecPositionDisplayNames[user.ec_position]}`
                                            : ''}
                                    </p>
                                    {user?.unit_number && (
                                        <p className="text-xs text-muted-foreground/70 truncate">Unit {user.unit_number}</p>
                                    )}
                                </div>
                            </div>
                            <Link
                                href="/emergency-services"
                                className="flex items-center gap-2 w-full px-3 py-2 mb-2 rounded-md text-sm font-medium text-orange-600 hover:bg-orange-50 transition-colors"
                                data-testid="sidebar-emergency-link"
                            >
                                <Phone className="h-4 w-4 flex-shrink-0"/>
                                <span>Emergency</span>
                            </Link>
                            <Button
                                variant="outline"
                                className="w-full justify-start text-destructive hover:text-destructive hover:bg-destructive/10"
                                onClick={handleLogout}
                                data-testid="sidebar-logout-btn"
                            >
                                <LogOut className="mr-2 h-4 w-4"/>
                                Sign Out
                            </Button>
                        </>
                    )}
                </div>
                {/* Floating expand tab — visible only when sidebar is collapsed, positioned at right edge */}
                {sidebarCollapsed && (
                    <button
                        onClick={() => setSidebarCollapsed(false)}
                        className="hidden lg:flex absolute -right-3 top-1/2 -translate-y-1/2 z-50 items-center justify-center w-6 h-10 bg-primary text-primary-foreground rounded-r-lg shadow-md hover:bg-primary/90 transition-colors"
                        aria-label="Expand sidebar"
                        data-testid="sidebar-expand-tab"
                    >
                        <ChevronRight className="h-4 w-4"/>
                    </button>
                )}
            </div>
        );
    };

    return (
        <NavigationProvider>
            <div className="min-h-screen bg-background" data-testid="dashboard-layout">
                <ImpersonationBanner/>
                {/* Mobile sidebar overlay */}
                {sidebarOpen && (
                    <div
                        className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden"
                        onClick={() => setSidebarOpen(false)}
                    />
                )}

                {/* Sidebar */}
                <aside
                    className={`fixed inset-y-0 left-0 z-50 w-[min(88vw,20rem)] max-w-full bg-background border-r border-border transition-all duration-300 ease-in-out ${
                        sidebarOpen ? 'translate-x-0' : '-translate-x-full'
                    } lg:translate-x-0 ${sidebarCollapsed ? 'lg:w-20' : 'lg:w-64'}`}
                    data-testid="dashboard-sidebar"
                >
                    <NavContent/>
                </aside>

                {/* Main content */}
                <div className={`min-w-0 ${sidebarCollapsed ? 'lg:pl-20' : 'lg:pl-64'} transition-all duration-300`}>
                    {/* Top header */}
                    <header className="glass-header lg:hidden">
                        <div className="h-16 min-w-0 px-3 flex items-center justify-between gap-2 sm:px-4">
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => setSidebarOpen(true)}
                                data-testid="mobile-sidebar-toggle"
                                aria-label="Open sidebar"
                            >
                                <Menu className="h-6 w-6"/>
                            </Button>

                            <Link href="/" className="flex min-w-0 flex-1 items-center justify-center gap-2">
                                {selectedBuilding?.building_id === DEFAULT_BUILDING_ID ? (
                                    <Image src="/eastgate-logo.png" alt={`${selectedBuilding.name} Logo`} width={32} height={32}
                                           className="h-8 w-8"/>
                                ) : (
                                    <>
                                        <Building2 className="h-8 w-8 text-primary"/>
                                        <span className="hidden min-w-0 truncate text-base font-bold sm:block">{selectedBuilding?.name || 'Building'}</span>
                                    </>
                                )}
                            </Link>

                            <div className="flex shrink-0 items-center gap-1 sm:gap-2">
                                {/* Mobile Notifications */}
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="ghost" size="icon" className="relative"
                                                aria-label="Notifications">
                                            <Bell className="h-5 w-5"/>
                                            {unreadCount > 0 && (
                                                <span
                                                    className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] font-medium text-destructive-foreground">
                        {unreadCount > 9 ? '9+' : unreadCount}
                      </span>
                                            )}
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent className="w-[min(18rem,calc(100vw-1rem))]" align="end">
                                        <DropdownMenuLabel className="flex items-center justify-between gap-2">
                                            <span>Notifications</span>
                                            {unreadCount > 0 && (
                                                <Button variant="ghost" size="sm"
                                                        className="h-auto p-0 text-xs text-primary"
                                                        onClick={markAllRead}>
                                                    Mark all as read
                                                </Button>
                                            )}
                                        </DropdownMenuLabel>
                                        <DropdownMenuSeparator/>
                                        <ScrollArea className="h-64">
                                            {notifications.length > 0 ? (
                                                notifications.map((notif) => (
                                                    <DropdownMenuItem
                                                        key={notif.id}
                                                        className={`flex flex-col items-start p-3 cursor-pointer ${!notif.is_read ? 'bg-primary/5' : 'opacity-70'}`}
                                                        onClick={() => {
                                                            markNotificationRead(notif.id);
                                                            if (notif.link) router.push(notif.link);
                                                            setSidebarOpen(false);
                                                        }}
                                                    >
                                                        <div className="flex w-full min-w-0 justify-between gap-2 items-center mb-1">
                                                            <span className="min-w-0 break-words font-semibold text-xs">{notif.title}</span>
                                                            {!notif.is_read && <Badge
                                                                className="h-1.5 w-1.5 p-0 rounded-full bg-primary"/>}
                                                        </div>
                                                        <p className="text-[10px] text-muted-foreground line-clamp-2">
                                                            {notif.message}
                                                        </p>
                                                    </DropdownMenuItem>
                                                ))
                                            ) : (
                                                <p className="p-4 text-center text-xs text-muted-foreground">No unread
                                                    notifications</p>
                                            )}
                                        </ScrollArea>
                                    </DropdownMenuContent>
                                </DropdownMenu>

                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="ghost" size="icon" data-testid="mobile-user-menu"
                                                aria-label="User menu">
                                            <Avatar className="h-8 w-8">
                                                <AvatarImage src={user?.profile_image}/>
                                                <AvatarFallback className="bg-primary text-primary-foreground text-xs">
                                                    {getInitials(user?.full_name)}
                                                </AvatarFallback>
                                            </Avatar>
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="end" className="w-[min(14rem,calc(100vw-1rem))]">
                                        <DropdownMenuLabel>
                                            <div className="flex min-w-0 flex-col">
                                                <span className="truncate">{user?.full_name}</span>
                                                <span
                                                    className="truncate text-xs font-normal text-muted-foreground">{user?.email}</span>
                                                <span className="text-xs text-primary">
                                                    {user?.role ? roleDisplayNames[user.role as keyof typeof roleDisplayNames] : ''}
                                                    {user?.ec_position && user.ec_position !== 'MEMBER' && ecPositionDisplayNames[user.ec_position]
                                                        ? ` · ${ecPositionDisplayNames[user.ec_position]}`
                                                        : ''}
                                                </span>
                                                {user?.unit_number && (
                                                    <span
                                                        className="text-xs text-muted-foreground/70">Unit {user.unit_number}</span>
                                                )}
                                            </div>
                                        </DropdownMenuLabel>
                                        <DropdownMenuSeparator/>
                                        <UserMenuShortcutItems onNavigate={() => setSidebarOpen(false)}/>
                                        <UserMenuModeItems/>
                                        {isRealAdmin() && (
                                            <>
                                                {!isImpersonating && (
                                                    <DropdownMenuItem onClick={() => setImpersonateModalOpen(true)}>
                                                        <UserSearch className="mr-2 h-4 w-4"/>
                                                        Impersonate User
                                                    </DropdownMenuItem>
                                                )}
                                            </>
                                        )}
                                        <DropdownMenuSeparator/>
                                        <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                                            <LogOut className="mr-2 h-4 w-4"/>
                                            Log out
                                        </DropdownMenuItem>
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            </div>
                        </div>
                    </header>

                    {/* Desktop header */}
                    <header className="hidden lg:flex glass-header h-16 items-center justify-between px-6">
                        <div className="flex items-center gap-8">
                            <div className="flex flex-col">
                                <h1 className="text-lg font-semibold leading-none mb-1">
                                    {navItems.find(item => item.href === pathname)?.label || 'Dashboard'}
                                </h1>
                                {selectedBuilding && (
                                    <span className="text-xs text-muted-foreground font-medium flex items-center gap-1">
                                        <Building2 className="h-3 w-3"/>
                                        {selectedBuilding.name}
                                    </span>
                                )}
                            </div>

                            {/* Public Navigation Links */}
                            <nav className="flex items-center gap-1">
                                {publicNavItems.map((item) => (
                                    <Link
                                        key={item.href}
                                        href={item.href}
                                        className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                                            pathname === item.href
                                                ? 'bg-primary/10 text-primary'
                                                : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                                        }`}
                                        data-testid={`header-nav-${item.label.toLowerCase()}`}
                                    >
                                        {item.label}
                                    </Link>
                                ))}
                            </nav>
                        </div>

                        <div className="flex items-center gap-4">
                            {/* Building Selector */}
                            {availableBuildings.length > 1 && (
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="outline" size="sm"
                                                className="gap-2 border-primary/20 bg-primary/5 hover:bg-primary/10">
                                            <Building2 className="h-4 w-4 text-primary"/>
                                            <span
                                                className="max-w-[120px] truncate">{selectedBuilding?.name || 'Select Building'}</span>
                                            <ChevronDown className="h-3 w-3 opacity-50"/>
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="end" className="w-64">
                                        <DropdownMenuLabel>Switch Property</DropdownMenuLabel>
                                        <DropdownMenuSeparator/>
                                        <ScrollArea className="h-auto max-h-[300px]">
                                            {availableBuildings.map((b) => (
                                                <DropdownMenuItem
                                                    key={b.id}
                                                    onClick={() => switchBuilding(b.id)}
                                                    className={`flex flex-col items-start gap-0.5 py-2 ${b.id === selectedBuilding?.id ? 'bg-primary/10' : ''}`}
                                                >
                                                    <span className="font-medium">{b.name}</span>
                                                    <span
                                                        className="text-[10px] text-muted-foreground truncate w-full">{b.address}</span>
                                                </DropdownMenuItem>
                                            ))}
                                        </ScrollArea>
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            )}

                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button variant="ghost" size="icon" onClick={() => router.push('/')}
                                            aria-label="Go to homepage">
                                        <Home className="h-5 w-5"/>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>
                                    <p>Home</p>
                                </TooltipContent>
                            </Tooltip>

                            {/* Notifications Dropdown */}
                            <DropdownMenu>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <DropdownMenuTrigger asChild>
                                            <Button variant="ghost" size="icon" className="relative"
                                                    aria-label="Notifications">
                                                <Bell className="h-5 w-5"/>
                                                {unreadCount > 0 && (
                                                    <span
                                                        className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] font-medium text-destructive-foreground">
                              {unreadCount > 9 ? '9+' : unreadCount}
                            </span>
                                                )}
                                            </Button>
                                        </DropdownMenuTrigger>
                                    </TooltipTrigger>
                                    <TooltipContent>
                                        <p>Notifications</p>
                                    </TooltipContent>
                                </Tooltip>
                                <DropdownMenuContent className="w-80" align="end">
                                    <DropdownMenuLabel className="flex items-center justify-between">
                                        <span>Notifications</span>
                                        {unreadCount > 0 && (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-auto p-0 text-xs font-normal text-primary"
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    markAllRead();
                                                }}
                                            >
                                                Mark all as read
                                            </Button>
                                        )}
                                    </DropdownMenuLabel>
                                    <DropdownMenuSeparator/>
                                    <ScrollArea className="h-[300px]">
                                        {notifications.length > 0 ? (
                                            notifications.map((notif) => (
                                                <DropdownMenuItem
                                                    key={notif.id}
                                                    className={`flex flex-col items-start p-4 cursor-pointer ${!notif.is_read ? 'bg-primary/5' : 'opacity-70'}`}
                                                    onClick={() => {
                                                        markNotificationRead(notif.id);
                                                        if (notif.link) router.push(notif.link);
                                                    }}
                                                >
                                                    <div className="flex w-full justify-between items-start mb-1">
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-semibold text-sm">{notif.title}</span>
                                                            {!notif.is_read && <Badge
                                                                className="h-2 w-2 p-0 rounded-full bg-primary"/>}
                                                        </div>
                                                        <span
                                                            className="text-[10px] text-muted-foreground whitespace-nowrap">
                            {new Date(notif.created_at).toLocaleDateString()}
                          </span>
                                                    </div>
                                                    <p className="text-xs text-muted-foreground line-clamp-2">
                                                        {notif.message}
                                                    </p>
                                                </DropdownMenuItem>
                                            ))
                                        ) : (
                                            <div className="flex flex-col items-center justify-center p-8 text-center">
                                                <Bell className="h-8 w-8 text-muted-foreground/30 mb-2"/>
                                                <p className="text-sm text-muted-foreground">No unread notifications</p>
                                            </div>
                                        )}
                                    </ScrollArea>
                                    <DropdownMenuSeparator/>
                                    <DropdownMenuItem
                                        className="justify-center text-xs font-medium text-primary py-2"
                                        onClick={() => router.push('/dashboard')}
                                    >
                                        Dashboard Overview
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>

                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" className="h-10 w-10 rounded-full p-0"
                                            aria-label="User menu">
                                        <Avatar className="h-10 w-10">
                                            <AvatarImage src={user?.profile_image}/>
                                            <AvatarFallback className="bg-primary text-primary-foreground">
                                                {getInitials(user?.full_name)}
                                            </AvatarFallback>
                                        </Avatar>
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" className="w-56">
                                    <DropdownMenuLabel>
                                        <div className="flex flex-col">
                                            <span>{user?.full_name}</span>
                                            <span
                                                className="text-xs font-normal text-muted-foreground">{user?.email}</span>
                                            <span className="text-xs text-primary">
                                                {user?.role ? roleDisplayNames[user.role as keyof typeof roleDisplayNames] : ''}
                                                {user?.ec_position && user.ec_position !== 'MEMBER' && ecPositionDisplayNames[user.ec_position]
                                                    ? ` · ${ecPositionDisplayNames[user.ec_position]}`
                                                    : ''}
                                            </span>
                                            {user?.unit_number && (
                                                <span
                                                    className="text-xs text-muted-foreground/70">Unit {user.unit_number}</span>
                                            )}
                                        </div>
                                    </DropdownMenuLabel>
                                    <DropdownMenuSeparator/>
                                    <UserMenuShortcutItems/>
                                    <UserMenuModeItems/>
                                    {isRealAdmin() && (
                                        <>
                                            {!isImpersonating && (
                                                <DropdownMenuItem onClick={() => setImpersonateModalOpen(true)}>
                                                    <UserSearch className="mr-2 h-4 w-4"/>
                                                    Impersonate User
                                                </DropdownMenuItem>
                                            )}
                                        </>
                                    )}
                                    <DropdownMenuSeparator/>
                                    <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                                        <LogOut className="mr-2 h-4 w-4"/>
                                        Log out
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                        </div>
                    </header>

                    <ImpersonateModal
                        isOpen={impersonateModalOpen}
                        onClose={() => setImpersonateModalOpen(false)}
                    />

                    {/* Page content */}
                    <main className="relative min-h-screen min-w-0 max-w-full overflow-x-clip bg-background p-3 sm:p-4 lg:p-6" data-testid="dashboard-content">
                        {children}
                        {/* Request Maintenance FAB.
                            Previously gated on the RAW user.role string, which broke it twice
                            over: an elevated user carries role='owner' with a different effective
                            role, and every manager/admin was excluded outright — so the button
                            silently vanished in Management Mode. Raising a maintenance request is
                            not a resident-only action; managers raise them constantly. Gated
                            through the AuthContext helpers (effective-role aware) plus the
                            maintenance feature toggle, so it tracks what the user can actually do. */}
                        {hasFeatureAccess('maintenance') && (isManager() || isOwner() || isTenant() || isGuest()) && (
                            <button
                                onClick={() => router.push('/maintenance')}
                                className="fixed bottom-4 right-3 z-50 flex max-w-[calc(100vw-1.5rem)] items-center gap-2 rounded-full bg-orange-500 px-4 py-3 text-sm font-semibold text-white shadow-lg transition-all hover:bg-orange-600 active:scale-95 sm:bottom-6 sm:right-6 sm:px-5"
                                data-testid="maintenance-fab"
                                title="Request Maintenance"
                            >
                                <Wrench className="h-4 w-4"/>
                                <span className="truncate">Request Maintenance</span>
                            </button>
                        )}
                    </main>

                    {/* Footer caption */}
                    <footer className="border-t border-border/50 px-3 py-2 sm:px-6">
                        <p id="dashboard-ip-notice"
                           className="break-words text-center text-[10px] text-muted-foreground/60 transition-all duration-300 opacity-100 visible"
                           style={{display: 'block', visibility: 'visible', opacity: 1}}>
                            {ipString || "A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au"}
                        </p>
                    </footer>
                </div>
            </div>

            {hasFeatureAccess('progressive_nav') && hasFeatureAccess('nav_customisation') && (
                <NavCustomiseDrawer
                    open={customiseDrawerOpen}
                    onClose={() => setCustomiseDrawerOpen(false)}
                />
            )}
        </NavigationProvider>
    );
};

export default DashboardLayout;
