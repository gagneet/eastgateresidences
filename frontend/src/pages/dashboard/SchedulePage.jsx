import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Calendar, Clock, Loader2, MapPin, Plus, User } from 'lucide-react';
import { formatDateTime, scheduleTypes, statusConfig } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: SchedulePage
 * Path: frontend/src/pages/dashboard/SchedulePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SchedulePage = () => {
    const {api, user, hasPermission} = useAuth();
    const [schedules, setSchedules] = useState([]);
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [filter, setFilter] = useState('all');
    const [formData, setFormData] = useState({
        title: '',
        description: '',
        start_time: '',
        end_time: '',
        location: '',
        assigned_to: '',
        schedule_type: 'general'
    });

    const fetchSchedules = React.useCallback(async () => {
        try {
            const params = filter !== 'all' ? `?schedule_type=${filter}` : '';
            const response = await api.get(`/schedule${params}`);
            setSchedules(response.data);
        } catch (error) {
            console.error('Failed to fetch schedules:', error);
        } finally {
            setLoading(false);
        }
    }, [api, filter]);

    const fetchUsers = React.useCallback(async () => {
        if (hasPermission('can_manage_users')) {
            try {
                const response = await api.get('/users?role=service_provider');
                setUsers(response.data);
            } catch (error) {
                console.error('Failed to fetch users:', error);
            }
        }
    }, [api, hasPermission]);

    useEffect(() => {
        fetchSchedules();
        fetchUsers();
    }, [fetchSchedules, fetchUsers]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/SchedulePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);

        try {
            await api.post('/schedule', {
                ...formData,
                assigned_to: formData.assigned_to || null
            });
            toast.success('Schedule created successfully');
            setDialogOpen(false);
            setFormData({
                title: '',
                description: '',
                start_time: '',
                end_time: '',
                location: '',
                assigned_to: '',
                schedule_type: 'general'
            });
            fetchSchedules();
        } catch (error) {
            toast.error('Failed to create schedule');
        } finally {
            setSubmitting(false);
        }
    };

    const canManage = hasPermission('can_manage_meetings');
    const isServiceProvider = user?.role === 'service_provider';

    return (
        <div className="space-y-6" data-testid="schedule-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Schedule</h1>
                    <p className="text-muted-foreground">
                        {isServiceProvider ? 'Your assigned tasks and schedules' : 'Manage building schedules and tasks'}
                    </p>
                </div>
                <div className="flex items-center gap-4">
                    <Select value={filter} onValueChange={setFilter}>
                        <SelectTrigger className="w-[150px]" data-testid="type-filter">
                            <SelectValue placeholder="Filter"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Types</SelectItem>
                            {Object.entries(scheduleTypes).map(([key, label]) => (
                                <SelectItem key={key} value={key}>{label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    {canManage && (
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogTrigger asChild>
                                <Button data-testid="create-schedule-btn">
                                    <Plus className="mr-2 h-4 w-4"/>
                                    Add Schedule
                                </Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Create Schedule</DialogTitle>
                                    <DialogDescription>Add a new scheduled task or event</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleSubmit} className="space-y-4" data-testid="schedule-form">
                                    <div className="space-y-2">
                                        <Label htmlFor="title">Title</Label>
                                        <Input
                                            id="title"
                                            value={formData.title}
                                            onChange={(e) => setFormData(prev => ( {...prev, title: e.target.value} ))}
                                            required
                                            data-testid="schedule-title-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="schedule_type">Type</Label>
                                        <Select
                                            value={formData.schedule_type}
                                            onValueChange={(value) => setFormData(prev => ( {
                                                ...prev,
                                                schedule_type: value
                                            } ))}
                                        >
                                            <SelectTrigger data-testid="schedule-type-select">
                                                <SelectValue/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                {Object.entries(scheduleTypes).map(([key, label]) => (
                                                    <SelectItem key={key} value={key}>{label}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="start_time">Start Time</Label>
                                            <Input
                                                id="start_time"
                                                type="datetime-local"
                                                value={formData.start_time}
                                                onChange={(e) => setFormData(prev => ( {
                                                    ...prev,
                                                    start_time: e.target.value
                                                } ))}
                                                required
                                                data-testid="schedule-start-input"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="end_time">End Time</Label>
                                            <Input
                                                id="end_time"
                                                type="datetime-local"
                                                value={formData.end_time}
                                                onChange={(e) => setFormData(prev => ( {
                                                    ...prev,
                                                    end_time: e.target.value
                                                } ))}
                                                required
                                                data-testid="schedule-end-input"
                                            />
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="location">Location</Label>
                                        <Input
                                            id="location"
                                            value={formData.location}
                                            onChange={(e) => setFormData(prev => ( {
                                                ...prev,
                                                location: e.target.value
                                            } ))}
                                            placeholder="e.g., Building Lobby"
                                            data-testid="schedule-location-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="description">Description</Label>
                                        <Textarea
                                            id="description"
                                            value={formData.description}
                                            onChange={(e) => setFormData(prev => ( {
                                                ...prev,
                                                description: e.target.value
                                            } ))}
                                            data-testid="schedule-description-input"
                                        />
                                    </div>
                                    {users.length > 0 && (
                                        <div className="space-y-2">
                                            <Label htmlFor="assigned_to">Assign To (Service Provider)</Label>
                                            <Select
                                                value={formData.assigned_to}
                                                onValueChange={(value) => setFormData(prev => ( {
                                                    ...prev,
                                                    assigned_to: value
                                                } ))}
                                            >
                                                <SelectTrigger data-testid="schedule-assignee-select">
                                                    <SelectValue placeholder="Select service provider"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="">Unassigned</SelectItem>
                                                    {users.map(u => (
                                                        <SelectItem key={u.id} value={u.id}>{u.full_name}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}
                                    <Button type="submit" className="w-full" disabled={submitting}>
                                        {submitting ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                                Creating...
                                            </>
                                        ) : (
                                            'Create Schedule'
                                        )}
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    )}
                </div>
            </div>

            {loading ? (
                <div className="space-y-4">
                    {[1, 2, 3].map((i) => (
                        <Card key={i} className="card-dashboard">
                            <CardContent className="p-4">
                                <div className="skeleton h-5 w-3/4 mb-2"/>
                                <div className="skeleton h-4 w-full mb-4"/>
                                <div className="skeleton h-4 w-1/3"/>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : schedules.length === 0 ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <Clock className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Schedules Found</h3>
                        <p className="text-muted-foreground">
                            {isServiceProvider
                                ? 'No tasks have been assigned to you yet.'
                                : 'No schedules have been created yet.'}
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-4">
                    {schedules.map((schedule) => {
                        const status = statusConfig[ schedule.status ] || statusConfig.scheduled;
                        return (
                            <Card
                                key={schedule.id}
                                className="card-dashboard"
                                data-testid={`schedule-${schedule.id}`}
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-start justify-between gap-4 mb-4">
                                        <div>
                                            <div className="flex items-center gap-2 mb-2 flex-wrap">
                                                <h3 className="font-semibold text-lg">{schedule.title}</h3>
                                                <Badge
                                                    variant="outline">{scheduleTypes[ schedule.schedule_type ]}</Badge>
                                                <Badge className={status.class}>{status.label}</Badge>
                                            </div>
                                            <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Calendar className="h-4 w-4"/>
                            {formatDateTime(schedule.start_time)} - {formatDateTime(schedule.end_time)}
                        </span>
                                                {schedule.location && (
                                                    <span className="flex items-center gap-1">
                            <MapPin className="h-4 w-4"/>
                                                        {schedule.location}
                          </span>
                                                )}
                                                {schedule.assigned_to_name && (
                                                    <span className="flex items-center gap-1">
                            <User className="h-4 w-4"/>
                                                        {schedule.assigned_to_name}
                          </span>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                    {schedule.description && (
                                        <p className="text-muted-foreground">{schedule.description}</p>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default SchedulePage;
