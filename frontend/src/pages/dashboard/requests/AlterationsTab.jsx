import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Textarea } from '../../../components/ui/textarea';
import { Card, CardContent } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Separator } from '../../../components/ui/separator';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { Checkbox } from '../../../components/ui/checkbox';
import { CheckCircle, ChevronDown, ChevronUp, Clock, Hammer, Loader2, Upload, X, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { formatDate } from '../../../lib/utils';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';
import {trackRequestCatalogueEvent} from '@/config/requestCatalogue';

const ALTERATION_TYPES = [
    {value: 'flooring', label: 'Flooring', icon: '🏠'},
    {value: 'plumbing', label: 'Plumbing', icon: '🔧'},
    {value: 'electrical', label: 'Electrical', icon: '⚡'},
    {value: 'structural', label: 'Structural', icon: '🏗️'},
    {value: 'painting', label: 'Painting', icon: '🎨'},
    {value: 'garden', label: 'Garden / Balcony', icon: '🌿'},
    {value: 'other', label: 'Other', icon: '🔨'},
];

const BY_LAW_TEXT = `By-Law 7 — Alterations and Additions
No owner or occupier shall make any alterations or additions to any part of the common property or the exterior of their lot without first obtaining the written approval of the Owners Corporation. Works must be carried out by licensed contractors, must not affect the structural integrity of the building, and must comply with all applicable laws and regulations. The owner is responsible for restoring the lot to its original condition upon sale or termination of ownership unless otherwise agreed.`;

const statusConfig = {
    pending: {label: 'Pending Review', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800', icon: CheckCircle},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800', icon: XCircle},
    completed: {label: 'Completed', color: 'bg-blue-100 text-blue-800', icon: CheckCircle},
};
/**
 * @generated FunctionHeader
 * Function: AlterationsTab
 * Path: frontend/src/pages/dashboard/requests/AlterationsTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AlterationsTab = () => {
    const {api, user} = useAuth();
    const [loading, setLoading] = useState(false);
    const [alterations, setAlterations] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const [byLawExpanded, setByLawExpanded] = useState(false);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: alterations,
        statusConfig,
        tab: 'alterations',
    });

    const [formData, setFormData] = useState({
        alteration_type: '',
        description: '',
        contractor_details: '',
        proposed_start_date: '',
        estimated_duration: '',
        plans_attachments: [],
        by_law_acknowledged: false,
        signature: '',
    });

    const fetchAlterations = useCallback(async () => {
        try {
            const response = await api.get('/requests/alterations');
            setAlterations(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch alteration requests:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchAlterations();
    }, [fetchAlterations]);
    /**
     * @generated FunctionHeader
     * Function: handleFileUpload
     * Path: frontend/src/pages/dashboard/requests/AlterationsTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleFileUpload = (e) => {
        const files = Array.from(e.target.files);
        if (formData.plans_attachments.length + files.length > 3) {
            toast.error('Maximum 3 files allowed');
            return;
        }
        files.forEach(file => {
            if (file.size > 5 * 1024 * 1024) {
                toast.error(`${file.name} exceeds 5MB limit`);
                return;
            }
            const reader = new FileReader();
            reader.onloadend = () => {
                setFormData(prev => ( {...prev, plans_attachments: [...prev.plans_attachments, reader.result]} ));
            };
            reader.readAsDataURL(file);
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/requests/AlterationsTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.alteration_type || !formData.description || !formData.proposed_start_date || !formData.estimated_duration) {
            toast.error('Please fill in all required fields');
            return;
        }
        if (!formData.by_law_acknowledged) {
            toast.error('You must acknowledge the by-law before submitting');
            return;
        }
        if (!formData.signature.trim()) {
            toast.error('Please provide your electronic signature');
            return;
        }

        setLoading(true);
        try {
            await api.post('/requests/alterations', {
                alteration_type: formData.alteration_type,
                description: formData.description,
                contractor_details: formData.contractor_details || null,
                proposed_start_date: formData.proposed_start_date,
                estimated_duration: formData.estimated_duration,
                plans_attachments: formData.plans_attachments,
            });
            trackRequestCatalogueEvent('request_form_submitted', {
                definition_key: 'alterations',
                definition_version: 1,
                role: user?.effective_role || user?.role,
                status: 'submitted',
            });
            toast.success('Alteration request submitted successfully');
            setFormData({
                alteration_type: '', description: '', contractor_details: '', proposed_start_date: '',
                estimated_duration: '', plans_attachments: [], by_law_acknowledged: false, signature: '',
            });
            setShowForm(false);
            fetchAlterations();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to submit alteration request');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <Alert>
                <Hammer className="h-4 w-4"/>
                <AlertDescription>
                    <strong>Alterations:</strong> EC approval is required for all renovations and modifications to your
                    unit.
                    Submit plans and contractor details for review. Processing may take up to 30 days.
                </AlertDescription>
            </Alert>

            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Alteration Requests</h3>
                    <p className="text-sm text-muted-foreground">Request EC approval for renovations and
                        modifications</p>
                </div>
                <Button onClick={() => setShowForm(!showForm)}>
                    <Hammer className="mr-2 h-4 w-4"/>
                    {showForm ? 'Cancel' : 'Request Approval'}
                </Button>
            </div>

            {showForm && (
                <Card className="border-primary/20">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-6">
                            {/* Section 1: Alteration Type */}
                            <div className="space-y-3">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">1.
                                    Alteration Type</h4>
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                    {ALTERATION_TYPES.map(type => (
                                        <button
                                            key={type.value}
                                            type="button"
                                            onClick={() => setFormData(prev => ( {
                                                ...prev,
                                                alteration_type: type.value
                                            } ))}
                                            className={`p-3 rounded-lg border-2 text-left transition-all ${formData.alteration_type === type.value ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50'}`}
                                        >
                                            <div className="text-xl mb-1">{type.icon}</div>
                                            <div className="text-xs font-medium">{type.label}</div>
                                        </button>
                                    ))}
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="description">Description of Works *</Label>
                                    <Textarea
                                        id="description"
                                        value={formData.description}
                                        onChange={e => setFormData(prev => ( {...prev, description: e.target.value} ))}
                                        placeholder="Describe the proposed alterations in detail, including materials, scope, and any impact on common property..."
                                        rows={4}
                                        required
                                    />
                                </div>
                            </div>

                            {/* Section 2: Contractor Details */}
                            <div className="space-y-3">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">2.
                                    Contractor Details</h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="contractor_details">Contractor Name & Contact</Label>
                                        <Textarea
                                            id="contractor_details"
                                            value={formData.contractor_details}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                contractor_details: e.target.value
                                            } ))}
                                            placeholder="Name, ABN, phone, licence number..."
                                            rows={3}
                                        />
                                    </div>
                                    <div className="space-y-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="proposed_start_date">Proposed Start Date *</Label>
                                            <Input
                                                id="proposed_start_date"
                                                type="date"
                                                value={formData.proposed_start_date}
                                                onChange={e => setFormData(prev => ( {
                                                    ...prev,
                                                    proposed_start_date: e.target.value
                                                } ))}
                                                min={new Date().toISOString().split('T')[ 0 ]}
                                                required
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="estimated_duration">Estimated Duration *</Label>
                                            <Input
                                                id="estimated_duration"
                                                value={formData.estimated_duration}
                                                onChange={e => setFormData(prev => ( {
                                                    ...prev,
                                                    estimated_duration: e.target.value
                                                } ))}
                                                placeholder="e.g. 2 weeks, 3 days"
                                                required
                                            />
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Section 3: Plans & Insurance */}
                            <div className="space-y-3">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">3.
                                    Plans & Documents</h4>
                                <div className="border-2 border-dashed border-border rounded-lg p-4 text-center">
                                    <input
                                        type="file"
                                        accept="image/*,.pdf"
                                        multiple
                                        onChange={handleFileUpload}
                                        className="hidden"
                                        id="alteration-plans"
                                    />
                                    <label htmlFor="alteration-plans" className="cursor-pointer">
                                        <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground"/>
                                        <p className="text-sm text-muted-foreground">Upload plans, drawings, or
                                            insurance certificate (PDF/image, max 5MB each)</p>
                                    </label>
                                </div>
                                {formData.plans_attachments.length > 0 && (
                                    <div className="flex gap-2 flex-wrap">
                                        {formData.plans_attachments.map((att, idx) => (
                                            <div key={idx}
                                                 className="flex items-center gap-2 p-2 bg-muted rounded-lg text-sm">
                                                <span>Document {idx + 1}</span>
                                                <button type="button" onClick={() => setFormData(prev => ( {
                                                    ...prev,
                                                    plans_attachments: prev.plans_attachments.filter((_, i) => i !== idx)
                                                } ))}>
                                                    <X className="h-3 w-3"/>
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Section 4: By-law Acknowledgment */}
                            <div className="space-y-3">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">4.
                                    By-Law Acknowledgment</h4>
                                <div className="border rounded-lg overflow-hidden">
                                    <button
                                        type="button"
                                        onClick={() => setByLawExpanded(!byLawExpanded)}
                                        className="w-full flex items-center justify-between p-4 text-sm font-medium bg-muted/30 hover:bg-muted/50 transition-colors"
                                    >
                                        <span>By-Law 7 — Alterations and Additions</span>
                                        {byLawExpanded ? <ChevronUp className="h-4 w-4"/> :
                                            <ChevronDown className="h-4 w-4"/>}
                                    </button>
                                    {byLawExpanded && (
                                        <div className="p-4 text-sm text-muted-foreground border-t bg-muted/10">
                                            {BY_LAW_TEXT}
                                        </div>
                                    )}
                                </div>
                                <div className="flex items-start space-x-2">
                                    <Checkbox
                                        id="by_law_acknowledged"
                                        checked={formData.by_law_acknowledged}
                                        onCheckedChange={checked => setFormData(prev => ( {
                                            ...prev,
                                            by_law_acknowledged: checked
                                        } ))}
                                    />
                                    <label htmlFor="by_law_acknowledged" className="text-sm">
                                        I have read and understand By-Law 7 regarding alterations and additions *
                                    </label>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="signature">Electronic Signature (type your full name) *</Label>
                                    <Input
                                        id="signature"
                                        value={formData.signature}
                                        onChange={e => setFormData(prev => ( {...prev, signature: e.target.value} ))}
                                        placeholder="Type your full name as a signature"
                                        required
                                    />
                                </div>
                            </div>

                            <div className="flex gap-2 pt-2">
                                <Button type="submit" disabled={loading}>
                                    {loading ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/> Submitting...</> : 'Submit Alteration Request'}
                                </Button>
                                <Button type="button" variant="outline"
                                        onClick={() => setShowForm(false)}>Cancel</Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
            )}

            <Separator/>

            <div className="space-y-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <h4 className="font-semibold">Your Alteration Requests</h4>
                    <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
                </div>
                {filteredRecords.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            <Hammer className="h-12 w-12 mx-auto mb-2 opacity-50"/>
                            <p>{statusFilter === 'all' ? 'No alteration requests submitted yet' : 'No alteration requests found for this status'}</p>
                        </CardContent>
                    </Card>
                ) : (
                    filteredRecords.map(req => {
                        const StatusIcon = statusConfig[ req.status ]?.icon || Clock;
                        const type = ALTERATION_TYPES.find(t => t.value === req.alteration_type);
                        return (
                            <Card key={req.id} className="border-l-4 border-l-orange-400">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between mb-2">
                                        <div>
                                            <h5 className="font-semibold">{type?.label || req.alteration_type}</h5>
                                            <p className="text-xs text-muted-foreground">Start: {req.proposed_start_date} ·
                                                Duration: {req.estimated_duration}</p>
                                        </div>
                                        <Badge
                                            className={statusConfig[ req.status ]?.color || 'bg-gray-100 text-gray-800'}>
                                            <StatusIcon className="h-3 w-3 mr-1"/>
                                            {statusConfig[ req.status ]?.label || req.status}
                                        </Badge>
                                    </div>
                                    <p className="text-sm text-muted-foreground line-clamp-2">{req.description}</p>
                                    {req.conditions && (
                                        <div className="mt-3 p-3 bg-blue-50 rounded border border-blue-200">
                                            <p className="text-xs font-semibold text-blue-700">Approval Conditions:</p>
                                            <p className="text-sm text-blue-800 mt-1">{req.conditions}</p>
                                        </div>
                                    )}
                                    <p className="text-xs text-muted-foreground mt-2">Submitted {formatDate(req.created_at)}</p>
                                </CardContent>
                            </Card>
                        );
                    })
                )}
            </div>
        </div>
    );
};

export default AlterationsTab;
