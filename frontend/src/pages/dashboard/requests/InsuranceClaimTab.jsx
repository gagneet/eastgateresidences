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
import {
    CheckCircle,
    ChevronLeft,
    ChevronRight,
    Clock,
    FileText,
    Loader2,
    Shield,
    Upload,
    X,
    XCircle
} from 'lucide-react';
import { toast } from 'sonner';
import { formatDate } from '../../../lib/utils';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';
import {trackRequestCatalogueEvent} from '@/config/requestCatalogue';

const INCIDENT_TYPES = [
    {value: 'water_damage', label: 'Water Damage', icon: '💧'},
    {value: 'fire_smoke', label: 'Fire / Smoke', icon: '🔥'},
    {value: 'theft_vandalism', label: 'Theft / Vandalism', icon: '🔓'},
    {value: 'storm', label: 'Storm Damage', icon: '⛈️'},
    {value: 'other', label: 'Other', icon: '📋'},
];

const statusConfig = {
    submitted: {label: 'Submitted', color: 'bg-blue-100 text-blue-800', icon: Clock},
    under_review: {label: 'Under Review', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800', icon: CheckCircle},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800', icon: XCircle},
};
/**
 * @generated FunctionHeader
 * Function: InsuranceClaimTab
 * Path: frontend/src/pages/dashboard/requests/InsuranceClaimTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InsuranceClaimTab = () => {
    const {api, user} = useAuth();
    const [step, setStep] = useState(1);
    const [loading, setLoading] = useState(false);
    const [claims, setClaims] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: claims,
        statusConfig,
        tab: 'insurance-claim',
    });

    const [formData, setFormData] = useState({
        claim_type: '',
        incident_date: '',
        description: '',
        estimated_cost: '',
        attachments: [],
    });

    const fetchClaims = useCallback(async () => {
        try {
            const response = await api.get('/requests/insurance-claims');
            setClaims(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch insurance claims:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchClaims();
    }, [fetchClaims]);
    /**
     * @generated FunctionHeader
     * Function: handleFileUpload
     * Path: frontend/src/pages/dashboard/requests/InsuranceClaimTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleFileUpload = (e) => {
        const files = Array.from(e.target.files);
        if (formData.attachments.length + files.length > 3) {
            toast.error('Maximum 3 photos allowed');
            return;
        }
        files.forEach(file => {
            if (file.size > 2 * 1024 * 1024) {
                toast.error(`${file.name} exceeds 2MB limit`);
                return;
            }
            const reader = new FileReader();
            reader.onloadend = () => {
                setFormData(prev => ( {...prev, attachments: [...prev.attachments, reader.result]} ));
            };
            reader.readAsDataURL(file);
        });
    };
    /**
     * @generated FunctionHeader
     * Function: removeAttachment
     * Path: frontend/src/pages/dashboard/requests/InsuranceClaimTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeAttachment = (idx) => {
        setFormData(prev => ( {...prev, attachments: prev.attachments.filter((_, i) => i !== idx)} ));
    };
    /**
     * @generated FunctionHeader
     * Function: resetForm
     * Path: frontend/src/pages/dashboard/requests/InsuranceClaimTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const resetForm = () => {
        setFormData({claim_type: '', incident_date: '', description: '', estimated_cost: '', attachments: []});
        setStep(1);
        setShowForm(false);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/requests/InsuranceClaimTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async () => {
        if (!formData.claim_type || !formData.incident_date || !formData.description || !formData.estimated_cost) {
            toast.error('Please complete all required fields');
            return;
        }
        setLoading(true);
        try {
            await api.post('/requests/insurance-claims', {
                claim_type: formData.claim_type,
                incident_date: formData.incident_date,
                description: formData.description,
                estimated_cost: parseFloat(formData.estimated_cost),
                attachments: formData.attachments,
            });
            trackRequestCatalogueEvent('request_form_submitted', {
                definition_key: 'insurance-claim',
                definition_version: 1,
                role: user?.effective_role || user?.role,
                status: 'submitted',
            });
            toast.success('Insurance claim submitted successfully');
            resetForm();
            fetchClaims();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to submit claim');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <Alert>
                <Shield className="h-4 w-4"/>
                <AlertDescription>
                    <strong>Insurance Claims:</strong> Report damage or incidents that may be covered under the strata's
                    insurance policy.
                    For emergencies, contact the strata manager immediately.
                </AlertDescription>
            </Alert>

            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Insurance Claims</h3>
                    <p className="text-sm text-muted-foreground">Report damage for insurance coverage</p>
                </div>
                <Button onClick={() => {
                    setShowForm(!showForm);
                    setStep(1);
                }}>
                    <FileText className="mr-2 h-4 w-4"/>
                    {showForm ? 'Cancel' : 'File a Claim'}
                </Button>
            </div>

            {showForm && (
                <Card className="border-primary/20">
                    <CardContent className="pt-6">
                        {/* Step indicator */}
                        <div className="flex items-center gap-2 mb-6">
                            {[1, 2, 3].map(s => (
                                <React.Fragment key={s}>
                                    <div
                                        className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold transition-colors ${step >= s ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}>
                                        {s}
                                    </div>
                                    {s < 3 &&
                                        <div className={`flex-1 h-1 rounded ${step > s ? 'bg-primary' : 'bg-muted'}`}/>}
                                </React.Fragment>
                            ))}
                        </div>
                        <p className="text-xs text-muted-foreground mb-6">
                            {step === 1 ? 'Step 1: Incident Details' : step === 2 ? 'Step 2: Damage & Evidence' : 'Step 3: Review & Submit'}
                        </p>

                        {/* Step 1 */}
                        {step === 1 && (
                            <div className="space-y-5">
                                <div className="space-y-2">
                                    <Label>Incident Type *</Label>
                                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                                        {INCIDENT_TYPES.map(type => (
                                            <button
                                                key={type.value}
                                                type="button"
                                                onClick={() => setFormData(prev => ( {
                                                    ...prev,
                                                    claim_type: type.value
                                                } ))}
                                                className={`p-3 rounded-lg border-2 text-left transition-all ${formData.claim_type === type.value ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50'}`}
                                            >
                                                <div className="text-2xl mb-1">{type.icon}</div>
                                                <div className="text-sm font-medium">{type.label}</div>
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="incident_date">Date of Incident *</Label>
                                    <Input
                                        id="incident_date"
                                        type="date"
                                        value={formData.incident_date}
                                        onChange={e => setFormData(prev => ( {
                                            ...prev,
                                            incident_date: e.target.value
                                        } ))}
                                        max={new Date().toISOString().split('T')[ 0 ]}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="description">Description of Incident *</Label>
                                    <Textarea
                                        id="description"
                                        value={formData.description}
                                        onChange={e => setFormData(prev => ( {...prev, description: e.target.value} ))}
                                        placeholder="Describe what happened in detail..."
                                        rows={4}
                                    />
                                </div>
                                <Button
                                    type="button"
                                    onClick={() => {
                                        if (!formData.claim_type || !formData.incident_date || !formData.description) {
                                            toast.error('Please complete all fields');
                                            return;
                                        }
                                        setStep(2);
                                    }}
                                    className="w-full"
                                >
                                    Next: Damage Details <ChevronRight className="ml-2 h-4 w-4"/>
                                </Button>
                            </div>
                        )}

                        {/* Step 2 */}
                        {step === 2 && (
                            <div className="space-y-5">
                                <div className="space-y-2">
                                    <Label htmlFor="estimated_cost">Estimated Damage Value (AUD) *</Label>
                                    <div className="relative">
                                        <span
                                            className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">$</span>
                                        <Input
                                            id="estimated_cost"
                                            type="number"
                                            min="0"
                                            step="0.01"
                                            value={formData.estimated_cost}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                estimated_cost: e.target.value
                                            } ))}
                                            className="pl-7"
                                            placeholder="0.00"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label>Photos / Evidence (up to 3, max 2MB each)</Label>
                                    <div className="border-2 border-dashed border-border rounded-lg p-4 text-center">
                                        <input
                                            type="file"
                                            accept="image/*"
                                            multiple
                                            onChange={handleFileUpload}
                                            className="hidden"
                                            id="claim-photos"
                                        />
                                        <label htmlFor="claim-photos" className="cursor-pointer">
                                            <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground"/>
                                            <p className="text-sm text-muted-foreground">Click to upload photos</p>
                                        </label>
                                    </div>
                                    {formData.attachments.length > 0 && (
                                        <div className="flex gap-2 flex-wrap mt-2">
                                            {formData.attachments.map((att, idx) => (
                                                <div key={idx} className="relative">
                                                    <img src={att} alt={`attachment ${idx + 1}`}
                                                         className="h-16 w-16 object-cover rounded-lg border"/>
                                                    <button
                                                        type="button"
                                                        onClick={() => removeAttachment(idx)}
                                                        className="absolute -top-1 -right-1 bg-destructive text-destructive-foreground rounded-full p-0.5"
                                                    >
                                                        <X className="h-3 w-3"/>
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                                <div className="flex gap-2">
                                    <Button type="button" variant="outline" onClick={() => setStep(1)}
                                            className="flex-1">
                                        <ChevronLeft className="mr-2 h-4 w-4"/> Back
                                    </Button>
                                    <Button type="button" onClick={() => {
                                        if (!formData.estimated_cost) {
                                            toast.error('Please enter estimated cost');
                                            return;
                                        }
                                        setStep(3);
                                    }} className="flex-1">
                                        Next: Review <ChevronRight className="ml-2 h-4 w-4"/>
                                    </Button>
                                </div>
                            </div>
                        )}

                        {/* Step 3 */}
                        {step === 3 && (
                            <div className="space-y-5">
                                <div className="space-y-3 p-4 bg-muted/30 rounded-lg text-sm">
                                    <h4 className="font-semibold">Review Your Claim</h4>
                                    <div className="grid grid-cols-2 gap-3">
                                        <div>
                                            <p className="text-muted-foreground">Incident Type</p>
                                            <p className="font-medium">{INCIDENT_TYPES.find(t => t.value === formData.claim_type)?.label}</p>
                                        </div>
                                        <div>
                                            <p className="text-muted-foreground">Date</p>
                                            <p className="font-medium">{formData.incident_date}</p>
                                        </div>
                                        <div>
                                            <p className="text-muted-foreground">Estimated Cost</p>
                                            <p className="font-medium">${parseFloat(formData.estimated_cost || 0).toFixed(2)}</p>
                                        </div>
                                        <div>
                                            <p className="text-muted-foreground">Photos</p>
                                            <p className="font-medium">{formData.attachments.length} attached</p>
                                        </div>
                                    </div>
                                    <div>
                                        <p className="text-muted-foreground">Description</p>
                                        <p className="font-medium">{formData.description}</p>
                                    </div>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    By submitting, you confirm the information above is accurate and agree to cooperate
                                    with any investigation.
                                </p>
                                <div className="flex gap-2">
                                    <Button type="button" variant="outline" onClick={() => setStep(2)}
                                            className="flex-1">
                                        <ChevronLeft className="mr-2 h-4 w-4"/> Back
                                    </Button>
                                    <Button type="button" onClick={handleSubmit} disabled={loading} className="flex-1">
                                        {loading ? <><Loader2
                                            className="mr-2 h-4 w-4 animate-spin"/> Submitting...</> : 'Submit Claim'}
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            <Separator/>

            {/* Submitted claims */}
            <div className="space-y-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <h4 className="font-semibold">Your Claims</h4>
                    <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
                </div>
                {filteredRecords.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            <Shield className="h-12 w-12 mx-auto mb-2 opacity-50"/>
                            <p>{statusFilter === 'all' ? 'No insurance claims submitted yet' : 'No insurance claims found for this status'}</p>
                        </CardContent>
                    </Card>
                ) : (
                    filteredRecords.map(claim => {
                        const StatusIcon = statusConfig[ claim.status ]?.icon || Clock;
                        return (
                            <Card key={claim.id} className="border-l-4 border-l-blue-400">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between mb-2">
                                        <div>
                                            <h5 className="font-semibold">{INCIDENT_TYPES.find(t => t.value === claim.claim_type)?.label || claim.claim_type}</h5>
                                            <p className="text-xs text-muted-foreground">Claim
                                                #{claim.claim_number} · {formatDate(claim.created_at)}</p>
                                        </div>
                                        <Badge
                                            className={statusConfig[ claim.status ]?.color || 'bg-gray-100 text-gray-800'}>
                                            <StatusIcon className="h-3 w-3 mr-1"/>
                                            {statusConfig[ claim.status ]?.label || claim.status}
                                        </Badge>
                                    </div>
                                    <div className="grid grid-cols-2 gap-2 text-sm">
                                        <div><span className="text-muted-foreground">Incident date: </span><span
                                            className="font-medium">{claim.incident_date}</span></div>
                                        <div><span className="text-muted-foreground">Estimated: </span><span
                                            className="font-medium">${claim.estimated_cost?.toFixed(2)}</span></div>
                                    </div>
                                    {claim.description &&
                                        <p className="text-sm mt-2 text-muted-foreground line-clamp-2">{claim.description}</p>}
                                </CardContent>
                            </Card>
                        );
                    })
                )}
            </div>
        </div>
    );
};

export default InsuranceClaimTab;
