import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Textarea } from '../../../components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Card, CardContent } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Separator } from '../../../components/ui/separator';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { CheckCircle, Clock, HelpCircle, Loader2, MessageSquare } from 'lucide-react';
import { toast } from 'sonner';
import { formatDate } from '../../../lib/utils';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';
import {trackRequestCatalogueEvent} from '@/config/requestCatalogue';

const ENQUIRY_CATEGORIES = [
    {value: 'coverage_query', label: 'Coverage Query'},
    {value: 'claim_procedure', label: 'Claim Procedure'},
    {value: 'certificate_of_currency', label: 'Certificate of Currency'},
    {value: 'premium', label: 'Premium Information'},
    {value: 'general', label: 'General Question'},
];

const statusConfig = {
    pending: {label: 'Pending Answer', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    answered: {label: 'Answered', color: 'bg-green-100 text-green-800', icon: CheckCircle},
};
/**
 * @generated FunctionHeader
 * Function: InsuranceEnquiryTab
 * Path: frontend/src/pages/dashboard/requests/InsuranceEnquiryTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InsuranceEnquiryTab = () => {
    const {api, user} = useAuth();
    const [loading, setLoading] = useState(false);
    const [enquiries, setEnquiries] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: enquiries,
        statusConfig,
        tab: 'insurance-enquiry',
    });

    const [formData, setFormData] = useState({
        subject: '',
        category: '',
        question: '',
    });

    const fetchEnquiries = useCallback(async () => {
        try {
            const response = await api.get('/requests/insurance-enquiries');
            setEnquiries(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch insurance enquiries:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchEnquiries();
    }, [fetchEnquiries]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/requests/InsuranceEnquiryTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.subject || !formData.question) {
            toast.error('Please fill in all required fields');
            return;
        }
        setLoading(true);
        try {
            await api.post('/requests/insurance-enquiries', {
                subject: formData.subject,
                question: formData.question,
            });
            trackRequestCatalogueEvent('request_form_submitted', {
                definition_key: 'insurance-enquiry',
                definition_version: 1,
                role: user?.effective_role || user?.role,
                status: 'submitted',
            });
            toast.success('Enquiry submitted successfully');
            setFormData({subject: '', category: '', question: ''});
            setShowForm(false);
            fetchEnquiries();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to submit enquiry');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <Alert>
                <HelpCircle className="h-4 w-4"/>
                <AlertDescription>
                    <strong>Insurance Questions:</strong> Ask questions about your coverage, claim procedures, or
                    request a certificate of currency.
                    The building team will respond according to the scheme's service process.
                </AlertDescription>
            </Alert>

            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Insurance Enquiries</h3>
                    <p className="text-sm text-muted-foreground">Ask questions about the strata insurance policy</p>
                </div>
                <Button onClick={() => setShowForm(!showForm)}>
                    <MessageSquare className="mr-2 h-4 w-4"/>
                    {showForm ? 'Cancel' : 'Ask a Question'}
                </Button>
            </div>

            {showForm && (
                <Card className="border-primary/20">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="category">Category</Label>
                                <Select value={formData.category}
                                        onValueChange={value => setFormData(prev => ( {...prev, category: value} ))}>
                                    <SelectTrigger>
                                        <SelectValue placeholder="Select a category (optional)"/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {ENQUIRY_CATEGORIES.map(cat => (
                                            <SelectItem key={cat.value} value={cat.value}>{cat.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="subject">Subject *</Label>
                                <Input
                                    id="subject"
                                    value={formData.subject}
                                    onChange={e => setFormData(prev => ( {...prev, subject: e.target.value} ))}
                                    placeholder="Brief subject of your enquiry"
                                    required
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="question">Your Question *</Label>
                                <Textarea
                                    id="question"
                                    value={formData.question}
                                    onChange={e => setFormData(prev => ( {...prev, question: e.target.value} ))}
                                    placeholder="Please provide as much detail as possible..."
                                    rows={4}
                                    required
                                />
                            </div>

                            <div className="flex gap-2 pt-2">
                                <Button type="submit" disabled={loading}>
                                    {loading ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/> Submitting...</> : 'Submit Enquiry'}
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
                    <h4 className="font-semibold">Your Enquiries</h4>
                    <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
                </div>
                {filteredRecords.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            <HelpCircle className="h-12 w-12 mx-auto mb-2 opacity-50"/>
                            <p>{statusFilter === 'all' ? 'No insurance enquiries submitted yet' : 'No insurance enquiries found for this status'}</p>
                        </CardContent>
                    </Card>
                ) : (
                    filteredRecords.map(enquiry => {
                        const StatusIcon = statusConfig[ enquiry.status ]?.icon || Clock;
                        return (
                            <Card key={enquiry.id} className="border-l-4 border-l-yellow-400">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between mb-2">
                                        <div>
                                            <h5 className="font-semibold">{enquiry.subject}</h5>
                                            <p className="text-xs text-muted-foreground">{formatDate(enquiry.created_at)}</p>
                                        </div>
                                        <Badge
                                            className={statusConfig[ enquiry.status ]?.color || 'bg-gray-100 text-gray-800'}>
                                            <StatusIcon className="h-3 w-3 mr-1"/>
                                            {statusConfig[ enquiry.status ]?.label || enquiry.status}
                                        </Badge>
                                    </div>
                                    <p className="text-sm text-muted-foreground mb-2">{enquiry.question}</p>
                                    {enquiry.answer && (
                                        <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                                            <p className="text-xs font-semibold text-green-700 mb-1">Answer
                                                from {enquiry.answered_by_name}:</p>
                                            <p className="text-sm text-green-800">{enquiry.answer}</p>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })
                )}
            </div>
        </div>
    );
};

export default InsuranceEnquiryTab;
