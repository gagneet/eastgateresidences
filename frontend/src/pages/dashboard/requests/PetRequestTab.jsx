import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Textarea } from '../../../components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Checkbox } from '../../../components/ui/checkbox';
import { Card, CardContent } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Separator } from '../../../components/ui/separator';
import { CheckCircle, CheckCircle2, Clock, Dog, Loader2, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { formatDate } from '../../../lib/utils';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';
import {trackRequestCatalogueEvent} from '@/config/requestCatalogue';

const STATUS_CONFIG = {
    pending: {label: 'Pending Review', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800', icon: CheckCircle},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800', icon: XCircle},
    conditional: {label: 'Conditional Approval', color: 'bg-blue-100 text-blue-800', icon: CheckCircle},
    under_review: {label: 'Under Review', color: 'bg-purple-100 text-purple-800', icon: Clock},
};

const EMPTY_FORM = {
    pet_type: 'dog', breed: '', size: 'medium', age: '', pet_name: '',
    council_registration: '', vaccination_current: false, house_trained: false, reason: '',
};
/**
 * @generated FunctionHeader
 * Function: PetRequestTab
 * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PetRequestTab = () => {
    const {api, isManager, user} = useAuth();
    const canManage = isManager();

    const [loading, setLoading] = useState(false);
    const [requests, setRequests] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const [formData, setFormData] = useState(EMPTY_FORM);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: requests,
        statusConfig: STATUS_CONFIG,
        tab: 'pet',
    });

    // Manager approval modal state
    const [actionTarget, setActionTarget] = useState(null);  // { id, action: 'approve'|'decline'|'conditional' }
    const [actionNotes, setActionNotes] = useState('');
    const [actionConds, setActionConds] = useState('');
    const [actionLoading, setActionLoading] = useState(false);

    const fetchRequests = useCallback(async () => {
        try {
            const res = await api.get('/requests/pets');
            setRequests(res.data);
        } catch {
            /* silently fail on initial load */
        }
    }, [api]);

    useEffect(() => {
        fetchRequests();
    }, [fetchRequests]);
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (e) => {
        const {name, value} = e.target;
        setFormData(prev => ( {...prev, [ name ]: value} ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.breed || !formData.pet_name || !formData.age) {
            toast.error('Please fill in all required fields');
            return;
        }
        setLoading(true);
        try {
            await api.post('/requests/pets', {...formData, age: parseInt(formData.age)});
            trackRequestCatalogueEvent('request_form_submitted', {
                definition_key: 'pet',
                definition_version: 1,
                role: user?.effective_role || user?.role,
                status: 'submitted',
            });
            toast.success('Pet request submitted successfully');
            setFormData(EMPTY_FORM);
            setShowForm(false);
            fetchRequests();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to submit pet request');
        } finally {
            setLoading(false);
        }
    };
    // ── Manager approval actions ────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: openAction
     * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openAction = (request, action) => {
        setActionTarget({id: request.id, name: request.pet_name, action});
        setActionNotes('');
        setActionConds('');
    };
    /**
     * @generated FunctionHeader
     * Function: closeAction
     * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const closeAction = () => setActionTarget(null);
    /**
     * @generated FunctionHeader
     * Function: submitAction
     * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const submitAction = async () => {
        if (!actionTarget) return;
        const {id, action} = actionTarget;

        const statusMap = {approve: 'approved', decline: 'rejected', conditional: 'conditional'};
        const newStatus = statusMap[ action ];

        if (action === 'conditional' && !actionConds.trim()) {
            toast.error('Please specify the conditions for conditional approval.');
            return;
        }

        const params = new URLSearchParams({status: newStatus});
        if (actionNotes.trim()) params.append('notes', actionNotes.trim());
        if (actionConds.trim()) params.append('conditions', actionConds.trim());

        setActionLoading(true);
        try {
            await api.put(`/requests/pets/${id}/status?${params.toString()}`);
            toast.success(`Pet request ${newStatus}.`);
            closeAction();
            fetchRequests();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to update request status.');
        } finally {
            setActionLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleMarkUnderReview
     * Path: frontend/src/pages/dashboard/requests/PetRequestTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleMarkUnderReview = async (petId) => {
        try {
            await api.put(`/requests/pets/${petId}/status?status=under_review`);
            toast.success('Marked as Under Review.');
            fetchRequests();
        } catch {
            toast.error('Failed to update status.');
        }
    };

    // ── Render ──────────────────────────────────────────────────────────────

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">
                        {canManage ? 'All Pet Requests' : 'Pet Requests'}
                    </h3>
                    <p className="text-sm text-muted-foreground">
                        {canManage
                            ? 'Review and approve pet requests from residents.'
                            : 'Request approval to keep a pet in your unit.'}
                    </p>
                </div>
                {!canManage && (
                    <Button onClick={() => setShowForm(!showForm)}>
                        <Dog className="mr-2 h-4 w-4"/>
                        {showForm ? 'Cancel' : 'New Pet Request'}
                    </Button>
                )}
            </div>

            {/* Submit Form (owner only) */}
            {showForm && !canManage && (
                <Card className="border-primary/20">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label htmlFor="pet_type">Pet Type *</Label>
                                    <Select value={formData.pet_type}
                                            onValueChange={v => handleChange({target: {name: 'pet_type', value: v}})}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="dog">Dog</SelectItem>
                                            <SelectItem value="cat">Cat</SelectItem>
                                            <SelectItem value="bird">Bird</SelectItem>
                                            <SelectItem value="fish">Fish</SelectItem>
                                            <SelectItem value="reptile">Reptile</SelectItem>
                                            <SelectItem value="other">Other</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="pet_name">Pet Name *</Label>
                                    <Input id="pet_name" name="pet_name" value={formData.pet_name}
                                           onChange={handleChange} placeholder="Max, Fluffy, etc." required/>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="breed">Breed *</Label>
                                    <Input id="breed" name="breed" value={formData.breed}
                                           onChange={handleChange} placeholder="Golden Retriever, Tabby, etc."
                                           required/>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="age">Age (years) *</Label>
                                    <Input id="age" name="age" type="number" min="0" max="30"
                                           value={formData.age} onChange={handleChange}
                                           placeholder="Age in years" required/>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="size">Size *</Label>
                                    <Select value={formData.size}
                                            onValueChange={v => handleChange({target: {name: 'size', value: v}})}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="small">Small (&lt; 10kg)</SelectItem>
                                            <SelectItem value="medium">Medium (10–25kg)</SelectItem>
                                            <SelectItem value="large">Large (&gt; 25kg)</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="council_registration">Council Registration Number</Label>
                                    <Input id="council_registration" name="council_registration"
                                           value={formData.council_registration} onChange={handleChange}
                                           placeholder="Optional"/>
                                </div>
                            </div>

                            <div className="space-y-3">
                                <div className="flex items-center space-x-2">
                                    <Checkbox id="vaccination_current" checked={formData.vaccination_current}
                                              onCheckedChange={c => setFormData(p => ( {
                                                  ...p,
                                                  vaccination_current: c
                                              } ))}/>
                                    <label htmlFor="vaccination_current" className="text-sm font-medium">
                                        Pet is up to date with vaccinations
                                    </label>
                                </div>
                                <div className="flex items-center space-x-2">
                                    <Checkbox id="house_trained" checked={formData.house_trained}
                                              onCheckedChange={c => setFormData(p => ( {...p, house_trained: c} ))}/>
                                    <label htmlFor="house_trained" className="text-sm font-medium">
                                        Pet is house-trained and well-behaved
                                    </label>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="reason">Additional Information (Optional)</Label>
                                <Textarea id="reason" name="reason" value={formData.reason}
                                          onChange={handleChange} rows={3}
                                          placeholder="Any additional information about your pet..."/>
                            </div>

                            <div className="flex gap-2 pt-2">
                                <Button type="submit" disabled={loading}>
                                    {loading ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/>Submitting...</> : 'Submit Pet Request'}
                                </Button>
                                <Button type="button" variant="outline"
                                        onClick={() => setShowForm(false)}>Cancel</Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
            )}

            <Separator/>

            {/* Request List */}
            <div className="space-y-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <h4 className="font-semibold">
                        {canManage ? `All Requests (${filteredRecords.length})` : 'Your Pet Requests'}
                    </h4>
                    <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
                </div>

                {filteredRecords.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            <Dog className="h-12 w-12 mx-auto mb-2 opacity-50"/>
                            <p>{statusFilter === 'all' ? 'No pet requests found.' : 'No pet requests found for this status.'}</p>
                        </CardContent>
                    </Card>
                ) : (
                    filteredRecords.map(req => {
                        const cfg = STATUS_CONFIG[ req.status ] || STATUS_CONFIG.pending;
                        const StatusIcon = cfg.icon;
                        return (
                            <Card key={req.id} className="border-l-4" style={{
                                borderLeftColor:
                                    req.status === 'approved' ? '#22c55e' :
                                        req.status === 'rejected' ? '#ef4444' :
                                            req.status === 'conditional' ? '#3b82f6' :
                                                req.status === 'under_review' ? '#a855f7' : '#facc15'
                            }}>
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between mb-3">
                                        <div>
                                            <h5 className="font-semibold">
                                                {req.pet_name} ({req.pet_type})
                                                {canManage && req.submitted_by_name && (
                                                    <span className="text-sm font-normal text-muted-foreground ml-2">
                                                        — {req.submitted_by_name} {req.unit_number ? `(Unit ${req.unit_number})` : ''}
                                                    </span>
                                                )}
                                            </h5>
                                            <p className="text-sm text-muted-foreground">
                                                {req.breed} &bull; {req.size} &bull; {req.age} year{req.age !== 1 ? 's' : ''} old
                                            </p>
                                        </div>
                                        <Badge className={cfg.color}>
                                            <StatusIcon className="h-3 w-3 mr-1"/>
                                            {cfg.label}
                                        </Badge>
                                    </div>

                                    <div className="grid grid-cols-2 gap-2 text-sm mb-3">
                                        <div>
                                            <span className="text-muted-foreground">Registration:</span>
                                            <p className="font-medium">{req.council_registration || 'Not provided'}</p>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground">Submitted:</span>
                                            <p className="font-medium">{formatDate(req.created_at)}</p>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground">Vaccinated:</span>
                                            <p className="font-medium">{req.vaccination_current ? 'Yes' : 'No'}</p>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground">House-trained:</span>
                                            <p className="font-medium">{req.house_trained ? 'Yes' : 'No'}</p>
                                        </div>
                                    </div>

                                    {req.conditions && (
                                        <div
                                            className="mt-3 p-3 bg-blue-50 dark:bg-blue-950 rounded border border-blue-200">
                                            <p className="text-sm font-semibold text-blue-900 dark:text-blue-100">Approval
                                                Conditions:</p>
                                            <p className="text-sm text-blue-800 dark:text-blue-200 mt-1">{req.conditions}</p>
                                        </div>
                                    )}

                                    {req.reason && (
                                        <div className="mt-3 text-sm">
                                            <span className="text-muted-foreground">Additional info: </span>
                                            <span>{req.reason}</span>
                                        </div>
                                    )}

                                    {req.reviewed_at && (
                                        <div className="mt-3 text-xs text-muted-foreground">
                                            Reviewed by {req.reviewed_by_name} on {formatDate(req.reviewed_at)}
                                        </div>
                                    )}

                                    {/* Manager action buttons */}
                                    {canManage && req.status !== 'approved' && req.status !== 'rejected' && (
                                        <div className="border-t mt-4 pt-3 flex items-center gap-2 flex-wrap">
                                            {req.status === 'pending' && (
                                                <Button
                                                    data-testid={`btn-review-${req.id}`}
                                                    size="sm"
                                                    variant="outline"
                                                    className="rounded-full active:scale-95"
                                                    onClick={() => handleMarkUnderReview(req.id)}
                                                >
                                                    Mark Under Review
                                                </Button>
                                            )}
                                            <Button
                                                data-testid={`btn-approve-${req.id}`}
                                                size="sm"
                                                className="rounded-full active:scale-95 bg-green-600 hover:bg-green-700 text-white"
                                                onClick={() => openAction(req, 'approve')}
                                            >
                                                <CheckCircle2 className="mr-1 h-3 w-3"/> Approve
                                            </Button>
                                            <Button
                                                data-testid={`btn-conditional-${req.id}`}
                                                size="sm"
                                                className="rounded-full active:scale-95 bg-blue-600 hover:bg-blue-700 text-white"
                                                onClick={() => openAction(req, 'conditional')}
                                            >
                                                Conditional
                                            </Button>
                                            <Button
                                                data-testid={`btn-decline-${req.id}`}
                                                size="sm"
                                                variant="destructive"
                                                className="rounded-full active:scale-95"
                                                onClick={() => openAction(req, 'decline')}
                                            >
                                                <XCircle className="mr-1 h-3 w-3"/> Decline
                                            </Button>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })
                )}
            </div>

            {/* Manager Action Modal (inline) */}
            {actionTarget && (
                <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
                     onClick={closeAction}>
                    <Card className="w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
                        <CardContent className="pt-6 space-y-4">
                            <h3 className="font-semibold text-lg capitalize">
                                {actionTarget.action === 'approve' ? 'Approve' :
                                    actionTarget.action === 'conditional' ? 'Conditional Approval' :
                                        'Decline'} — {actionTarget.name}
                            </h3>

                            {actionTarget.action === 'conditional' && (
                                <div className="space-y-2">
                                    <Label>Conditions (required) *</Label>
                                    <Textarea
                                        placeholder="e.g. Pet must be kept on leash in all common areas and owner accepts full liability for any damage..."
                                        value={actionConds}
                                        onChange={e => setActionConds(e.target.value)}
                                        rows={3}
                                    />
                                </div>
                            )}

                            <div className="space-y-2">
                                <Label>Notes (optional)</Label>
                                <Textarea
                                    placeholder={
                                        actionTarget.action === 'decline'
                                            ? 'Reason for declining (will be shared with owner)...'
                                            : 'Any additional notes...'
                                    }
                                    value={actionNotes}
                                    onChange={e => setActionNotes(e.target.value)}
                                    rows={2}
                                />
                            </div>

                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={closeAction} disabled={actionLoading}>
                                    Cancel
                                </Button>
                                <Button
                                    data-testid="btn-confirm-action"
                                    disabled={actionLoading}
                                    className={
                                        actionTarget.action === 'approve' ? 'bg-green-600 hover:bg-green-700 text-white' :
                                            actionTarget.action === 'conditional' ? 'bg-blue-600 hover:bg-blue-700 text-white' :
                                                'bg-red-600 hover:bg-red-700 text-white'
                                    }
                                    onClick={submitAction}
                                >
                                    {actionLoading
                                        ? <><Loader2 className="mr-2 h-4 w-4 animate-spin"/>Processing...</>
                                        : `Confirm ${actionTarget.action === 'approve' ? 'Approval' : actionTarget.action === 'conditional' ? 'Conditional' : 'Decline'}`
                                    }
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
};

export default PetRequestTab;
