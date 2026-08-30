import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import {
    AlertCircle,
    Calendar,
    CheckCircle2,
    CheckSquare,
    Clock,
    Image as ImageIcon,
    MapPin,
    Pause,
    Phone,
    PlayCircle,
    Wrench
} from 'lucide-react';
import { formatDate } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * Service Provider Dashboard
 * Mobile-friendly job management interface for contractors and service providers
 * Displays assigned work orders, job history, and minimal resident data
 */
const ServiceProviderDashboard = () => {
    const {user, api, token, isImpersonating} = useAuth();
    const router = useRouter();
    const [workOrders, setWorkOrders] = useState({
        pending: [],
        in_progress: [],
        completed: []
    });
    const [stats, setStats] = useState({
        total_assigned: 0,
        completed_this_month: 0,
        avg_completion_time: 0
    });
    const [loading, setLoading] = useState(true);

    const fetchDashboardData = React.useCallback(async () => {
        if (!token) return;
        try {
            // Fetch work orders assigned to this service provider
            try {
                const workOrdersRes = await api.get('/maintenance?assigned_to_me=true');
                const orders = workOrdersRes.data || [];

                // Group by status
                setWorkOrders({
                    pending: orders.filter(o => o.status === 'pending' || o.status === 'assigned'),
                    in_progress: orders.filter(o => o.status === 'in_progress'),
                    completed: orders.filter(o => o.status === 'completed').slice(0, 5)
                });

                // Calculate stats
                setStats({
                    total_assigned: orders.filter(o => o.status !== 'completed').length,
                    completed_this_month: orders.filter(o => {
                        const completed = new Date(o.updated_at);
                        const now = new Date();
                        return o.status === 'completed' &&
                            completed.getMonth() === now.getMonth() &&
                            completed.getFullYear() === now.getFullYear();
                    }).length,
                    avg_completion_time: 2.5 // Mock - would calculate from actual data
                });
            } catch (error) {
                console.error('Failed to fetch work orders:', error);
            }
        } catch (error) {
            console.error('Failed to fetch dashboard data:', error);
            toast.error('Failed to load dashboard data');
        } finally {
            setLoading(false);
        }
    }, [api, token]);

    useEffect(() => {
        fetchDashboardData();
    }, [fetchDashboardData]);
    /**
     * @generated FunctionHeader
     * Function: updateWorkOrderStatus
     * Path: frontend/src/pages/dashboard/ServiceProviderDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateWorkOrderStatus = async (orderId, newStatus) => {
        try {
            await api.patch(`/maintenance/${orderId}`, {status: newStatus});
            toast.success(`Work order status updated to ${newStatus.replace('_', ' ')}`);
            fetchDashboardData();
        } catch (error) {
            console.error('Failed to update work order:', error);
            toast.error('Failed to update status');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getStatusColor
     * Path: frontend/src/pages/dashboard/ServiceProviderDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusColor = (status) => {
        switch (status) {
            case 'pending':
            case 'assigned':
                return 'bg-yellow-50 border-yellow-200 text-yellow-800';
            case 'in_progress':
                return 'bg-blue-50 border-blue-200 text-blue-800';
            case 'completed':
                return 'bg-green-50 border-green-200 text-green-800';
            default:
                return 'bg-gray-50 border-gray-200 text-gray-800';
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getStatusIcon
     * Path: frontend/src/pages/dashboard/ServiceProviderDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusIcon = (status) => {
        switch (status) {
            case 'pending':
            case 'assigned':
                return <Clock className="h-4 w-4"/>;
            case 'in_progress':
                return <PlayCircle className="h-4 w-4"/>;
            case 'completed':
                return <CheckCircle2 className="h-4 w-4"/>;
            default:
                return <AlertCircle className="h-4 w-4"/>;
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getPriorityBadge
     * Path: frontend/src/pages/dashboard/ServiceProviderDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getPriorityBadge = (priority) => {
        const variants = {
            urgent: {color: 'bg-red-100 text-red-800 border-red-200', label: 'Urgent'},
            high: {color: 'bg-orange-100 text-orange-800 border-orange-200', label: 'High'},
            medium: {color: 'bg-blue-100 text-blue-800 border-blue-200', label: 'Medium'},
            low: {color: 'bg-gray-100 text-gray-800 border-gray-200', label: 'Low'}
        };
        const variant = variants[ priority ] || variants.medium;
        return (
            <Badge className={`${variant.color} text-xs`}>
                {variant.label}
            </Badge>
        );
    };
    /**
     * @generated FunctionHeader
     * Function: WorkOrderCard
     * Path: frontend/src/pages/dashboard/ServiceProviderDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const WorkOrderCard = ({order, showActions = true}) => (
        <Card className={`${getStatusColor(order.status)} border-2`}>
            <CardContent className="p-4">
                <div className="flex items-start justify-between mb-3">
                    <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1">
                            {getStatusIcon(order.status)}
                            <h4 className="font-semibold text-sm">{order.title}</h4>
                        </div>
                        <p className="text-xs text-muted-foreground mb-2">
                            Request ID: #{order.id?.slice(0, 8)}
                        </p>
                    </div>
                    {getPriorityBadge(order.priority)}
                </div>

                <p className="text-sm mb-3 line-clamp-2">
                    {order.description}
                </p>

                <div className="space-y-2 text-xs text-muted-foreground mb-3">
                    {order.unit_number && (
                        <div className="flex items-center gap-2">
                            <MapPin className="h-3 w-3"/>
                            <span>Unit {order.unit_number}</span>
                        </div>
                    )}
                    {order.location && (
                        <div className="flex items-center gap-2">
                            <MapPin className="h-3 w-3"/>
                            <span>{order.location}</span>
                        </div>
                    )}
                    <div className="flex items-center gap-2">
                        <Clock className="h-3 w-3"/>
                        <span>Reported: {formatDate(order.created_at)}</span>
                    </div>
                    {order.estimated_cost && (
                        <div className="flex items-center gap-2">
                            <span className="font-semibold">Est. Cost: ${order.estimated_cost}</span>
                        </div>
                    )}
                </div>

                {showActions && order.status !== 'completed' && (
                    <div className="flex gap-2">
                        {order.status === 'pending' || order.status === 'assigned' ? (
                            <Button
                                size="sm"
                                className="flex-1"
                                onClick={() => updateWorkOrderStatus(order.id, 'in_progress')}
                            >
                                <PlayCircle className="h-4 w-4 mr-1"/>
                                Start Work
                            </Button>
                        ) : (
                            <>
                                <Button
                                    size="sm"
                                    variant="outline"
                                    className="flex-1"
                                    onClick={() => updateWorkOrderStatus(order.id, 'pending')}
                                >
                                    <Pause className="h-4 w-4 mr-1"/>
                                    Pause
                                </Button>
                                <Button
                                    size="sm"
                                    className="flex-1"
                                    onClick={() => updateWorkOrderStatus(order.id, 'completed')}
                                >
                                    <CheckSquare className="h-4 w-4 mr-1"/>
                                    Complete
                                </Button>
                            </>
                        )}
                    </div>
                )}

                {order.status === 'completed' && order.actual_cost && (
                    <div className="mt-2 p-2 bg-white rounded border">
                        <span className="text-xs font-semibold">Actual Cost: ${order.actual_cost}</span>
                    </div>
                )}
            </CardContent>
        </Card>
    );

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <div className="text-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto"></div>
                    <p className="mt-4 text-muted-foreground">Loading work orders...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="service-provider-dashboard">
            {/* Welcome Header */}
            <div>
                <h1 className="text-3xl font-bold flex items-center gap-2">
                    <Wrench className="h-8 w-8"/>
                    Service Provider Portal
                </h1>
                <p className="text-muted-foreground mt-1">
                    {user?.full_name} - Job Management Dashboard
                </p>
            </div>

            {/* Stats Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <div className="p-3 rounded-full bg-yellow-50">
                                <Clock className="h-6 w-6 text-yellow-600"/>
                            </div>
                            <div>
                                <p className="text-sm text-muted-foreground">Active Jobs</p>
                                <p className="text-2xl font-bold">{stats.total_assigned}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <div className="p-3 rounded-full bg-green-50">
                                <CheckCircle2 className="h-6 w-6 text-green-600"/>
                            </div>
                            <div>
                                <p className="text-sm text-muted-foreground">Completed This Month</p>
                                <p className="text-2xl font-bold">{stats.completed_this_month}</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <div className="p-3 rounded-full bg-blue-50">
                                <Calendar className="h-6 w-6 text-blue-600"/>
                            </div>
                            <div>
                                <p className="text-sm text-muted-foreground">Avg. Completion</p>
                                <p className="text-2xl font-bold">{stats.avg_completion_time} days</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Pending Jobs - Kanban Style */}
            {workOrders.pending.length > 0 && (
                <div>
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-xl font-bold flex items-center gap-2">
                            <AlertCircle className="h-5 w-5 text-yellow-600"/>
                            Pending Jobs ({workOrders.pending.length})
                        </h2>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {workOrders.pending.map((order) => (
                            <WorkOrderCard key={order.id} order={order}/>
                        ))}
                    </div>
                </div>
            )}

            {/* In Progress Jobs */}
            {workOrders.in_progress.length > 0 && (
                <div>
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-xl font-bold flex items-center gap-2">
                            <PlayCircle className="h-5 w-5 text-blue-600"/>
                            In Progress ({workOrders.in_progress.length})
                        </h2>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {workOrders.in_progress.map((order) => (
                            <WorkOrderCard key={order.id} order={order}/>
                        ))}
                    </div>
                </div>
            )}

            {/* No Active Jobs */}
            {workOrders.pending.length === 0 && workOrders.in_progress.length === 0 && (
                <Card className="card-dashboard">
                    <CardContent className="p-12 text-center">
                        <CheckCircle2 className="h-12 w-12 text-green-600 mx-auto mb-4"/>
                        <h3 className="text-lg font-semibold mb-2">All Caught Up!</h3>
                        <p className="text-muted-foreground">
                            You have no active work orders at the moment.
                        </p>
                    </CardContent>
                </Card>
            )}

            {/* Recently Completed Jobs */}
            {workOrders.completed.length > 0 && (
                <div>
                    <div className="flex items-center justify-between mb-4">
                        <h2 className="text-xl font-bold flex items-center gap-2">
                            <CheckCircle2 className="h-5 w-5 text-green-600"/>
                            Recently Completed
                        </h2>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => router.push('/maintenance/schedule')}
                        >
                            View All History
                        </Button>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {workOrders.completed.map((order) => (
                            <WorkOrderCard key={order.id} order={order} showActions={false}/>
                        ))}
                    </div>
                </div>
            )}

            {/* Quick Actions */}
            <Card className="card-dashboard bg-gradient-to-r from-primary/5 to-secondary/5">
                <CardHeader>
                    <CardTitle className="text-base">Service Provider Resources</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <Button
                            variant="outline"
                            className="h-auto py-4 flex flex-col items-center gap-2 bg-white"
                            onClick={() => router.push('/maintenance/schedule')}
                        >
                            <Calendar className="h-5 w-5"/>
                            <span className="text-sm">My Schedule</span>
                        </Button>

                        <Button
                            variant="outline"
                            className="h-auto py-4 flex flex-col items-center gap-2 bg-white"
                            onClick={() => router.push('/emergency-services')}
                        >
                            <Phone className="h-5 w-5"/>
                            <span className="text-sm">Building Contacts</span>
                        </Button>

                        {!isImpersonating && (
                            <Button
                                variant="outline"
                                className="h-auto py-4 flex flex-col items-center gap-2 bg-white"
                                onClick={() => router.push('/profile')}
                            >
                                <ImageIcon className="h-5 w-5"/>
                                <span className="text-sm">My Profile</span>
                            </Button>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Mobile Optimization Note */}
            <Card className="card-dashboard border-blue-200 bg-blue-50/50">
                <CardContent className="p-4">
                    <p className="text-sm text-blue-800">
                        💡 <strong>Mobile Tip:</strong> This dashboard is optimized for mobile use.
                        Add this page to your home screen for quick access to your work orders on the go.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
};

export default ServiceProviderDashboard;
