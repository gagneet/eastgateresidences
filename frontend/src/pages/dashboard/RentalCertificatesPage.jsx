// @featuretrace:documents — s.119 rental/tenancy certificate requests, issuance, and PDF download.
// Layer: frontend
// Data flow: RentalCertificatesPage → GET/POST /rental-certificates,
//            PUT /rental-certificates/{id}/issue, GET /rental-certificates/{id}/pdf
//            → rental_certificates (building-scoped).
// Related: backend/routers/rental_certificates.py
//           frontend/src/hooks/useActiveUnit.ts (the unit a certificate is issued for)
//
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import {
    AlertCircle,
    Building2,
    CheckCircle2,
    Clock,
    Download,
    FileCheck,
    FileText,
    Loader2,
    Plus,
    RefreshCw,
    Shield,
} from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatusBadge = ({status}) => {
    if (status === 'issued') {
        return (
            <Badge className="bg-green-600 text-white hover:bg-green-700">
                <CheckCircle2 className="h-3 w-3 mr-1"/> Issued
            </Badge>
        );
    }
    if (status === 'superseded') {
        return (
            <Badge variant="secondary">
                <AlertCircle className="h-3 w-3 mr-1"/> Superseded
            </Badge>
        );
    }
    return (
        <Badge variant="outline" className="border-amber-500 text-amber-600">
            <Clock className="h-3 w-3 mr-1"/> Pending
        </Badge>
    );
};
/**
 * @generated FunctionHeader
 * Function: RentalCertificatesPage
 * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const RentalCertificatesPage = () => {
    const {api, user, isManager} = useAuth();
    const {activeUnit} = useActiveUnit();
    // Strict role check: only a pure owner has a locked unit — managers with a unit
    // should still be able to request for any unit they choose.
    const isOwner = user?.role === 'owner';

    const [certificates, setCertificates] = useState([]);
    const [loading, setLoading] = useState(true);
    const [requestDialogOpen, setRequestDialogOpen] = useState(false);
    const [issueDialogOpen, setIssueDialogOpen] = useState(false);
    const [selectedCert, setSelectedCert] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [downloadingId, setDownloadingId] = useState(null);

    const [requestForm, setRequestForm] = useState({
        unit_number: '',
        tenant_name: '',
        lease_start_date: '',
        lease_end_date: '',
        additional_notes: '',
    });
    // Initialise the request form freshly each time the dialog is opened so the
    // unit_number is always read from the resolved user object (avoids the race
    // where useState runs once at mount before the auth context has loaded).
    /**
     * @generated FunctionHeader
     * Function: openRequestDialog
     * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openRequestDialog = () => {
        setRequestForm({
            // A certificate is issued for one unit — default to the active one so a
            // multi-unit owner does not request against the wrong lot.
            unit_number: activeUnit || '',
            tenant_name: '',
            lease_start_date: '',
            lease_end_date: '',
            additional_notes: '',
        });
        setRequestDialogOpen(true);
    };

    const [issueForm, setIssueForm] = useState({
        insurance_insurer: '',
        insurance_coverage_amount: '',
        insurance_public_liability: '$10,000,000',
        insurance_expiry: '',
        known_defects: 'None at time of issue',
        special_levies_pending: 'None pending at time of issue',
        common_property_notes: '',
        strata_manager_name: '',
        strata_manager_contact: '',
        strata_manager_licence: '',
    });

    const fetchCertificates = useCallback(async () => {
        try {
            setLoading(true);
            const response = await api.get('/rental-certificates');
            setCertificates(response.data || []);
        } catch (error) {
            console.error('Failed to fetch rental certificates:', error);
            toast.error('Failed to load rental certificates');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchCertificates();
    }, [fetchCertificates]);
    /**
     * @generated FunctionHeader
     * Function: handleRequest
     * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRequest = async (e) => {
        e.preventDefault();
        if (!requestForm.tenant_name || !requestForm.lease_start_date) {
            toast.error('Tenant name and lease start date are required');
            return;
        }
        try {
            setSubmitting(true);
            await api.post('/rental-certificates', requestForm);
            toast.success('Certificate request submitted successfully');
            setRequestDialogOpen(false);
            setRequestForm({
                unit_number: activeUnit || '',
                tenant_name: '',
                lease_start_date: '',
                lease_end_date: '',
                additional_notes: '',
            });  // user is guaranteed resolved here — form reset is safe
            fetchCertificates();
        } catch (error) {
            const msg = error.response?.data?.detail || 'Failed to submit request';
            toast.error(msg);
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleIssue
     * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleIssue = async (e) => {
        e.preventDefault();
        if (!selectedCert) return;
        try {
            setSubmitting(true);
            const payload = {...issueForm};
            if (payload.insurance_coverage_amount) {
                payload.insurance_coverage_amount = parseFloat(payload.insurance_coverage_amount);
            } else {
                delete payload.insurance_coverage_amount;
            }
            await api.put(`/rental-certificates/${selectedCert.id}/issue`, payload);
            toast.success(`Certificate ${selectedCert.cert_number} issued successfully`);
            setIssueDialogOpen(false);
            setSelectedCert(null);
            fetchCertificates();
        } catch (error) {
            const msg = error.response?.data?.detail || 'Failed to issue certificate';
            toast.error(msg);
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownloadPdf
     * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadPdf = async (cert) => {
        try {
            setDownloadingId(cert.id);
            const response = await api.get(`/rental-certificates/${cert.id}/pdf`, {
                responseType: 'blob',
            });
            const url = window.URL.createObjectURL(new Blob([response.data], {type: 'application/pdf'}));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `${cert.cert_number}.pdf`);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
            toast.success('PDF downloaded');
        } catch (error) {
            toast.error('Failed to download PDF');
        } finally {
            setDownloadingId(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openIssueDialog
     * Path: frontend/src/pages/dashboard/RentalCertificatesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openIssueDialog = (cert) => {
        setSelectedCert(cert);
        setIssueForm({
            insurance_insurer: cert.insurance_insurer || '',
            insurance_coverage_amount: cert.insurance_coverage_amount || '',
            insurance_public_liability: cert.insurance_public_liability || '$10,000,000',
            insurance_expiry: cert.insurance_expiry || '',
            known_defects: cert.known_defects || 'None at time of issue',
            special_levies_pending: cert.special_levies_pending || 'None pending at time of issue',
            common_property_notes: cert.common_property_notes || '',
            strata_manager_name: cert.strata_manager_name || '',
            strata_manager_contact: cert.strata_manager_contact || '',
            strata_manager_licence: cert.strata_manager_licence || '',
        });
        setIssueDialogOpen(true);
    };

    const requestedCerts = certificates.filter((c) => c.status === 'requested');
    const issuedCerts = certificates.filter((c) => c.status === 'issued');

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Page header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <FileCheck className="h-6 w-6 text-primary"/>
                        Rental Certificates
                    </h1>
                    <p className="text-muted-foreground text-sm mt-1">
                        ACT Section 119A — Unit Titles (Management) Act 2011
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchCertificates}>
                        <RefreshCw className="h-4 w-4 mr-2"/> Refresh
                    </Button>
                    {( isOwner || isManager ) && (
                        <Button size="sm" onClick={() => openRequestDialog()}>
                            <Plus className="h-4 w-4 mr-2"/> Request Certificate
                        </Button>
                    )}
                </div>
            </div>

            {/* Compliance notice banner */}
            <Card className="border-amber-200 bg-amber-50">
                <CardContent className="py-3 flex items-start gap-3">
                    <Shield className="h-5 w-5 text-amber-600 mt-0.5 shrink-0"/>
                    <div className="text-sm text-amber-800">
                        <strong>Mandatory since 9 January 2025:</strong> Landlords must provide a Rental
                        Certificate to incoming tenants before signing a lease. Certificates are valid for{' '}
                        <strong>5 years</strong> and have a government fee cap of <strong>$342</strong>.
                    </div>
                </CardContent>
            </Card>

            {/* Stats row */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">Total Certificates</p>
                            <FileText className="h-4 w-4 text-muted-foreground"/>
                        </div>
                        <p className="text-2xl font-bold mt-1">{certificates.length}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">Pending Requests</p>
                            <Clock className="h-4 w-4 text-amber-500"/>
                        </div>
                        <p className="text-2xl font-bold mt-1 text-amber-600">{requestedCerts.length}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4 pb-3">
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">Issued (Active)</p>
                            <CheckCircle2 className="h-4 w-4 text-green-600"/>
                        </div>
                        <p className="text-2xl font-bold mt-1 text-green-700">{issuedCerts.length}</p>
                    </CardContent>
                </Card>
            </div>

            {/* Main content tabs */}
            <Tabs defaultValue={isManager ? 'manage' : 'my'}>
                <TabsList>
                    {( isOwner || isManager ) && <TabsTrigger value="my">My Certificates</TabsTrigger>}
                    {isManager && (
                        <TabsTrigger value="manage">
                            Manage Requests
                            {requestedCerts.length > 0 && (
                                <Badge variant="destructive" className="ml-2 h-5 min-w-5 px-1">
                                    {requestedCerts.length}
                                </Badge>
                            )}
                        </TabsTrigger>
                    )}
                </TabsList>

                {/* ── Tab: My Certificates ── */}
                {( isOwner || isManager ) && (
                    <TabsContent value="my" className="mt-4">
                        {certificates.length === 0 ? (
                            <Card>
                                <CardContent className="py-12 text-center text-muted-foreground">
                                    <FileCheck className="h-12 w-12 mx-auto mb-3 opacity-30"/>
                                    <p>No rental certificates yet.</p>
                                    {( isOwner || isManager ) && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="mt-4"
                                            onClick={() => openRequestDialog()}
                                        >
                                            <Plus className="h-4 w-4 mr-2"/> Request your first certificate
                                        </Button>
                                    )}
                                </CardContent>
                            </Card>
                        ) : (
                            <div className="space-y-3">
                                {certificates.map((cert) => (
                                    <Card key={cert.id} className="hover:shadow-sm transition-shadow">
                                        <CardContent className="py-4">
                                            <div className="flex items-start justify-between gap-4">
                                                <div className="flex items-center gap-3">
                                                    <div className="rounded-full bg-primary/10 p-2">
                                                        <FileCheck className="h-4 w-4 text-primary"/>
                                                    </div>
                                                    <div>
                                                        <p className="font-semibold text-sm">{cert.cert_number}</p>
                                                        <p className="text-xs text-muted-foreground">
                                                            Unit {cert.unit_number} · Tenant: {cert.tenant_name}
                                                        </p>
                                                        <p className="text-xs text-muted-foreground mt-0.5">
                                                            Requested:{' '}
                                                            {new Date(cert.requested_at).toLocaleDateString('en-AU')}
                                                            {cert.issued_at && (
                                                                <> ·
                                                                    Issued: {new Date(cert.issued_at).toLocaleDateString('en-AU')}</>
                                                            )}
                                                            {cert.expiry_date && (
                                                                <> · Expires: {cert.expiry_date.slice(0, 10)}</>
                                                            )}
                                                        </p>
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2 shrink-0">
                                                    <StatusBadge status={cert.status}/>
                                                    {cert.status === 'issued' && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={() => handleDownloadPdf(cert)}
                                                            disabled={downloadingId === cert.id}
                                                        >
                                                            {downloadingId === cert.id ? (
                                                                <Loader2 className="h-4 w-4 animate-spin"/>
                                                            ) : (
                                                                <Download className="h-4 w-4"/>
                                                            )}
                                                            <span className="ml-1 hidden sm:inline">PDF</span>
                                                        </Button>
                                                    )}
                                                </div>
                                            </div>
                                        </CardContent>
                                    </Card>
                                ))}
                            </div>
                        )}
                    </TabsContent>
                )}

                {/* ── Tab: Manage Requests (admin) ── */}
                {isManager && (
                    <TabsContent value="manage" className="mt-4">
                        <Card>
                            <CardHeader className="pb-2">
                                <CardTitle className="text-base">All Certificate Requests</CardTitle>
                                <CardDescription className="text-xs">
                                    Review pending requests and issue certificates with required ACT s.119A details.
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                {certificates.length === 0 ? (
                                    <p className="text-center text-muted-foreground py-8">No certificate requests.</p>
                                ) : (
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Cert #</TableHead>
                                                <TableHead>Unit</TableHead>
                                                <TableHead>Tenant</TableHead>
                                                <TableHead>Requested By</TableHead>
                                                <TableHead>Date</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead>Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {certificates.map((cert) => (
                                                <TableRow key={cert.id}>
                                                    <TableCell
                                                        className="font-mono text-xs">{cert.cert_number}</TableCell>
                                                    <TableCell>
                                                        <Badge variant="outline" className="text-xs">
                                                            <Building2 className="h-3 w-3 mr-1"/>
                                                            {cert.unit_number}
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell className="text-sm">{cert.tenant_name}</TableCell>
                                                    <TableCell className="text-sm">{cert.requested_by_name}</TableCell>
                                                    <TableCell className="text-xs text-muted-foreground">
                                                        {new Date(cert.requested_at).toLocaleDateString('en-AU')}
                                                    </TableCell>
                                                    <TableCell>
                                                        <StatusBadge status={cert.status}/>
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex gap-1">
                                                            {cert.status === 'requested' && (
                                                                <Button
                                                                    size="sm"
                                                                    variant="default"
                                                                    onClick={() => openIssueDialog(cert)}
                                                                >
                                                                    <CheckCircle2 className="h-3 w-3 mr-1"/> Issue
                                                                </Button>
                                                            )}
                                                            {cert.status === 'issued' && (
                                                                <>
                                                                    <Button
                                                                        size="sm"
                                                                        variant="outline"
                                                                        onClick={() => handleDownloadPdf(cert)}
                                                                        disabled={downloadingId === cert.id}
                                                                    >
                                                                        {downloadingId === cert.id ? (
                                                                            <Loader2 className="h-3 w-3 animate-spin"/>
                                                                        ) : (
                                                                            <Download className="h-3 w-3"/>
                                                                        )}
                                                                        <span className="ml-1">PDF</span>
                                                                    </Button>
                                                                    <Button
                                                                        size="sm"
                                                                        variant="ghost"
                                                                        onClick={() => openIssueDialog(cert)}
                                                                        title="Update certificate details"
                                                                    >
                                                                        Edit
                                                                    </Button>
                                                                </>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                )}
                            </CardContent>
                        </Card>
                    </TabsContent>
                )}
            </Tabs>

            {/* ── Request Certificate Dialog ── */}
            <Dialog open={requestDialogOpen} onOpenChange={setRequestDialogOpen}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <FileCheck className="h-5 w-5"/> Request Rental Certificate
                        </DialogTitle>
                        <DialogDescription>
                            Complete the form to request an ACT s.119A Rental Certificate for an incoming tenant.
                        </DialogDescription>
                    </DialogHeader>

                    <form onSubmit={handleRequest} className="space-y-4">
                        <div>
                            <Label htmlFor="rc-unit">Unit Number</Label>
                            <Input
                                id="rc-unit"
                                value={requestForm.unit_number}
                                onChange={(e) => setRequestForm({...requestForm, unit_number: e.target.value})}
                                disabled={isOwner}
                                placeholder="e.g. TH017"
                                required
                            />
                        </div>
                        <div>
                            <Label htmlFor="rc-tenant">Tenant Full Name *</Label>
                            <Input
                                id="rc-tenant"
                                value={requestForm.tenant_name}
                                onChange={(e) => setRequestForm({...requestForm, tenant_name: e.target.value})}
                                placeholder="Full name of incoming tenant"
                                required
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label htmlFor="rc-start">Lease Start Date *</Label>
                                <Input
                                    id="rc-start"
                                    type="date"
                                    value={requestForm.lease_start_date}
                                    onChange={(e) =>
                                        setRequestForm({...requestForm, lease_start_date: e.target.value})
                                    }
                                    required
                                />
                            </div>
                            <div>
                                <Label htmlFor="rc-end">Lease End Date</Label>
                                <Input
                                    id="rc-end"
                                    type="date"
                                    value={requestForm.lease_end_date}
                                    onChange={(e) =>
                                        setRequestForm({...requestForm, lease_end_date: e.target.value})
                                    }
                                />
                            </div>
                        </div>
                        <div>
                            <Label htmlFor="rc-notes">Additional Notes</Label>
                            <Textarea
                                id="rc-notes"
                                rows={2}
                                value={requestForm.additional_notes}
                                onChange={(e) =>
                                    setRequestForm({...requestForm, additional_notes: e.target.value})
                                }
                                placeholder="Any relevant information for the EC/admin"
                            />
                        </div>

                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setRequestDialogOpen(false)}>
                                Cancel
                            </Button>
                            <Button type="submit" disabled={submitting}>
                                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                                Submit Request
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            {/* ── Issue Certificate Dialog (admin) ── */}
            <Dialog open={issueDialogOpen} onOpenChange={setIssueDialogOpen}>
                <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <CheckCircle2 className="h-5 w-5 text-green-600"/>
                            {selectedCert?.status === 'issued' ? 'Update' : 'Issue'} Certificate{' '}
                            {selectedCert?.cert_number}
                        </DialogTitle>
                        <DialogDescription>
                            Unit <strong>{selectedCert?.unit_number}</strong> · Tenant:{' '}
                            <strong>{selectedCert?.tenant_name}</strong>
                            <br/>
                            Financial data (levies, arrears, EC members) will be auto-populated from the database.
                        </DialogDescription>
                    </DialogHeader>

                    <form onSubmit={handleIssue} className="space-y-4">
                        {/* Insurance */}
                        <div className="space-y-3">
                            <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                                Insurance Details
                            </h3>
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label htmlFor="is-insurer">Insurer Name</Label>
                                    <Input
                                        id="is-insurer"
                                        value={issueForm.insurance_insurer}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, insurance_insurer: e.target.value})
                                        }
                                        placeholder="e.g. Strata Community Insurance"
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="is-coverage">Coverage Amount ($)</Label>
                                    <Input
                                        id="is-coverage"
                                        type="number"
                                        value={issueForm.insurance_coverage_amount}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, insurance_coverage_amount: e.target.value})
                                        }
                                        placeholder="e.g. 12000000"
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="is-liability">Public Liability</Label>
                                    <Input
                                        id="is-liability"
                                        value={issueForm.insurance_public_liability}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, insurance_public_liability: e.target.value})
                                        }
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="is-expiry">Policy Expiry Date</Label>
                                    <Input
                                        id="is-expiry"
                                        type="date"
                                        value={issueForm.insurance_expiry}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, insurance_expiry: e.target.value})
                                        }
                                    />
                                </div>
                            </div>
                        </div>

                        {/* Strata Manager */}
                        <div className="space-y-3">
                            <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                                Strata Manager
                            </h3>
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label htmlFor="sm-name">Name</Label>
                                    <Input
                                        id="sm-name"
                                        value={issueForm.strata_manager_name}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, strata_manager_name: e.target.value})
                                        }
                                        placeholder="Strata manager name"
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="sm-contact">Contact</Label>
                                    <Input
                                        id="sm-contact"
                                        value={issueForm.strata_manager_contact}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, strata_manager_contact: e.target.value})
                                        }
                                        placeholder="Phone or email"
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="sm-licence">Licence Number</Label>
                                    <Input
                                        id="sm-licence"
                                        value={issueForm.strata_manager_licence}
                                        onChange={(e) =>
                                            setIssueForm({...issueForm, strata_manager_licence: e.target.value})
                                        }
                                        placeholder="ACT licence #"
                                    />
                                </div>
                            </div>
                        </div>

                        {/* Additional */}
                        <div className="space-y-3">
                            <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                                Defects &amp; Levies
                            </h3>
                            <div>
                                <Label htmlFor="if-defects">Known Defects</Label>
                                <Textarea
                                    id="if-defects"
                                    rows={2}
                                    value={issueForm.known_defects}
                                    onChange={(e) =>
                                        setIssueForm({...issueForm, known_defects: e.target.value})
                                    }
                                />
                            </div>
                            <div>
                                <Label htmlFor="if-levies">Special Levies Pending</Label>
                                <Textarea
                                    id="if-levies"
                                    rows={2}
                                    value={issueForm.special_levies_pending}
                                    onChange={(e) =>
                                        setIssueForm({...issueForm, special_levies_pending: e.target.value})
                                    }
                                />
                            </div>
                            <div>
                                <Label htmlFor="if-common">Common Property Notes</Label>
                                <Textarea
                                    id="if-common"
                                    rows={2}
                                    value={issueForm.common_property_notes}
                                    onChange={(e) =>
                                        setIssueForm({...issueForm, common_property_notes: e.target.value})
                                    }
                                    placeholder="Any relevant common property information"
                                />
                            </div>
                        </div>

                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setIssueDialogOpen(false)}>
                                Cancel
                            </Button>
                            <Button type="submit" disabled={submitting} className="bg-green-600 hover:bg-green-700">
                                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                                {selectedCert?.status === 'issued' ? 'Update Certificate' : 'Issue Certificate'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default RentalCertificatesPage;
