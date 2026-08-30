// @featuretrace:governance-meetings — AGM list, motions, voting, and attendance for owners and managers.
// Layer: frontend
// Data flow: AGMVotingPage → GET /agm, /agm/{id}/motions, /agm/{id}/attendance, /chat/users;
//            POST /agm, PUT /agm/{id} → agm + agm_motions + agm_attendance (building-scoped).
// Related: backend/routers/meetings_agm.py
//           frontend/src/hooks/useActiveUnit.ts (the "you" attendance row's unit)
//
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
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
import { Tabs, TabsContent, TabsList, TabsTrigger, } from '../../components/ui/tabs';
import { Progress } from '../../components/ui/progress';
import {
    BarChart3,
    Calendar,
    CheckCircle2,
    ClipboardList,
    Info,
    Loader2,
    MapPin,
    MinusCircle,
    Plus,
    UserCheck,
    UserMinus,
    UserPlus,
    Users,
    Vote,
    XCircle
} from 'lucide-react';
import { formatDateTime } from '../../lib/utils';
import { toast } from 'sonner';
import { useApiData } from '../../hooks/useApiData';
import { useFormData } from '../../hooks/useFormData';
/**
 * @generated FunctionHeader
 * Function: AGMVotingPage
 * Path: frontend/src/pages/dashboard/AGMVotingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AGMVotingPage = () => {
    const {api, hasPermission, user, isAdmin, isManager} = useAuth();
    const {activeUnit} = useActiveUnit();
    const [selectedAgm, setSelectedAgm] = useState(null);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [motionDialogOpen, setMotionDialogOpen] = useState(false);
    const [proxyDialogOpen, setProxyDialogOpen] = useState(false);

    // Use custom hooks to reduce duplication
    const {
        data: agms,
        loading: agmsLoading,
        refetch: refetchAgms
    } = useApiData(
        useCallback(() => api.get('/agm'), [api])
    );

    const fetchMotionsCallback = useCallback(
        () => selectedAgm ? api.get(`/agm/${selectedAgm.id}/motions`) : Promise.resolve({data: []}),
        [api, selectedAgm]
    );

    const {
        data: motions,
        loading: motionsLoading,
        refetch: refetchMotions
    } = useApiData(
        fetchMotionsCallback,
        {dependencies: [selectedAgm]}
    );

    const fetchAttendanceCallback = useCallback(
        () => selectedAgm
            ? api.get(`/agm/${selectedAgm.id}/attendance`)
            : Promise.resolve({data: []}),
        [api, selectedAgm]
    );

    const {
        data: attendance,
        loading: attendanceLoading,
        refetch: refetchAttendance
    } = useApiData(
        fetchAttendanceCallback,
        {dependencies: [selectedAgm]}
    );

    const {
        data: owners,
        loading: ownersLoading
    } = useApiData(
        useCallback(() => api.get('/chat/users'), [api]), // Reuse chat users for owner list
        {enabled: proxyDialogOpen || isManager()}
    );

    const {
        formData: agmFormData,
        handleChange: handleAgmChange,
        handleSubmit: handleAgmSubmit,
        resetForm: resetAgmForm,
        isSubmitting: agmSubmitting,
    } = useFormData({
        title: '',
        date: '',
        location: '',
        agenda: ''
    });

    const {
        formData: motionFormData,
        handleChange: handleMotionChange,
        handleSubmit: handleMotionSubmit,
        resetForm: resetMotionForm,
        isSubmitting: motionSubmitting,
    } = useFormData({
        title: '',
        description: '',
        motion_type: 'ordinary'
    });

    // Set selected AGM when data loads
    useEffect(() => {
        if (agms && agms.length > 0 && !selectedAgm) {
            setSelectedAgm(agms[ 0 ]);
        }
    }, [agms, selectedAgm]);

    const handleCreateAgm = handleAgmSubmit(async (data) => {
        try {
            const agendaItems = data.agenda.split('\n').filter(item => item.trim());
            await api.post('/agm', {
                ...data,
                agenda: agendaItems
            });
            toast.success('AGM created successfully');
            setDialogOpen(false);
            resetAgmForm();
            refetchAgms();
        } catch (error) {
            toast.error('Failed to create AGM');
            throw error;
        }
    });
    /**
     * @generated FunctionHeader
     * Function: handleUpdateAgmStatus
     * Path: frontend/src/pages/dashboard/AGMVotingPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateAgmStatus = async (status) => {
        if (!selectedAgm?.id) return;
        try {
            await api.put(`/agm/${selectedAgm.id}`, {status});
            toast.success(`AGM status updated to ${status}`);
            refetchAgms();
            setSelectedAgm(prev => ( {...prev, status} ));
        } catch (error) {
            toast.error('Failed to update status');
        }
    };

    const handleCreateMotion = handleMotionSubmit(async (data) => {
        try {
            await api.post(`/agm/${selectedAgm.id}/motions`, {
                ...data,
                agm_id: selectedAgm.id
            });
            toast.success('Motion added');
            setMotionDialogOpen(false);
            resetMotionForm();
            refetchMotions();
        } catch (error) {
            toast.error('Failed to add motion');
            throw error;
        }
    });
    /**
     * @generated FunctionHeader
     * Function: handleVote
     * Path: frontend/src/pages/dashboard/AGMVotingPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleVote = async (motionId, vote, isPreVote = false) => {
        try {
            await api.post(`/agm/motions/${motionId}/vote`, {
                motion_id: motionId,
                vote,
                is_pre_vote: isPreVote
            });
            toast.success(isPreVote ? 'Pre-vote recorded' : 'Vote recorded');
            refetchMotions();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to vote');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAttendanceUpdate
     * Path: frontend/src/pages/dashboard/AGMVotingPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAttendanceUpdate = async (status, proxyId = null) => {
        try {
            await api.post(`/agm/${selectedAgm.id}/attendance`, {
                user_id: user.id,
                status,
                proxy_id: proxyId
            });
            toast.success(`Marked as ${status}`);
            refetchAttendance();
            setProxyDialogOpen(false);
        } catch (error) {
            toast.error('Failed to update attendance');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleConfirmAttendance
     * Path: frontend/src/pages/dashboard/AGMVotingPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleConfirmAttendance = async (userId) => {
        try {
            await api.post(`/agm/${selectedAgm.id}/attendance/${userId}/confirm`);
            toast.success('Attendance confirmed');
            refetchAttendance();
        } catch (error) {
            toast.error('Failed to confirm attendance');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getVotePercentage
     * Path: frontend/src/pages/dashboard/AGMVotingPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getVotePercentage = (motion, type) => {
        const total = motion.votes_for + motion.votes_against + motion.votes_abstain;
        if (total === 0) return 0;
        return Math.round(( motion[ `votes_${type}` ] / total ) * 100);
    };

    const motionTypeConfig = {
        ordinary: {
            label: 'Ordinary Resolution',
            description: 'Requires simple majority (>50%)',
            color: 'bg-blue-100 text-blue-800'
        },
        special: {
            label: 'Special Resolution',
            description: 'Requires 75% majority',
            color: 'bg-purple-100 text-purple-800'
        },
        unanimous: {
            label: 'Unanimous Resolution',
            description: 'Requires 100% agreement',
            color: 'bg-red-100 text-red-800'
        }
    };

    const myAttendanceStatus = useMemo(() => {
        if (!attendance || !user?.id) return null;
        return attendance.find(a => a.user_id === user.id);
    }, [attendance, user?.id]);

    return (
        <div className="space-y-6" data-testid="agm-voting-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">AGM & Voting</h1>
                    <p className="text-muted-foreground">Annual General Meetings and resolution voting</p>
                </div>
                <div className="flex gap-2">
                    {hasPermission('can_manage_meetings') && (
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogTrigger asChild>
                                <Button data-testid="create-agm-btn">
                                    <Plus className="mr-2 h-4 w-4"/>
                                    Schedule AGM
                                </Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Schedule AGM</DialogTitle>
                                    <DialogDescription>Create a new Annual General Meeting</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleCreateAgm} className="space-y-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="title">Meeting Title</Label>
                                        <Input
                                            id="title"
                                            name="title"
                                            value={agmFormData.title}
                                            onChange={handleAgmChange}
                                            placeholder="e.g., 2026 Annual General Meeting"
                                            required
                                        />
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="date">Date & Time</Label>
                                            <Input
                                                id="date"
                                                name="date"
                                                type="datetime-local"
                                                value={agmFormData.date}
                                                onChange={handleAgmChange}
                                                required
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="location">Location</Label>
                                            <Input
                                                id="location"
                                                name="location"
                                                value={agmFormData.location}
                                                onChange={handleAgmChange}
                                                placeholder="e.g., Community Room"
                                                required
                                            />
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="agenda">Agenda Items (one per line)</Label>
                                        <Textarea
                                            id="agenda"
                                            name="agenda"
                                            value={agmFormData.agenda}
                                            onChange={handleAgmChange}
                                            placeholder="1. Welcome and apologies&#10;2. Confirm minutes of previous AGM&#10;3. Financial report&#10;..."
                                            rows={5}
                                        />
                                    </div>
                                    <Button type="submit" className="w-full" disabled={agmSubmitting}>
                                        {agmSubmitting ? <><Loader2
                                            className="mr-2 h-4 w-4 animate-spin"/>Creating...</> : 'Create AGM'}
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* AGM List */}
                <Card className="card-dashboard">
                    <CardHeader>
                        <CardTitle className="text-lg">Meetings</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2 px-2">
                        {agmsLoading ? (
                            <div className="space-y-2">
                                {[1, 2, 3].map((i) => <div key={i} className="skeleton h-16 w-full"/>)}
                            </div>
                        ) : agms.length === 0 ? (
                            <div className="py-8 text-center">
                                <Vote className="h-12 w-12 text-muted-foreground/50 mx-auto mb-2"/>
                                <p className="text-sm text-muted-foreground">No AGMs scheduled</p>
                            </div>
                        ) : (
                            agms.map((agm) => (
                                <button
                                    key={agm.id}
                                    onClick={() => setSelectedAgm(agm)}
                                    className={`w-full p-4 rounded-lg text-left transition-colors border ${
                                        selectedAgm?.id === agm.id ? 'bg-primary/10 border-primary shadow-sm' : 'bg-card hover:bg-muted border-transparent'
                                    }`}
                                >
                                    <div className="flex items-center justify-between mb-1">
                                        <p className="font-semibold text-sm">{agm.title}</p>
                                        <Badge
                                            variant={agm.status === 'completed' ? 'secondary' : agm.status === 'in_progress' ? 'default' : 'outline'}
                                            className="text-[10px] uppercase">
                                            {agm.status}
                                        </Badge>
                                    </div>
                                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                        <Calendar className="h-3 w-3"/>
                                        {formatDateTime(agm.date)}
                                    </div>
                                </button>
                            ))
                        )}
                    </CardContent>
                </Card>

                {/* Selected AGM Details & Motions */}
                <div className="lg:col-span-2 space-y-6">
                    {selectedAgm ? (
                        <Tabs defaultValue="details" className="w-full">
                            <TabsList className="grid w-full grid-cols-3 mb-4">
                                <TabsTrigger value="details">Details & Agenda</TabsTrigger>
                                <TabsTrigger value="voting">Resolutions & Voting</TabsTrigger>
                                <TabsTrigger value="attendance">Attendance & Proxies</TabsTrigger>
                            </TabsList>

                            <TabsContent value="details" className="space-y-4">
                                <Card className="card-dashboard">
                                    <CardHeader>
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <CardTitle>{selectedAgm.title}</CardTitle>
                                                <CardDescription className="flex flex-wrap gap-4 mt-2">
                          <span className="flex items-center gap-1">
                            <Calendar className="h-4 w-4"/>
                              {formatDateTime(selectedAgm.date)}
                          </span>
                                                    <span className="flex items-center gap-1">
                            <MapPin className="h-4 w-4"/>
                                                        {selectedAgm.location}
                          </span>
                                                </CardDescription>
                                            </div>
                                            <div className="flex flex-col gap-2 items-end">
                                                <Badge
                                                    variant={selectedAgm.status === 'completed' ? 'secondary' : 'default'}
                                                    className="px-3 py-1">
                                                    {selectedAgm.status.toUpperCase()}
                                                </Badge>
                                                {isManager() && selectedAgm?.status === 'upcoming' && (
                                                    <Button size="sm"
                                                            onClick={() => handleUpdateAgmStatus('in_progress')}>Start
                                                        Meeting</Button>
                                                )}
                                                {isManager() && selectedAgm?.status === 'in_progress' && (
                                                    <Button size="sm" variant="outline"
                                                            onClick={() => handleUpdateAgmStatus('completed')}>Complete
                                                        Meeting</Button>
                                                )}
                                            </div>
                                        </div>
                                    </CardHeader>
                                    <CardContent className="space-y-6">
                                        {selectedAgm.agenda?.length > 0 && (
                                            <div>
                                                <h4 className="font-bold mb-3 flex items-center gap-2 text-primary">
                                                    <ClipboardList className="h-4 w-4"/>
                                                    Order of Business
                                                </h4>
                                                <div className="bg-muted/30 rounded-xl p-4 border border-muted">
                                                    <ol className="list-decimal list-inside space-y-3 text-sm">
                                                        {selectedAgm.agenda.map((item, i) => (
                                                            <li key={i}
                                                                className="pl-2 border-b border-border/50 pb-2 last:border-0 last:pb-0">{item}</li>
                                                        ))}
                                                    </ol>
                                                </div>
                                            </div>
                                        )}

                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div
                                                className="p-4 rounded-xl border bg-amber-50/50 dark:bg-amber-950/10 border-amber-100 dark:border-amber-900/30">
                                                <h5 className="text-xs font-bold text-amber-800 dark:text-amber-400 uppercase mb-2 flex items-center gap-2">
                                                    <Info className="h-3 w-3"/> Important Note
                                                </h5>
                                                <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
                                                    Please ensure your attendance is recorded. If you cannot attend, you
                                                    may nominate a proxy or submit pre-votes on resolutions.
                                                </p>
                                            </div>
                                            <div
                                                className="p-4 rounded-xl border bg-blue-50/50 dark:bg-blue-950/10 border-blue-100 dark:border-blue-900/30">
                                                <h5 className="text-xs font-bold text-blue-800 dark:text-blue-400 uppercase mb-2 flex items-center gap-2">
                                                    <BarChart3 className="h-3 w-3"/> Quorum Requirements
                                                </h5>
                                                <p className="text-xs text-blue-700 dark:text-blue-300 leading-relaxed">
                                                    A quorum is present if individuals entitled to vote for at least 25%
                                                    of the total units of entitlement are present in person or by proxy.
                                                </p>
                                            </div>
                                        </div>
                                    </CardContent>
                                </Card>
                            </TabsContent>

                            <TabsContent value="voting" className="space-y-4">
                                <Card className="card-dashboard">
                                    <CardHeader className="flex flex-row items-center justify-between pb-2">
                                        <div>
                                            <CardTitle className="text-lg">Proposed Resolutions</CardTitle>
                                            <CardDescription>Review and cast your vote on meeting
                                                motions</CardDescription>
                                        </div>
                                        {hasPermission('can_manage_meetings') && (
                                            <Dialog open={motionDialogOpen} onOpenChange={setMotionDialogOpen}>
                                                <DialogTrigger asChild>
                                                    <Button size="sm" variant="outline">
                                                        <Plus className="mr-2 h-4 w-4"/>
                                                        Add Resolution
                                                    </Button>
                                                </DialogTrigger>
                                                <DialogContent>
                                                    <DialogHeader>
                                                        <DialogTitle>Add Resolution</DialogTitle>
                                                        <DialogDescription>Create a formal motion for
                                                            voting</DialogDescription>
                                                    </DialogHeader>
                                                    <form onSubmit={handleCreateMotion} className="space-y-4">
                                                        <div className="space-y-2">
                                                            <Label htmlFor="motion_title">Title</Label>
                                                            <Input
                                                                id="motion_title"
                                                                name="title"
                                                                value={motionFormData.title}
                                                                onChange={handleMotionChange}
                                                                required
                                                            />
                                                        </div>
                                                        <div className="space-y-2">
                                                            <Label htmlFor="motion_type">Resolution Type</Label>
                                                            <Select
                                                                value={motionFormData.motion_type}
                                                                onValueChange={(v) => handleMotionChange('motion_type', v)}
                                                            >
                                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                                <SelectContent>
                                                                    {Object.entries(motionTypeConfig).map(([key, config]) => (
                                                                        <SelectItem key={key}
                                                                                    value={key}>{config.label}</SelectItem>
                                                                    ))}
                                                                </SelectContent>
                                                            </Select>
                                                            <p className="text-xs text-muted-foreground">
                                                                {motionTypeConfig[ motionFormData.motion_type ].description}
                                                            </p>
                                                        </div>
                                                        <div className="space-y-2">
                                                            <Label htmlFor="motion_desc">Full text of resolution</Label>
                                                            <Textarea
                                                                id="motion_desc"
                                                                name="description"
                                                                value={motionFormData.description}
                                                                onChange={handleMotionChange}
                                                                rows={6}
                                                                required
                                                            />
                                                        </div>
                                                        <Button type="submit" className="w-full"
                                                                disabled={motionSubmitting}>
                                                            {motionSubmitting ?
                                                                <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                                            Save Resolution
                                                        </Button>
                                                    </form>
                                                </DialogContent>
                                            </Dialog>
                                        )}
                                    </CardHeader>
                                    <CardContent className="space-y-6 pt-4">
                                        {motionsLoading ? (
                                            <div className="flex justify-center py-12"><Loader2
                                                className="h-8 w-8 animate-spin text-primary"/></div>
                                        ) : motions.length === 0 ? (
                                            <div className="py-12 text-center border-2 border-dashed rounded-2xl">
                                                <Vote className="h-12 w-12 text-muted-foreground/30 mx-auto mb-3"/>
                                                <p className="text-sm text-muted-foreground font-medium">No resolutions
                                                    have been proposed for this AGM</p>
                                            </div>
                                        ) : (
                                            motions.map((motion, index) => {
                                                const typeConfig = motionTypeConfig[ motion.motion_type ];
                                                const totalVotes = motion.votes_for + motion.votes_against + motion.votes_abstain;
                                                const hasVoted = user?.id && motion.voters?.includes(user.id);

                                                return (
                                                    <div key={motion.id}
                                                         className="p-6 border rounded-2xl space-y-5 bg-card/50 shadow-sm transition-all hover:shadow-md">
                                                        <div className="flex items-start justify-between gap-4">
                                                            <div className="space-y-2">
                                                                <div className="flex items-center gap-3">
                                  <span
                                      className="bg-primary/10 text-primary text-xs font-bold h-6 w-6 rounded-full flex items-center justify-center">
                                    {index + 1}
                                  </span>
                                                                    <h4 className="font-bold text-lg leading-tight">{motion.title}</h4>
                                                                </div>
                                                                <Badge
                                                                    className={`${typeConfig.color} border-none shadow-none`}>{typeConfig.label}</Badge>
                                                                <p className="text-sm text-muted-foreground leading-relaxed italic border-l-2 pl-4 mt-4">
                                                                    "{motion.description}"
                                                                </p>
                                                            </div>
                                                            <div className="flex flex-col items-end gap-2">
                                                                <Badge
                                                                    variant={motion.status === 'passed' ? 'default' : motion.status === 'failed' ? 'destructive' : 'secondary'}
                                                                    className="px-3 py-1">
                                                                    {motion.status.toUpperCase()}
                                                                </Badge>
                                                                {hasVoted && (
                                                                    <Badge variant="outline"
                                                                           className="text-green-600 border-green-200 bg-green-50 text-[10px]">
                                                                        <CheckCircle2 className="h-3 w-3 mr-1"/> VOTE
                                                                        CAST
                                                                    </Badge>
                                                                )}
                                                            </div>
                                                        </div>

                                                        {/* Vote Results (Always show aggregated, masking individual) */}
                                                        <div className="space-y-3 bg-muted/20 p-5 rounded-xl">
                                                            <div className="flex items-center gap-3">
                                                                <div
                                                                    className="w-16 text-[10px] font-bold text-muted-foreground uppercase">For
                                                                </div>
                                                                <Progress value={getVotePercentage(motion, 'for')}
                                                                          className="flex-1 h-2 bg-slate-200"/>
                                                                <div
                                                                    className="w-12 text-sm font-semibold text-right">{getVotePercentage(motion, 'for')}%
                                                                </div>
                                                                <div
                                                                    className="text-xs text-muted-foreground w-12">({motion.votes_for})
                                                                </div>
                                                            </div>
                                                            <div className="flex items-center gap-3">
                                                                <div
                                                                    className="w-16 text-[10px] font-bold text-muted-foreground uppercase">Against
                                                                </div>
                                                                <Progress value={getVotePercentage(motion, 'against')}
                                                                          className="flex-1 h-2 bg-slate-200 [&>div]:bg-red-500"/>
                                                                <div
                                                                    className="w-12 text-sm font-semibold text-right">{getVotePercentage(motion, 'against')}%
                                                                </div>
                                                                <div
                                                                    className="text-xs text-muted-foreground w-12">({motion.votes_against})
                                                                </div>
                                                            </div>
                                                            <div className="flex items-center gap-3">
                                                                <div
                                                                    className="w-16 text-[10px] font-bold text-muted-foreground uppercase">Abstain
                                                                </div>
                                                                <Progress value={getVotePercentage(motion, 'abstain')}
                                                                          className="flex-1 h-2 bg-slate-200 [&>div]:bg-slate-400"/>
                                                                <div
                                                                    className="w-12 text-sm font-semibold text-right">{getVotePercentage(motion, 'abstain')}%
                                                                </div>
                                                                <div
                                                                    className="text-xs text-muted-foreground w-12">({motion.votes_abstain})
                                                                </div>
                                                            </div>
                                                            <div
                                                                className="pt-2 border-t border-border/50 mt-2 flex justify-between items-center">
                                                                <span
                                                                    className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Participation</span>
                                                                <span className="text-xs font-bold">{totalVotes} units voted</span>
                                                            </div>
                                                        </div>

                                                        {/* Vote Buttons */}
                                                        {motion.status === 'pending' && !hasVoted && (
                                                            <div className="flex flex-col sm:flex-row gap-3 pt-2">
                                                                <Button
                                                                    variant="outline"
                                                                    className="flex-1 h-11 border-green-200 text-green-700 hover:bg-green-50 hover:text-green-800 font-bold"
                                                                    onClick={() => handleVote(motion.id, 'for', selectedAgm.status === 'upcoming')}
                                                                >
                                                                    <CheckCircle2 className="mr-2 h-4 w-4"/>
                                                                    {selectedAgm.status === 'upcoming' ? 'Pre-vote FOR' : 'Vote FOR'}
                                                                </Button>
                                                                <Button
                                                                    variant="outline"
                                                                    className="flex-1 h-11 border-red-200 text-red-700 hover:bg-red-50 hover:text-red-800 font-bold"
                                                                    onClick={() => handleVote(motion.id, 'against', selectedAgm.status === 'upcoming')}
                                                                >
                                                                    <XCircle className="mr-2 h-4 w-4"/>
                                                                    {selectedAgm.status === 'upcoming' ? 'Pre-vote AGAINST' : 'Vote AGAINST'}
                                                                </Button>
                                                                <Button
                                                                    variant="outline"
                                                                    className="flex-1 h-11 font-bold"
                                                                    onClick={() => handleVote(motion.id, 'abstain', selectedAgm.status === 'upcoming')}
                                                                >
                                                                    <MinusCircle
                                                                        className="mr-2 h-4 w-4 text-muted-foreground"/>
                                                                    Abstain
                                                                </Button>
                                                            </div>
                                                        )}

                                                        {isAdmin() && (
                                                            <div className="pt-2">
                                                                <Button variant="ghost" size="sm"
                                                                        className="text-[10px] h-6 uppercase font-bold text-muted-foreground hover:text-primary">
                                                                    View Audit Trail (Admin Only)
                                                                </Button>
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })
                                        )}
                                    </CardContent>
                                </Card>
                            </TabsContent>

                            <TabsContent value="attendance" className="space-y-4">
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                                    {/* My Status */}
                                    <Card className="md:col-span-1">
                                        <CardHeader>
                                            <CardTitle className="text-base">Your Status</CardTitle>
                                        </CardHeader>
                                        <CardContent className="space-y-4">
                                            {selectedAgm.status !== 'completed' ? (
                                                <>
                                                    <div className="flex flex-col gap-3">
                                                        <Button
                                                            variant={myAttendanceStatus?.status === 'attending' ? 'default' : 'outline'}
                                                            className="w-full justify-start h-12"
                                                            onClick={() => handleAttendanceUpdate('attending')}
                                                        >
                                                            <UserCheck className="mr-3 h-5 w-5"/>
                                                            I am attending
                                                        </Button>
                                                        <Button
                                                            variant={myAttendanceStatus?.status === 'apology' ? 'default' : 'outline'}
                                                            className="w-full justify-start h-12"
                                                            onClick={() => handleAttendanceUpdate('apology')}
                                                        >
                                                            <UserMinus className="mr-3 h-5 w-5"/>
                                                            I am sending apologies
                                                        </Button>
                                                    </div>

                                                    <div className="pt-4 border-t space-y-4">
                                                        <div>
                                                            <Label
                                                                className="text-xs font-bold uppercase text-muted-foreground mb-2 block">Proxy
                                                                Representation</Label>
                                                            <Dialog open={proxyDialogOpen}
                                                                    onOpenChange={setProxyDialogOpen}>
                                                                <DialogTrigger asChild>
                                                                    <Button variant="outline"
                                                                            className="w-full h-12 justify-start border-dashed border-primary/50 text-primary">
                                                                        <UserPlus className="mr-3 h-5 w-5"/>
                                                                        Nominate Proxy
                                                                    </Button>
                                                                </DialogTrigger>
                                                                <DialogContent>
                                                                    <DialogHeader>
                                                                        <DialogTitle>Nominate Proxy</DialogTitle>
                                                                        <DialogDescription>
                                                                            Nominate another registered owner to vote on
                                                                            your behalf at the AGM.
                                                                        </DialogDescription>
                                                                    </DialogHeader>
                                                                    <div className="space-y-4 py-4">
                                                                        <Label>Select Proxy Holder</Label>
                                                                        <Select
                                                                            onValueChange={(val) => handleAttendanceUpdate('apology', val)}>
                                                                            <SelectTrigger>
                                                                                <SelectValue
                                                                                    placeholder="Search registered owners..."/>
                                                                            </SelectTrigger>
                                                                            <SelectContent>
                                                                                {owners?.filter(o => o.id !== user?.id).map(owner => (
                                                                                    <SelectItem key={owner.id}
                                                                                                value={owner.id}>
                                                                                        {owner.full_name} (Unit {owner.unit_number})
                                                                                    </SelectItem>
                                                                                ))}
                                                                            </SelectContent>
                                                                        </Select>
                                                                        <div
                                                                            className="p-3 bg-blue-50 rounded-lg text-xs text-blue-800 leading-relaxed border border-blue-100">
                                                                            <strong>Note:</strong> Nominated proxies
                                                                            will receive a digital notification. You can
                                                                            still pre-vote if you nominate a proxy; your
                                                                            pre-vote takes precedence if cast.
                                                                        </div>
                                                                    </div>
                                                                </DialogContent>
                                                            </Dialog>
                                                        </div>

                                                        {myAttendanceStatus?.proxy_id && (
                                                            <div
                                                                className="p-3 rounded-xl border bg-primary/5 border-primary/20">
                                                                <p className="text-[10px] font-bold text-primary uppercase mb-1">Current
                                                                    Proxy</p>
                                                                <p className="text-sm font-semibold">
                                                                    {owners?.find(o => o.id === myAttendanceStatus.proxy_id)?.full_name || 'Nominated Proxy'}
                                                                </p>
                                                                <Button variant="link" size="sm"
                                                                        className="h-auto p-0 text-xs mt-1 text-destructive"
                                                                        onClick={() => handleAttendanceUpdate('attending', null)}>
                                                                    Revoke Proxy
                                                                </Button>
                                                            </div>
                                                        )}
                                                    </div>
                                                </>
                                            ) : (
                                                <div className="text-center py-8">
                                                    <Badge
                                                        className="mb-2">{myAttendanceStatus?.status || 'Unknown'}</Badge>
                                                    <p className="text-sm text-muted-foreground">AGM is completed</p>
                                                </div>
                                            )}
                                        </CardContent>
                                    </Card>

                                    {/* Attendance List (Managers Only) */}
                                    <Card className="md:col-span-2">
                                        <CardHeader className="flex flex-row items-center justify-between pb-2">
                                            <div>
                                                <CardTitle className="text-base">Attendance Register</CardTitle>
                                                <CardDescription>Official record of meeting
                                                    participation</CardDescription>
                                            </div>
                                            <Badge variant="outline"
                                                   className="font-mono">{attendance?.length || 0} RESPONSES</Badge>
                                        </CardHeader>
                                        <CardContent>
                                            {!isManager() ? (
                                                attendanceLoading ? (
                                                    <div className="flex justify-center py-8"><Loader2
                                                        className="h-6 w-6 animate-spin"/></div>
                                                ) : attendance && attendance.length > 0 ? (
                                                    <div className="overflow-hidden rounded-xl border">
                                                        <table className="w-full text-sm">
                                                            <thead className="bg-muted/50 border-b">
                                                            <tr>
                                                                <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Owner</th>
                                                                <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Response</th>
                                                                <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Unit
                                                                    Vote
                                                                </th>
                                                                <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Verification</th>
                                                            </tr>
                                                            </thead>
                                                            <tbody className="divide-y">
                                                            {attendance.map((record) => (
                                                                <tr key={record.user_id} className="hover:bg-muted/30">
                                                                    <td className="px-4 py-3">
                                                                        <div
                                                                            className="font-semibold">{record.full_name || user?.full_name || 'You'}</div>
                                                                        <div
                                                                            className="text-[10px] text-muted-foreground">Unit {record.unit_number || activeUnit}</div>
                                                                    </td>
                                                                    <td className="px-4 py-3">
                                                                        <Badge
                                                                            variant={record.status === 'attending' ? 'default' : 'outline'}
                                                                            className="text-[10px] h-5">
                                                                            {record.status?.toUpperCase()}
                                                                        </Badge>
                                                                        {record.proxy_name && (
                                                                            <div
                                                                                className="text-[10px] text-primary mt-1 font-bold flex items-center gap-1">
                                                                                <Users
                                                                                    className="h-2 w-2"/> PROXY: {record.proxy_name}
                                                                            </div>
                                                                        )}
                                                                    </td>
                                                                    <td className="px-4 py-3">
                                                                        {record.unit_vote_counted ? (
                                                                            <Badge
                                                                                className="bg-blue-100 text-blue-700 hover:bg-blue-100 border-none shadow-none text-[10px]">COUNTED</Badge>
                                                                        ) : (
                                                                            <span
                                                                                className="text-[10px] text-muted-foreground italic">Co-owner vote</span>
                                                                        )}
                                                                    </td>
                                                                    <td className="px-4 py-3">
                                                                        {record.confirmed_by_admin ? (
                                                                            <Badge
                                                                                className="bg-green-100 text-green-700 hover:bg-green-100 border-none shadow-none text-[10px]">VERIFIED</Badge>
                                                                        ) : (
                                                                            <span
                                                                                className="text-[10px] text-muted-foreground italic">Pending</span>
                                                                        )}
                                                                    </td>
                                                                </tr>
                                                            ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                ) : (
                                                    <div className="py-8 text-center text-sm text-muted-foreground">
                                                        Your attendance has not been recorded yet. Use the buttons above
                                                        to register.
                                                    </div>
                                                )
                                            ) : attendanceLoading ? (
                                                <div className="flex justify-center py-8"><Loader2
                                                    className="h-6 w-6 animate-spin"/></div>
                                            ) : (
                                                <div className="overflow-hidden rounded-xl border">
                                                    <table className="w-full text-sm">
                                                        <thead className="bg-muted/50 border-b">
                                                        <tr>
                                                            <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Resident</th>
                                                            <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Response</th>
                                                            <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Unit
                                                                Vote
                                                            </th>
                                                            <th className="px-4 py-3 text-left font-bold text-[10px] uppercase text-muted-foreground">Verification</th>
                                                            <th className="px-4 py-3 text-right font-bold text-[10px] uppercase text-muted-foreground">Actions</th>
                                                        </tr>
                                                        </thead>
                                                        <tbody className="divide-y">
                                                        {attendance.length === 0 ? (
                                                            <tr>
                                                                <td colSpan={5}
                                                                    className="px-4 py-8 text-center text-muted-foreground">No
                                                                    attendance records yet
                                                                </td>
                                                            </tr>
                                                        ) : (
                                                            attendance.map((record) => {
                                                                const attendee = owners?.find(o => o.id === record.user_id);
                                                                return (
                                                                    <tr key={record.user_id}
                                                                        className="hover:bg-muted/30">
                                                                        <td className="px-4 py-3">
                                                                            <div
                                                                                className="font-semibold">{record.full_name || attendee?.full_name || 'Unknown User'}</div>
                                                                            <div
                                                                                className="text-[10px] text-muted-foreground">Unit {record.unit_number || attendee?.unit_number}</div>
                                                                        </td>
                                                                        <td className="px-4 py-3">
                                                                            <Badge
                                                                                variant={record.status === 'attending' ? 'default' : 'outline'}
                                                                                className="text-[10px] h-5">
                                                                                {record.status.toUpperCase()}
                                                                            </Badge>
                                                                            {record.proxy_id && (
                                                                                <div
                                                                                    className="text-[10px] text-primary mt-1 font-bold flex items-center gap-1">
                                                                                    <Users
                                                                                        className="h-2 w-2"/> PROXY: {record.proxy_name || owners?.find(o => o.id === record.proxy_id)?.full_name || 'Nominated'}
                                                                                </div>
                                                                            )}
                                                                        </td>
                                                                        <td className="px-4 py-3">
                                                                            {record.unit_vote_counted ? (
                                                                                <Badge
                                                                                    className="bg-blue-100 text-blue-700 hover:bg-blue-100 border-none shadow-none text-[10px]">COUNTED</Badge>
                                                                            ) : (
                                                                                <span
                                                                                    className="text-[10px] text-muted-foreground italic">Co-owner</span>
                                                                            )}
                                                                        </td>
                                                                        <td className="px-4 py-3">
                                                                            {record.confirmed_by_admin ? (
                                                                                <Badge
                                                                                    className="bg-green-100 text-green-700 hover:bg-green-100 border-none shadow-none text-[10px]">
                                                                                    VERIFIED PRESENT
                                                                                </Badge>
                                                                            ) : (
                                                                                <span
                                                                                    className="text-[10px] text-muted-foreground italic">Unverified</span>
                                                                            )}
                                                                        </td>
                                                                        <td className="px-4 py-3 text-right">
                                                                            {!record.confirmed_by_admin && record.status === 'attending' && (
                                                                                <Button size="sm" variant="ghost"
                                                                                        className="h-7 text-[10px] font-bold text-primary hover:bg-primary/10"
                                                                                        onClick={() => handleConfirmAttendance(record.user_id)}>
                                                                                    CONFIRM PRESENCE
                                                                                </Button>
                                                                            )}
                                                                        </td>
                                                                    </tr>
                                                                );
                                                            })
                                                        )}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            )}
                                        </CardContent>
                                    </Card>
                                </div>
                            </TabsContent>
                        </Tabs>
                    ) : (
                        <Card className="card-dashboard">
                            <CardContent className="py-24 text-center">
                                <div
                                    className="bg-primary/5 h-20 w-20 rounded-full flex items-center justify-center mx-auto mb-6">
                                    <Vote className="h-10 w-10 text-primary/40"/>
                                </div>
                                <h3 className="text-xl font-bold mb-2">Select an AGM Meeting</h3>
                                <p className="text-muted-foreground max-w-xs mx-auto">Choose a scheduled meeting from
                                    the side panel to view details, record your attendance, or cast votes.</p>
                            </CardContent>
                        </Card>
                    )}
                </div>
            </div>
        </div>
    );
};

export default AGMVotingPage;
