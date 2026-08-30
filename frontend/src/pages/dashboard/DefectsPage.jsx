import React, { useCallback, useMemo, useState } from 'react';
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
import {
    AlertTriangle,
    Calendar,
    CheckCircle2,
    Clock,
    FileWarning,
    Loader2,
    MapPin,
    Plus,
    Shield,
    Wrench
} from 'lucide-react';
import { formatDate } from '../../lib/utils';
import { toast } from 'sonner';
import { useFormData } from '../../hooks/useFormData';
import { useApiData } from '../../hooks/useApiData';

// Memoized config objects to prevent recreation on every render
const statusConfig = {
    reported: {label: 'Reported', color: 'bg-blue-100 text-blue-800', icon: Clock},
    acknowledged: {label: 'Acknowledged', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    in_progress: {label: 'In Progress', color: 'bg-orange-100 text-orange-800', icon: Wrench},
    resolved: {label: 'Resolved', color: 'bg-green-100 text-green-800', icon: CheckCircle2},
    closed: {label: 'Closed', color: 'bg-gray-100 text-gray-800', icon: CheckCircle2}
};

const severityConfig = {
    low: {label: 'Low', color: 'bg-gray-100 text-gray-800'},
    medium: {label: 'Medium', color: 'bg-yellow-100 text-yellow-800'},
    high: {label: 'High', color: 'bg-orange-100 text-orange-800'},
    critical: {label: 'Critical', color: 'bg-red-100 text-red-800'}
};

const defectTypes = [
    {value: 'structural', label: 'Structural'},
    {value: 'waterproofing', label: 'Waterproofing'},
    {value: 'electrical', label: 'Electrical'},
    {value: 'plumbing', label: 'Plumbing'},
    {value: 'finishing', label: 'Finishing/Cosmetic'},
    {value: 'fire_safety', label: 'Fire Safety'},
    {value: 'hvac', label: 'HVAC/Air Conditioning'},
    {value: 'other', label: 'Other'}
];
/**
 * @generated FunctionHeader
 * Function: DefectsPage
 * Path: frontend/src/pages/dashboard/DefectsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const DefectsPage = () => {
    const {api, hasPermission} = useAuth();
    const [dialogOpen, setDialogOpen] = useState(false);
    const [detailsOpen, setDetailsOpen] = useState(false);
    const [selectedDefect, setSelectedDefect] = useState(null);
    const [statusFilter, setStatusFilter] = useState('all');

    // Use custom hooks for cleaner state management
    // Compute initial date dynamically when form data is created
    const initialFormData = useMemo(() => ( {
        title: '',
        description: '',
        location: '',
        defect_type: 'other',
        severity: 'medium',
        discovered_date: new Date().toISOString().split('T')[ 0 ],
        warranty_claim: false,
        images: []
    } ), []);

    const {formData, handleChange, resetForm, handleSubmit, isSubmitting} = useFormData(initialFormData);

    const canManage = hasPermission('can_manage_meetings');

    // Memoize fetch function to prevent infinite loops
    const fetchDefects = useCallback(() => {
        const params = statusFilter && statusFilter !== 'all' ? `?status=${statusFilter}` : '';
        return api.get(`/defects${params}`);
    }, [api, statusFilter]);

    // Use optimized data fetching hook with automatic refetch on filter change
    const {data: defects, loading, refetch: refetchDefects} = useApiData(
        fetchDefects,
        {dependencies: [statusFilter]}
    );

    const onSubmit = handleSubmit(async (data) => {
        await api.post('/defects', data);
        toast.success('Defect reported successfully');
        setDialogOpen(false);
        resetForm();
        refetchDefects();
    });
    /**
     * @generated FunctionHeader
     * Function: handleStatusUpdate
     * Path: frontend/src/pages/dashboard/DefectsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleStatusUpdate = async (defectId, status, notes = '') => {
        try {
            await api.put(`/defects/${defectId}/status?status=${status}&resolution_notes=${encodeURIComponent(notes)}`);
            toast.success('Status updated');
            refetchDefects();
            setDetailsOpen(false);
        } catch (error) {
            toast.error('Failed to update status');
        }
    };

    // Memoize computed stats to prevent recalculation on every render
    const stats = useMemo(() => {
        if (!defects) return {total: 0, open: 0, critical: 0, warranty: 0};
        return {
            total: defects.length,
            open: defects.filter(d => ['reported', 'acknowledged', 'in_progress'].includes(d.status)).length,
            critical: defects.filter(d => d.severity === 'critical' && d.status !== 'closed').length,
            warranty: defects.filter(d => d.warranty_claim && d.status !== 'closed').length
        };
    }, [defects]);

    return (
        <div className="space-y-6" data-testid="defects-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Building Defects</h1>
                    <p className="text-muted-foreground">Track and manage building defects and warranty claims</p>
                </div>
                <div className="flex gap-2">
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-[150px]">
                            <SelectValue placeholder="All Status"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Status</SelectItem>
                            {Object.entries(statusConfig).map(([key, config]) => (
                                <SelectItem key={key} value={key}>{config.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                        <DialogTrigger asChild>
                            <Button data-testid="report-defect-btn">
                                <Plus className="mr-2 h-4 w-4"/>
                                Report Defect
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-lg">
                            <DialogHeader>
                                <DialogTitle>Report Building Defect</DialogTitle>
                                <DialogDescription>Document a defect for tracking and resolution</DialogDescription>
                            </DialogHeader>
                            <form onSubmit={onSubmit} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="title">Defect Title</Label>
                                    <Input
                                        id="title"
                                        name="title"
                                        value={formData.title}
                                        onChange={handleChange}
                                        placeholder="e.g., Water leak in basement"
                                        required
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="type">Defect Type</Label>
                                        <Select value={formData.defect_type}
                                                onValueChange={(v) => handleChange('defect_type', v)}>
                                            <SelectTrigger><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                {defectTypes.map(t => <SelectItem key={t.value}
                                                                                  value={t.value}>{t.label}</SelectItem>)}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="severity">Severity</Label>
                                        <Select value={formData.severity}
                                                onValueChange={(v) => handleChange('severity', v)}>
                                            <SelectTrigger><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                {Object.entries(severityConfig).map(([key, config]) => (
                                                    <SelectItem key={key} value={key}>{config.label}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="location">Location</Label>
                                        <Input
                                            id="location"
                                            name="location"
                                            value={formData.location}
                                            onChange={handleChange}
                                            placeholder="e.g., Level B1 parking"
                                            required
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="discovered">Discovered Date</Label>
                                        <Input
                                            id="discovered"
                                            name="discovered_date"
                                            type="date"
                                            value={formData.discovered_date}
                                            onChange={handleChange}
                                            required
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="description">Description</Label>
                                    <Textarea
                                        id="description"
                                        name="description"
                                        value={formData.description}
                                        onChange={handleChange}
                                        placeholder="Describe the defect in detail..."
                                        rows={3}
                                        required
                                    />
                                </div>
                                <div className="flex items-center gap-2">
                                    <input
                                        type="checkbox"
                                        id="warranty"
                                        name="warranty_claim"
                                        checked={formData.warranty_claim}
                                        onChange={handleChange}
                                        className="rounded"
                                    />
                                    <Label htmlFor="warranty" className="font-normal">This is a warranty claim</Label>
                                </div>
                                <Button type="submit" className="w-full" disabled={isSubmitting}>
                                    {isSubmitting ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/>Submitting...</> : 'Report Defect'}
                                </Button>
                            </form>
                        </DialogContent>
                    </Dialog>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <FileWarning className="h-8 w-8 text-blue-500"/>
                            <div>
                                <p className="text-2xl font-bold">{stats.total}</p>
                                <p className="text-xs text-muted-foreground">Total Defects</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <Clock className="h-8 w-8 text-orange-500"/>
                            <div>
                                <p className="text-2xl font-bold">{stats.open}</p>
                                <p className="text-xs text-muted-foreground">Open Issues</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <AlertTriangle className="h-8 w-8 text-red-500"/>
                            <div>
                                <p className="text-2xl font-bold">{stats.critical}</p>
                                <p className="text-xs text-muted-foreground">Critical</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-3">
                            <Shield className="h-8 w-8 text-purple-500"/>
                            <div>
                                <p className="text-2xl font-bold">{stats.warranty}</p>
                                <p className="text-xs text-muted-foreground">Warranty Claims</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Defects List */}
            {loading ? (
                <div className="space-y-4">
                    {[1, 2, 3].map((i) => <div key={i} className="skeleton h-24 w-full"/>)}
                </div>
            ) : ( !defects || defects.length === 0 ) ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <CheckCircle2 className="h-16 w-16 text-green-500/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Defects Found</h3>
                        <p className="text-muted-foreground">
                            {statusFilter ? 'No defects match this filter' : 'Great news! No building defects have been reported.'}
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-4">
                    {defects.map((defect) => {
                        const status = statusConfig[ defect.status ];
                        const severity = severityConfig[ defect.severity ];
                        const StatusIcon = status.icon;

                        return (
                            <Card
                                key={defect.id}
                                className="card-dashboard cursor-pointer hover:shadow-md transition-shadow"
                                onClick={() => {
                                    setSelectedDefect(defect);
                                    setDetailsOpen(true);
                                }}
                            >
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2 mb-2 flex-wrap">
                                                <h3 className="font-semibold">{defect.title}</h3>
                                                <Badge className={status.color}>
                                                    <StatusIcon className="h-3 w-3 mr-1"/>
                                                    {status.label}
                                                </Badge>
                                                <Badge className={severity.color}>{severity.label}</Badge>
                                                {defect.warranty_claim && (
                                                    <Badge variant="outline"
                                                           className="border-purple-500 text-purple-600">
                                                        <Shield className="h-3 w-3 mr-1"/>
                                                        Warranty
                                                    </Badge>
                                                )}
                                            </div>
                                            <p className="text-sm text-muted-foreground mb-3 line-clamp-2">{defect.description}</p>
                                            <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <MapPin className="h-3 w-3"/>
                            {defect.location}
                        </span>
                                                <span
                                                    className="capitalize">{defect.defect_type.replace('_', ' ')}</span>
                                                <span className="flex items-center gap-1">
                          <Calendar className="h-3 w-3"/>
                          Discovered {formatDate(defect.discovered_date)}
                        </span>
                                            </div>
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}

            {/* Details Dialog */}
            <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
                <DialogContent className="max-w-2xl">
                    {selectedDefect && (
                        <>
                            <DialogHeader>
                                <div className="flex items-center gap-2 mb-2">
                                    <Badge className={statusConfig[ selectedDefect.status ]?.color}>
                                        {statusConfig[ selectedDefect.status ]?.label}
                                    </Badge>
                                    <Badge className={severityConfig[ selectedDefect.severity ]?.color}>
                                        {severityConfig[ selectedDefect.severity ]?.label}
                                    </Badge>
                                    {selectedDefect.warranty_claim && (
                                        <Badge variant="outline" className="border-purple-500 text-purple-600">Warranty
                                            Claim</Badge>
                                    )}
                                </div>
                                <DialogTitle>{selectedDefect.title}</DialogTitle>
                            </DialogHeader>

                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-4 text-sm">
                                    <div>
                                        <p className="text-muted-foreground">Type</p>
                                        <p className="font-medium capitalize">{selectedDefect.defect_type.replace('_', ' ')}</p>
                                    </div>
                                    <div>
                                        <p className="text-muted-foreground">Location</p>
                                        <p className="font-medium">{selectedDefect.location}</p>
                                    </div>
                                    <div>
                                        <p className="text-muted-foreground">Discovered</p>
                                        <p className="font-medium">{formatDate(selectedDefect.discovered_date)}</p>
                                    </div>
                                    <div>
                                        <p className="text-muted-foreground">Reported By</p>
                                        <p className="font-medium">{selectedDefect.reported_by_name}</p>
                                    </div>
                                </div>

                                <div>
                                    <p className="text-muted-foreground mb-1">Description</p>
                                    <p className="whitespace-pre-wrap">{selectedDefect.description}</p>
                                </div>

                                {selectedDefect.resolution_notes && (
                                    <div className="p-4 bg-green-50 rounded-lg">
                                        <p className="text-sm font-medium text-green-800 mb-1">Resolution Notes</p>
                                        <p className="text-sm text-green-700">{selectedDefect.resolution_notes}</p>
                                    </div>
                                )}

                                {canManage && !['resolved', 'closed'].includes(selectedDefect.status) && (
                                    <div className="pt-4 border-t space-y-3">
                                        <p className="font-medium">Update Status</p>
                                        <div className="flex flex-wrap gap-2">
                                            {selectedDefect.status === 'reported' && (
                                                <Button size="sm"
                                                        onClick={() => handleStatusUpdate(selectedDefect.id, 'acknowledged')}>
                                                    Acknowledge
                                                </Button>
                                            )}
                                            {['reported', 'acknowledged'].includes(selectedDefect.status) && (
                                                <Button size="sm"
                                                        onClick={() => handleStatusUpdate(selectedDefect.id, 'in_progress')}>
                                                    Start Work
                                                </Button>
                                            )}
                                            {selectedDefect.status === 'in_progress' && (
                                                <Button size="sm" variant="default" onClick={() => {
                                                    const notes = prompt('Resolution notes:');
                                                    if (notes) handleStatusUpdate(selectedDefect.id, 'resolved', notes);
                                                }}>
                                                    Mark Resolved
                                                </Button>
                                            )}
                                            {selectedDefect.status === 'resolved' && (
                                                <Button size="sm"
                                                        onClick={() => handleStatusUpdate(selectedDefect.id, 'closed')}>
                                                    Close
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default DefectsPage;
