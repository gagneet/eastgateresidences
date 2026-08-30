"use client";
import React, {useEffect, useState} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {Card, CardContent, CardHeader, CardTitle} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Badge} from '../../components/ui/badge';
import {
    ArrowRight,
    Bell,
    Calendar,
    ClipboardCheck,
    ClipboardList,
    DollarSign,
    FileText,
    Megaphone,
    MessageSquare,
    ShoppingBag,
    TrendingDown,
    TrendingUp,
    Users,
    Wrench
} from 'lucide-react';
import {formatCurrency, formatDateTime} from '../../lib/utils';
import SuperAdminDashboard from './SuperAdminDashboard';
import OwnerDashboard from './OwnerDashboard';
import ECDashboard from './ECDashboard';
import TenantDashboard from './TenantDashboard';
import ServiceProviderDashboard from './ServiceProviderDashboard';
import ManagerDashboard from './ManagerDashboard';
import PendingApprovalDashboard from './PendingApprovalDashboard';
/**
 * @generated FunctionHeader
 * Function: DashboardHome
 * Path: frontend/src/pages/dashboard/DashboardHome.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const DashboardHome: React.FC = () => {
    const {user, hasPermission, api, isAdmin, isECMember, isManager, isImpersonating} = useAuth();
    const router = useRouter();
    const [stats, setStats] = useState<any>({});
    const [maintStats, setMaintStats] = useState({open_requests: 0});
    const [announcements, setAnnouncements] = useState<any[]>([]);
    const [todos, setTodos] = useState<any[]>([]);
    const [importantDocs, setImportantDocs] = useState<any[]>([]);
    const [dismissedDocs, setDismissedDocs] = useState<Set<string>>(
        () => new Set(typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]') : [])
    );
    /**
     * @generated FunctionHeader
     * Function: dismissDoc
     * Path: frontend/src/pages/dashboard/DashboardHome.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const dismissDoc = (docId: string) => {
        const updated = new Set([...dismissedDocs, docId]);
        setDismissedDocs(updated);
        localStorage.setItem('dismissed_important_docs', JSON.stringify([...updated]));
    };

    useEffect(() => {
        // Wait until user is loaded before making any API calls
        if (!user) return;

        // Fetch important docs for all non-role-specific users
        api.get('/documents/important').then(res => {
            setImportantDocs(res.data || []);
        }).catch(() => {
        });

        // Don't fetch data if user has a role-specific dashboard
        if (isAdmin() || isManager() || isECMember() || user?.role === 'owner' || user?.role === 'tenant' || user?.role === 'service_provider') return;
        /**
         * @generated FunctionHeader
         * Function: fetchData
         * Path: frontend/src/pages/dashboard/DashboardHome.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchData = async () => {
            try {
                const [statsRes, announcementsRes, maintRes] = await Promise.all([
                    api.get('/admin/stats'),
                    api.get('/announcements'),
                    api.get('/analytics/maintenance-stats')
                ]);
                setStats(statsRes.data);
                setMaintStats(maintRes.data);
                setAnnouncements(announcementsRes.data.slice(0, 5));

                if (hasPermission('can_view_meetings')) {
                    const todosRes = await api.get('/todos?status=pending');
                    setTodos(todosRes.data.slice(0, 5));
                }
            } catch (error) {
                console.error('Failed to fetch dashboard data:', error);
            }
        };
        fetchData();
    }, [api, hasPermission, isAdmin, isECMember, isManager, user]);

    // CRITICAL: Check if user account is pending approval
    if (user && user.is_approved === false && !isAdmin() && !isECMember() && !isManager()) {
        return <PendingApprovalDashboard/>;
    }

    // Route to role-specific dashboards
    if (isAdmin()) {
        return <SuperAdminDashboard/>;
    }

    if (isManager() && !isAdmin()) {
        return <ManagerDashboard/>;
    }

    if (isECMember()) {
        return <ECDashboard/>;
    }

    if (user?.role === 'owner') {
        return <OwnerDashboard/>;
    }

    if (user?.role === 'tenant') {
        return <TenantDashboard/>;
    }

    if (user?.role === 'service_provider') {
        return <ServiceProviderDashboard/>;
    }

    const quickActions = [
        {
            label: 'View Marketplace',
            icon: ShoppingBag,
            href: '/community/marketplace',
            show: true
        },
        {
            label: 'Community Chat',
            icon: MessageSquare,
            href: '/community/chat',
            show: hasPermission('can_chat')
        },
        {
            label: 'Documents',
            icon: FileText,
            href: '/documents',
            show: hasPermission('can_view_documents')
        },
        {
            label: 'Meetings',
            icon: Calendar,
            href: '/governance/meetings',
            show: hasPermission('can_view_meetings')
        },
        {
            label: 'Finances',
            icon: DollarSign,
            href: '/financials/overview',
            show: hasPermission('can_view_finances')
        },
        {
            label: 'Users',
            icon: Users,
            href: '/admin/users',
            show: hasPermission('can_manage_users')
        }
    ].filter(action => action.show);

    const statCards = [
        {
            label: 'Active Listings',
            value: stats.active_listings || 0,
            icon: ShoppingBag,
            show: true
        },
        {
            label: 'Announcements',
            value: stats.active_announcements || 0,
            icon: Megaphone,
            show: true
        },
        {
            label: 'Documents',
            value: stats.total_documents || 0,
            icon: FileText,
            show: hasPermission('can_view_documents')
        },
        {
            label: 'Pending Tasks',
            value: stats.pending_todos || 0,
            icon: ClipboardList,
            show: hasPermission('can_view_meetings')
        },
        {
            label: 'Upcoming Meetings',
            value: stats.upcoming_meetings || 0,
            icon: Calendar,
            show: hasPermission('can_view_meetings')
        },
        {
            label: 'Total Users',
            value: stats.total_users || 0,
            icon: Users,
            show: hasPermission('can_manage_users')
        }
    ].filter(stat => stat.show);

    const visibleImportantDocs = importantDocs.filter(d => !dismissedDocs.has(d.id));

    return (
        <div className="space-y-8" data-testid="dashboard-home">
            {/* Important Document Alerts */}
            {visibleImportantDocs.map(doc => (
                <Card key={doc.id} className="border-amber-400 bg-amber-50 dark:bg-amber-950/20"
                      data-testid="important-doc-alert">
                    <CardContent className="p-4">
                        <div className="flex items-start gap-3">
                            <div className="p-2 rounded-full bg-amber-100 dark:bg-amber-900 shrink-0">
                                <Bell className="h-4 w-4 text-amber-600"/>
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="font-semibold text-sm text-amber-900 dark:text-amber-100">{doc.name}</p>
                                {doc.importance_summary && (
                                    <p className="text-xs text-amber-800 dark:text-amber-200 mt-1 line-clamp-3">{doc.importance_summary}</p>
                                )}
                            </div>
                            <button
                                onClick={() => dismissDoc(doc.id)}
                                className="shrink-0 text-amber-500 hover:text-amber-700 text-lg leading-none ml-2"
                                aria-label="Dismiss"
                            >×
                            </button>
                        </div>
                    </CardContent>
                </Card>
            ))}

            {/* Pending Approval Alert */}
            {user && !user.is_approved && (
                <Card className="border-warning bg-warning/5" data-testid="pending-approval-alert">
                    <CardContent className="p-6">
                        <div className="flex items-start gap-4">
                            <div className="p-3 rounded-full bg-warning/10">
                                <ClipboardList className="h-6 w-6 text-warning"/>
                            </div>
                            <div className="flex-1">
                                <h3 className="text-lg font-semibold mb-2">Account Pending Approval</h3>
                                <p className="text-muted-foreground mb-4">
                                    Your account is currently pending approval from the Executive Committee or
                                    Administrator.
                                    You have limited view-only access until your account is approved.
                                    You will receive an email notification once your account has been reviewed.
                                </p>
                                <div className="flex flex-col sm:flex-row gap-3">
                                    {!isImpersonating && (
                                        <Button variant="outline" onClick={() => router.push('/profile')}>
                                            <Users className="mr-2 h-4 w-4"/>
                                            View Profile
                                        </Button>
                                    )}
                                    <Button variant="outline" onClick={() => router.push('/emergency-services')}>
                                        <MessageSquare className="mr-2 h-4 w-4"/>
                                        Contact Support
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Welcome Section */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Welcome back, {user?.full_name?.split(' ')[0]}!</h1>
                    <p className="text-muted-foreground">Here's what's happening in your community</p>
                </div>
                {hasPermission('can_create_listings') && user?.is_approved && (
                    <Button onClick={() => router.push('/community/my-listings')} data-testid="create-listing-btn">
                        <ShoppingBag className="mr-2 h-4 w-4"/>
                        Create Listing
                    </Button>
                )}
            </div>

            {/* Maintenance & Approvals KPI */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <Card className="card-dashboard hover:border-primary transition-colors cursor-pointer group"
                      onClick={() => router.push('/maintenance')}>
                    <CardContent className="p-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Maintenance</p>
                                <h3 className="text-3xl font-bold mt-1">{maintStats.open_requests} Open</h3>
                            </div>
                            <div
                                className="p-3 bg-orange-100 dark:bg-orange-950 rounded-full group-hover:scale-110 transition-transform">
                                <Wrench className="h-6 w-6 text-orange-600"/>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {hasPermission('can_manage_finances') && (
                    <Card className="card-dashboard hover:border-primary transition-colors cursor-pointer group"
                          onClick={() => router.push('/requests/my-approvals')}>
                        <CardContent className="p-6">
                            <div className="flex items-center justify-between">
                                <div>
                                    <p className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Approvals</p>
                                    <h3 className="text-3xl font-bold mt-1">{stats.pending_invoices_count || 0} Pending</h3>
                                </div>
                                <div
                                    className="p-3 bg-purple-100 dark:bg-purple-950 rounded-full group-hover:scale-110 transition-transform">
                                    <ClipboardCheck className="h-6 w-6 text-purple-600"/>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                )}
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                {statCards.map((stat, index) => (
                    <Card key={index} className="card-dashboard"
                          data-testid={`stat-${stat.label.toLowerCase().replace(/\s+/g, '-')}`}>
                        <CardContent className="p-4">
                            <div className="flex items-center justify-between mb-2">
                                <stat.icon className="h-5 w-5 text-primary"/>
                            </div>
                            <p className="text-2xl font-bold">{stat.value}</p>
                            <p className="text-xs text-muted-foreground">{stat.label}</p>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {/* Finance Summary (for EC/Admin) */}
            {hasPermission('can_view_finances') && stats.finance_balance !== undefined && (
                <Card className="card-dashboard" data-testid="finance-summary">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-lg flex items-center gap-2">
                            <DollarSign className="h-5 w-5"/>
                            Financial Summary
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center gap-4">
                            <div>
                                <p className="text-sm text-muted-foreground">Current Balance</p>
                                <p className={`text-3xl font-bold ${stats.finance_balance >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                    {formatCurrency(stats.finance_balance)}
                                </p>
                            </div>
                            {stats.finance_balance >= 0 ? (
                                <TrendingUp className="h-8 w-8 text-green-600"/>
                            ) : (
                                <TrendingDown className="h-8 w-8 text-red-600"/>
                            )}
                        </div>
                        <Button variant="link" className="p-0 mt-2" onClick={() => router.push('/financials/overview')}>
                            View Details <ArrowRight className="ml-1 h-4 w-4"/>
                        </Button>
                    </CardContent>
                </Card>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Announcements */}
                <Card className="card-dashboard" data-testid="announcements-card">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-lg flex items-center gap-2">
                                <Megaphone className="h-5 w-5"/>
                                Recent Announcements
                            </CardTitle>
                            <Button variant="ghost" size="sm" onClick={() => router.push('/community/notices')}>
                                View All
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {announcements.length === 0 ? (
                            <p className="text-muted-foreground text-sm">No announcements yet</p>
                        ) : (
                            <div className="space-y-3">
                                {announcements.map((announcement) => (
                                    <div
                                        key={announcement.id}
                                        className="flex items-start gap-3 p-3 rounded-lg bg-muted/50"
                                    >
                                        <Badge
                                            className={`shrink-0 ${
                                                announcement.priority === 'urgent' ? 'bg-red-100 text-red-800' :
                                                    announcement.priority === 'high' ? 'bg-orange-100 text-orange-800' :
                                                        'bg-blue-100 text-blue-800'
                                            }`}
                                        >
                                            {announcement.priority}
                                        </Badge>
                                        <div className="min-w-0 flex-1">
                                            <p className="font-medium text-sm truncate">{announcement.title}</p>
                                            <p className="text-xs text-muted-foreground">{formatDateTime(announcement.created_at)}</p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* Pending Tasks (for EC/Admin) */}
                {hasPermission('can_view_meetings') && (
                    <Card className="card-dashboard" data-testid="tasks-card">
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-lg flex items-center gap-2">
                                    <ClipboardList className="h-5 w-5"/>
                                    Pending Tasks
                                </CardTitle>
                                <Button variant="ghost" size="sm" onClick={() => router.push('/governance/todos')}>
                                    View All
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {todos.length === 0 ? (
                                <p className="text-muted-foreground text-sm">No pending tasks</p>
                            ) : (
                                <div className="space-y-3">
                                    {todos.map((todo) => (
                                        <div
                                            key={todo.id}
                                            className={`flex items-start gap-3 p-3 rounded-lg bg-muted/50 priority-${todo.priority}`}
                                        >
                                            <div className="min-w-0 flex-1">
                                                <p className="font-medium text-sm">{todo.title}</p>
                                                <div className="flex items-center gap-2 mt-1">
                                                    {todo.due_date && (
                                                        <span className="text-xs text-muted-foreground">
                              Due: {formatDateTime(todo.due_date)}
                            </span>
                                                    )}
                                                    {todo.assigned_to_name && (
                                                        <span className="text-xs text-primary">
                              → {todo.assigned_to_name}
                            </span>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                )}
            </div>

            {/* Quick Actions */}
            <Card className="card-dashboard" data-testid="quick-actions">
                <CardHeader className="pb-2">
                    <CardTitle className="text-lg">Quick Actions</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
                        {quickActions.map((action, index) => (
                            <Button
                                key={index}
                                variant="outline"
                                className="h-auto py-4 flex-col gap-2"
                                onClick={() => router.push(action.href)}
                                data-testid={`quick-action-${action.label.toLowerCase().replace(/\s+/g, '-')}`}
                            >
                                <action.icon className="h-5 w-5"/>
                                <span className="text-xs">{action.label}</span>
                            </Button>
                        ))}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
};

export default DashboardHome;
