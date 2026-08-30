import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import {
    ArrowRight,
    Bell,
    Calendar,
    FileText,
    Home,
    Megaphone,
    MessageSquare,
    Phone,
    ShoppingBag,
    Wrench
} from 'lucide-react';
import { formatDate } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * Tenant Dashboard
 * Streamlined view focused on announcements, events, maintenance, and community features
 * Minimal financial data access as per tenant role requirements
 */
const TenantDashboard = () => {
    const {user, api, token, isImpersonating} = useAuth();
    const router = useRouter();
    const [announcements, setAnnouncements] = useState([]);
    const [maintenanceRequests, setMaintenanceRequests] = useState([]);
    const [upcomingEvents, setUpcomingEvents] = useState([]);
    const [marketplaceListings, setMarketplaceListings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [importantDocs, setImportantDocs] = useState([]);
    const [dismissedDocs, setDismissedDocs] = useState(
        () => new Set(typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]') : [])
    );

    const fetchDashboardData = React.useCallback(async () => {
        if (!token) return;
        try {
            // Fetch recent announcements
            try {
                const announcementsRes = await api.get('/announcements?limit=5');
                setAnnouncements(announcementsRes.data || []);
            } catch (error) {
                console.error('Failed to fetch announcements:', error);
            }

            // Fetch tenant's maintenance requests
            try {
                const maintenanceRes = await api.get('/maintenance?mine=true');
                setMaintenanceRequests(maintenanceRes.data.slice(0, 3) || []);
            } catch (error) {
                console.error('Failed to fetch maintenance requests:', error);
            }

            // Fetch upcoming events
            try {
                const eventsRes = await api.get('/meetings?upcoming=true');
                setUpcomingEvents(eventsRes.data.slice(0, 3) || []);
            } catch (error) {
                console.error('Failed to fetch events:', error);
            }

            // Fetch recent marketplace listings
            try {
                const listingsRes = await api.get('/listings?limit=6');
                setMarketplaceListings(listingsRes.data || []);
            } catch (error) {
                console.error('Failed to fetch marketplace listings:', error);
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

    useEffect(() => {
        if (!api) return;
        api.get('/documents/important').then(res => setImportantDocs(res.data || [])).catch(() => {
        });
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: dismissDoc
     * Path: frontend/src/pages/dashboard/TenantDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const dismissDoc = (docId) => {
        const newSet = new Set([...dismissedDocs, docId]);
        setDismissedDocs(newSet);
        localStorage.setItem('dismissed_important_docs', JSON.stringify([...newSet]));
    };

    const quickActions = [
        {
            label: 'Submit Maintenance',
            icon: Wrench,
            href: '/maintenance',
            description: 'Report an issue',
            color: 'text-orange-600 bg-orange-50'
        },
        {
            label: 'Events Calendar',
            icon: Calendar,
            href: '/community/events',
            description: 'View upcoming events',
            color: 'text-blue-600 bg-blue-50'
        },
        {
            label: 'Community Chat',
            icon: MessageSquare,
            href: '/community/chat',
            description: 'Connect with residents',
            color: 'text-green-600 bg-green-50'
        },
        {
            label: 'Marketplace',
            icon: ShoppingBag,
            href: '/community/marketplace',
            description: 'Browse listings',
            color: 'text-purple-600 bg-purple-50'
        },
        {
            label: 'Documents',
            icon: FileText,
            href: '/documents',
            description: 'Building rules & docs',
            color: 'text-gray-600 bg-gray-50'
        },
        {
            label: 'Emergency Services',
            icon: Phone,
            href: '/emergency-services',
            description: 'Emergency contacts',
            color: 'text-red-600 bg-red-50'
        }
    ];

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <div className="text-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto"></div>
                    <p className="mt-4 text-muted-foreground">Loading your dashboard...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="tenant-dashboard">
            {/* Important Document Alerts */}
            {importantDocs.filter(d => !dismissedDocs.has(d.id)).map(doc => (
                <div key={doc.id}
                     className="flex items-start gap-3 p-4 rounded-lg border border-amber-400 bg-amber-50 text-amber-900 shadow-sm">
                    <Bell className="h-5 w-5 text-amber-500 mt-0.5 shrink-0"/>
                    <div className="flex-1 min-w-0">
                        <p className="font-semibold text-sm">Important: {doc.title}</p>
                        {doc.importance_summary && (
                            <p className="text-sm mt-0.5 line-clamp-3">{doc.importance_summary}</p>
                        )}
                        <p className="text-xs text-amber-600 mt-1">{( doc.category || '' ).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</p>
                    </div>
                    <button onClick={() => dismissDoc(doc.id)}
                            className="shrink-0 text-amber-400 hover:text-amber-700 transition-colors text-lg leading-none"
                            aria-label="Dismiss">×
                    </button>
                </div>
            ))}

            {/* Welcome Header */}
            <div>
                <h1 className="text-3xl font-bold flex items-center gap-2">
                    <Home className="h-8 w-8"/>
                    Welcome, {user?.full_name}
                </h1>
                <p className="text-muted-foreground mt-1">
                    {user.unit_number ? `Unit ${user.unit_number} - Tenant` : 'Tenant'} - Your community dashboard
                </p>
            </div>

            {/* Important Announcements Banner */}
            {announcements.filter(a => a.priority === 'urgent').length > 0 && (
                <Card className="border-red-200 bg-red-50">
                    <CardContent className="p-4">
                        <div className="flex items-start gap-3">
                            <Bell className="h-5 w-5 text-red-600 mt-0.5"/>
                            <div>
                                <h3 className="font-semibold text-red-900">Urgent Announcements</h3>
                                <p className="text-sm text-red-800 mt-1">
                                    {announcements.filter(a => a.priority === 'urgent').length} urgent announcement(s)
                                    require your attention
                                </p>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="mt-2 border-red-300 text-red-700 hover:bg-red-100"
                                    onClick={() => router.push('/community/notices')}
                                >
                                    View Now
                                </Button>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Quick Actions */}
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle>Quick Actions</CardTitle>
                    <CardDescription>Common tasks and resources for tenants</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                        {quickActions.map((action, index) => (
                            <button
                                key={index}
                                onClick={() => router.push(action.href)}
                                className="flex flex-col items-center justify-center p-4 rounded-lg border-2 border-border hover:border-primary hover:bg-primary/5 transition-all group"
                                data-testid={`quick-action-${action.label.toLowerCase().replace(/\s+/g, '-')}`}
                            >
                                <div
                                    className={`p-3 rounded-full ${action.color} mb-2 group-hover:scale-110 transition-transform`}>
                                    <action.icon className="h-6 w-6"/>
                                </div>
                                <span className="font-medium text-sm text-center">{action.label}</span>
                                <span className="text-xs text-muted-foreground text-center mt-1">
                  {action.description}
                </span>
                            </button>
                        ))}
                    </div>
                </CardContent>
            </Card>

            {/* Main Content Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Recent Announcements */}
                <Card className="card-dashboard">
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <Megaphone className="h-5 w-5"/>
                                <CardTitle>Community Announcements</CardTitle>
                            </div>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => router.push('/community/notices')}
                                className="gap-1"
                            >
                                View All
                                <ArrowRight className="h-4 w-4"/>
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {announcements.length === 0 ? (
                            <p className="text-center py-8 text-muted-foreground">No announcements</p>
                        ) : (
                            <div className="space-y-4">
                                {announcements.map((announcement) => (
                                    <div
                                        key={announcement.id}
                                        className="p-3 rounded-lg border hover:bg-accent/50 cursor-pointer transition-colors"
                                        onClick={() => router.push('/community/notices')}
                                    >
                                        <div className="flex items-start justify-between mb-1">
                                            <h4 className="font-semibold text-sm">{announcement.title}</h4>
                                            {announcement.priority === 'urgent' && (
                                                <Badge variant="destructive" className="text-xs">
                                                    <Bell className="h-3 w-3 mr-1"/>
                                                    Urgent
                                                </Badge>
                                            )}
                                        </div>
                                        <p className="text-sm text-muted-foreground line-clamp-2">
                                            {announcement.content}
                                        </p>
                                        <p className="text-xs text-muted-foreground mt-2">
                                            {formatDate(announcement.created_at)}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* Maintenance Requests */}
                <Card className="card-dashboard">
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <Wrench className="h-5 w-5"/>
                                <CardTitle>My Maintenance Requests</CardTitle>
                            </div>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => router.push('/maintenance')}
                                className="gap-1"
                            >
                                View All
                                <ArrowRight className="h-4 w-4"/>
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {maintenanceRequests.length === 0 ? (
                            <div className="text-center py-8">
                                <p className="text-muted-foreground mb-3">No active requests</p>
                                <Button
                                    onClick={() => router.push('/maintenance')}
                                    size="sm"
                                    variant="outline"
                                >
                                    Submit New Request
                                </Button>
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {maintenanceRequests.map((request) => (
                                    <div
                                        key={request.id}
                                        className="p-3 rounded-lg border hover:bg-accent/50 cursor-pointer transition-colors"
                                        onClick={() => router.push('/maintenance')}
                                    >
                                        <div className="flex items-start justify-between mb-1">
                                            <h4 className="font-semibold text-sm">{request.title}</h4>
                                            <Badge variant={
                                                request.status === 'completed' ? 'default' :
                                                    request.status === 'in_progress' ? 'secondary' :
                                                        'outline'
                                            }>
                                                {request.status.replace('_', ' ')}
                                            </Badge>
                                        </div>
                                        <p className="text-sm text-muted-foreground line-clamp-1">
                                            {request.description}
                                        </p>
                                        <p className="text-xs text-muted-foreground mt-1">
                                            {formatDate(request.created_at)}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* Upcoming Events */}
                <Card className="card-dashboard">
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <Calendar className="h-5 w-5"/>
                                <CardTitle>Upcoming Events</CardTitle>
                            </div>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => router.push('/community/events')}
                                className="gap-1"
                            >
                                View Calendar
                                <ArrowRight className="h-4 w-4"/>
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {upcomingEvents.length === 0 ? (
                            <p className="text-center py-8 text-muted-foreground">No upcoming events</p>
                        ) : (
                            <div className="space-y-3">
                                {upcomingEvents.map((event) => (
                                    <div
                                        key={event.id}
                                        className="p-3 rounded-lg border hover:bg-accent/50 cursor-pointer transition-colors"
                                        onClick={() => router.push('/community/events')}
                                    >
                                        <h4 className="font-semibold text-sm">{event.title}</h4>
                                        <p className="text-sm text-muted-foreground mt-1">
                                            {formatDate(event.date)} {event.time && `at ${event.time}`}
                                        </p>
                                        {event.location && (
                                            <p className="text-xs text-muted-foreground mt-1">📍 {event.location}</p>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* Community Marketplace */}
                <Card className="card-dashboard">
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <ShoppingBag className="h-5 w-5"/>
                                <CardTitle>Community Marketplace</CardTitle>
                            </div>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => router.push('/community/marketplace')}
                                className="gap-1"
                            >
                                View All
                                <ArrowRight className="h-4 w-4"/>
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {marketplaceListings.length === 0 ? (
                            <p className="text-center py-8 text-muted-foreground">No listings available</p>
                        ) : (
                            <div className="grid grid-cols-2 gap-3">
                                {marketplaceListings.slice(0, 4).map((listing) => (
                                    <div
                                        key={listing.id}
                                        className="p-3 rounded-lg border hover:bg-accent/50 cursor-pointer transition-colors"
                                        onClick={() => router.push('/community/marketplace')}
                                    >
                                        <h4 className="font-semibold text-sm line-clamp-1">{listing.title}</h4>
                                        <p className="text-sm text-primary font-bold mt-1">
                                            {listing.price ? `$${listing.price}` : 'Free'}
                                        </p>
                                        <p className="text-xs text-muted-foreground mt-1 line-clamp-1">
                                            {listing.category}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* Building Information */}
            <Card className="card-dashboard bg-gradient-to-r from-primary/5 to-secondary/5">
                <CardHeader>
                    <CardTitle className="text-base">Tenant Resources</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <Button
                            variant="outline"
                            className="h-auto py-4 flex flex-col items-center gap-2 bg-white"
                            onClick={() => router.push('/documents')}
                        >
                            <FileText className="h-5 w-5"/>
                            <span className="text-sm">Building Rules</span>
                        </Button>

                        <Button
                            variant="outline"
                            className="h-auto py-4 flex flex-col items-center gap-2 bg-white"
                            onClick={() => router.push('/emergency-services')}
                        >
                            <Phone className="h-5 w-5"/>
                            <span className="text-sm">Emergency Contacts</span>
                        </Button>

                        {!isImpersonating && (
                            <Button
                                variant="outline"
                                className="h-auto py-4 flex flex-col items-center gap-2 bg-white"
                                onClick={() => router.push('/profile')}
                            >
                                <Home className="h-5 w-5"/>
                                <span className="text-sm">My Profile</span>
                            </Button>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
};

export default TenantDashboard;
