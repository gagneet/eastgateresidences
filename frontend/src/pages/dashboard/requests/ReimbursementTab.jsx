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
import { CheckCircle, Clock, DollarSign, Loader2, Upload, X, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { formatCurrency, formatDate } from '../../../lib/utils';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';
import {trackRequestCatalogueEvent} from '@/config/requestCatalogue';

const EXPENSE_CATEGORIES = [
    {value: 'common_area_repair', label: 'Common Area Repair'},
    {value: 'emergency_expense', label: 'Emergency Expense'},
    {value: 'common_area_supplies', label: 'Common Area Supplies'},
    {value: 'meeting_expense', label: 'Meeting Expense'},
    {value: 'other', label: 'Other'},
];

const statusConfig = {
    pending: {label: 'Pending Approval', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800', icon: CheckCircle},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800', icon: XCircle},
    paid: {label: 'Paid', color: 'bg-blue-100 text-blue-800', icon: CheckCircle},
};
// Format BSB as XXX-XXX
/**
 * @generated FunctionHeader
 * Function: formatBSB
 * Path: frontend/src/pages/dashboard/requests/ReimbursementTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const formatBSB = (value) => {
    const digits = value.replace(/\D/g, '').slice(0, 6);
    if (digits.length > 3) return `${digits.slice(0, 3)}-${digits.slice(3)}`;
    return digits;
};
/**
 * @generated FunctionHeader
 * Function: ReimbursementTab
 * Path: frontend/src/pages/dashboard/requests/ReimbursementTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ReimbursementTab = () => {
    const {api, user} = useAuth();
    const [loading, setLoading] = useState(false);
    const [reimbursements, setReimbursements] = useState([]);
    const [showForm, setShowForm] = useState(false);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: reimbursements,
        statusConfig,
        tab: 'reimbursement',
    });

    const [formData, setFormData] = useState({
        description: '',
        amount: '',
        expense_date: '',
        category: '',
        receipt_attachments: [],
        bank_account_name: '',
        bsb: '',
        account_number: '',
        approval_reference: '',
    });

    const fetchReimbursements = useCallback(async () => {
        try {
            const response = await api.get('/requests/reimbursements');
            setReimbursements(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch reimbursements:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchReimbursements();
    }, [fetchReimbursements]);
    /**
     * @generated FunctionHeader
     * Function: handleFileUpload
     * Path: frontend/src/pages/dashboard/requests/ReimbursementTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleFileUpload = (e) => {
        const files = Array.from(e.target.files);
        if (formData.receipt_attachments.length + files.length > 5) {
            toast.error('Maximum 5 receipts allowed');
            return;
        }
        files.forEach(file => {
            if (file.size > 5 * 1024 * 1024) {
                toast.error(`${file.name} exceeds 5MB limit`);
                return;
            }
            const reader = new FileReader();
            reader.onloadend = () => {
                setFormData(prev => ( {...prev, receipt_attachments: [...prev.receipt_attachments, reader.result]} ));
            };
            reader.readAsDataURL(file);
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/requests/ReimbursementTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.description || !formData.amount || !formData.expense_date || !formData.category) {
            toast.error('Please fill in all required fields');
            return;
        }
        if (!formData.bank_account_name || !formData.bsb || !formData.account_number) {
            toast.error('Please provide your bank account details for payment');
            return;
        }
        if (formData.receipt_attachments.length === 0) {
            toast.error('Please attach at least one receipt');
            return;
        }
        const bsbDigits = formData.bsb.replace(/\D/g, '');
        if (bsbDigits.length !== 6) {
            toast.error('BSB must be 6 digits');
            return;
        }

        setLoading(true);
        try {
            await api.post('/requests/reimbursements', {
                description: formData.description,
                amount: parseFloat(formData.amount),
                expense_date: formData.expense_date,
                category: formData.category,
                receipt_attachments: formData.receipt_attachments,
                bank_account_name: formData.bank_account_name,
                bsb: bsbDigits,
                account_number: formData.account_number,
            });
            trackRequestCatalogueEvent('request_form_submitted', {
                definition_key: 'reimbursement',
                definition_version: 1,
                role: user?.effective_role || user?.role,
                status: 'submitted',
            });
            toast.success('Reimbursement request submitted successfully');
            setFormData({
                description: '', amount: '', expense_date: '', category: '',
                receipt_attachments: [], bank_account_name: '', bsb: '', account_number: '', approval_reference: '',
            });
            setShowForm(false);
            fetchReimbursements();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to submit reimbursement request');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <Alert>
                <DollarSign className="h-4 w-4"/>
                <AlertDescription>
                    <strong>Reimbursements:</strong> Request repayment for approved expenses you paid out of pocket.
                    Attach original receipts. EC approval required — processing takes up to 14 days.
                </AlertDescription>
            </Alert>

            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Reimbursement Requests</h3>
                    <p className="text-sm text-muted-foreground">Claim back approved out-of-pocket expenses</p>
                </div>
                <Button onClick={() => setShowForm(!showForm)}>
                    <DollarSign className="mr-2 h-4 w-4"/>
                    {showForm ? 'Cancel' : 'Request Reimbursement'}
                </Button>
            </div>

            {showForm && (
                <Card className="border-primary/20">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-6">
                            {/* Expense Details */}
                            <div className="space-y-4">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">Expense
                                    Details</h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="category">Expense Category *</Label>
                                        <Select value={formData.category}
                                                onValueChange={value => setFormData(prev => ( {
                                                    ...prev,
                                                    category: value
                                                } ))}>
                                            <SelectTrigger>
                                                <SelectValue placeholder="Select category"/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                {EXPENSE_CATEGORIES.map(cat => (
                                                    <SelectItem key={cat.value}
                                                                value={cat.value}>{cat.label}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="expense_date">Expense Date *</Label>
                                        <Input
                                            id="expense_date"
                                            type="date"
                                            value={formData.expense_date}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                expense_date: e.target.value
                                            } ))}
                                            max={new Date().toISOString().split('T')[ 0 ]}
                                            required
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="amount">Amount (AUD) *</Label>
                                        <div className="relative">
                                            <span
                                                className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">$</span>
                                            <Input
                                                id="amount"
                                                type="number"
                                                min="0.01"
                                                step="0.01"
                                                value={formData.amount}
                                                onChange={e => setFormData(prev => ( {
                                                    ...prev,
                                                    amount: e.target.value
                                                } ))}
                                                className="pl-7"
                                                placeholder="0.00"
                                                required
                                            />
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="approval_reference">Pre-Approval Reference (optional)</Label>
                                        <Input
                                            id="approval_reference"
                                            value={formData.approval_reference}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                approval_reference: e.target.value
                                            } ))}
                                            placeholder="EC approval ref if applicable"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="description">Description *</Label>
                                    <Textarea
                                        id="description"
                                        value={formData.description}
                                        onChange={e => setFormData(prev => ( {...prev, description: e.target.value} ))}
                                        placeholder="Describe what the expense was for and why it was necessary..."
                                        rows={3}
                                        required
                                    />
                                </div>
                            </div>

                            {/* Receipts */}
                            <div className="space-y-3">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">Receipts
                                    *</h4>
                                <div className="border-2 border-dashed border-border rounded-lg p-4 text-center">
                                    <input
                                        type="file"
                                        accept="image/*,.pdf"
                                        multiple
                                        onChange={handleFileUpload}
                                        className="hidden"
                                        id="receipt-files"
                                    />
                                    <label htmlFor="receipt-files" className="cursor-pointer">
                                        <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground"/>
                                        <p className="text-sm text-muted-foreground">Upload receipts or invoices
                                            (PDF/image, up to 5 files, max 5MB each)</p>
                                    </label>
                                </div>
                                {formData.receipt_attachments.length > 0 && (
                                    <div className="flex gap-2 flex-wrap">
                                        {formData.receipt_attachments.map((att, idx) => (
                                            <div key={idx}
                                                 className="flex items-center gap-2 p-2 bg-muted rounded-lg text-sm">
                                                <span>Receipt {idx + 1}</span>
                                                <button type="button" onClick={() => setFormData(prev => ( {
                                                    ...prev,
                                                    receipt_attachments: prev.receipt_attachments.filter((_, i) => i !== idx)
                                                } ))}>
                                                    <X className="h-3 w-3"/>
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Bank Details */}
                            <div className="space-y-4">
                                <h4 className="font-semibold text-sm uppercase tracking-wide text-muted-foreground">Bank
                                    Account for Payment</h4>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                    <div className="md:col-span-3 space-y-2">
                                        <Label htmlFor="bank_account_name">Account Name *</Label>
                                        <Input
                                            id="bank_account_name"
                                            value={formData.bank_account_name}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                bank_account_name: e.target.value
                                            } ))}
                                            placeholder="Account holder name"
                                            required
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="bsb">BSB *</Label>
                                        <Input
                                            id="bsb"
                                            value={formData.bsb}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                bsb: formatBSB(e.target.value)
                                            } ))}
                                            placeholder="000-000"
                                            maxLength={7}
                                            required
                                        />
                                    </div>
                                    <div className="md:col-span-2 space-y-2">
                                        <Label htmlFor="account_number">Account Number *</Label>
                                        <Input
                                            id="account_number"
                                            value={formData.account_number}
                                            onChange={e => setFormData(prev => ( {
                                                ...prev,
                                                account_number: e.target.value.replace(/\D/g, '')
                                            } ))}
                                            placeholder="Account number"
                                            maxLength={10}
                                            required
                                        />
                                    </div>
                                </div>
                            </div>

                            <div className="flex gap-2 pt-2">
                                <Button type="submit" disabled={loading}>
                                    {loading ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/> Submitting...</> : 'Submit Reimbursement'}
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
                    <h4 className="font-semibold">Your Reimbursement Requests</h4>
                    <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
                </div>
                {filteredRecords.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            <DollarSign className="h-12 w-12 mx-auto mb-2 opacity-50"/>
                            <p>{statusFilter === 'all' ? 'No reimbursement requests submitted yet' : 'No reimbursement requests found for this status'}</p>
                        </CardContent>
                    </Card>
                ) : (
                    filteredRecords.map(req => {
                        const StatusIcon = statusConfig[ req.status ]?.icon || Clock;
                        const cat = EXPENSE_CATEGORIES.find(c => c.value === req.category);
                        return (
                            <Card key={req.id} className="border-l-4 border-l-emerald-400">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between mb-2">
                                        <div>
                                            <h5 className="font-semibold">{cat?.label || req.category} — {formatCurrency(req.amount)}</h5>
                                            <p className="text-xs text-muted-foreground">Expense
                                                date: {req.expense_date} · Submitted: {formatDate(req.created_at)}</p>
                                        </div>
                                        <Badge
                                            className={statusConfig[ req.status ]?.color || 'bg-gray-100 text-gray-800'}>
                                            <StatusIcon className="h-3 w-3 mr-1"/>
                                            {statusConfig[ req.status ]?.label || req.status}
                                        </Badge>
                                    </div>
                                    <p className="text-sm text-muted-foreground line-clamp-2">{req.description}</p>
                                    {req.paid_at && (
                                        <p className="text-xs text-green-600 mt-2">
                                            Paid on {formatDate(req.paid_at)}
                                            {req.payment_reference && ` · Ref: ${req.payment_reference}`}
                                        </p>
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

export default ReimbursementTab;
