import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, } from '../../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import { Plus } from 'lucide-react';
import { formatDate } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: InsuranceClaimsPage
 * Path: frontend/src/pages/dashboard/InsuranceClaimsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InsuranceClaimsPage = () => {
    const {api, hasPermission, selectedBuilding} = useAuth();
    const [claims, setClaims] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);

    const buildingId = selectedBuilding?.building_id || '';
    const [claimForm, setClaimForm] = useState({
        insurer: '', claim_number: '', incident_date: '', description: '', building_id: buildingId
    });

    const canManage = hasPermission('can_manage_finances');
    /**
     * @generated FunctionHeader
     * Function: fetchClaims
     * Path: frontend/src/pages/dashboard/InsuranceClaimsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchClaims = async () => {
        try {
            const response = await api.get('/insurance-claims');
            setClaims(response.data);
        } catch (error) {
            console.error('Failed to fetch claims:', error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchClaims();
    }, []);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/InsuranceClaimsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/insurance-claims', claimForm);
            toast.success('Insurance claim reported');
            setDialogOpen(false);
            setClaimForm({insurer: '', claim_number: '', incident_date: '', description: '', building_id: buildingId});
            fetchClaims();
        } catch (error) {
            toast.error('Failed to report claim');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Insurance Claims</h1>
                    <p className="text-muted-foreground">Track and manage building insurance claims</p>
                </div>
                {canManage && (
                    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                        <DialogTrigger asChild>
                            <Button><Plus className="mr-2 h-4 w-4"/>New Claim</Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>Report Insurance Claim</DialogTitle>
                            </DialogHeader>
                            <form onSubmit={handleSubmit} className="space-y-4">
                                <div className="space-y-2">
                                    <Label>Insurer</Label>
                                    <Input value={claimForm.insurer} onChange={(e) => setClaimForm(prev => ( {
                                        ...prev,
                                        insurer: e.target.value
                                    } ))} required/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Claim Number (if known)</Label>
                                    <Input value={claimForm.claim_number} onChange={(e) => setClaimForm(prev => ( {
                                        ...prev,
                                        claim_number: e.target.value
                                    } ))}/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Incident Date</Label>
                                    <Input type="date" value={claimForm.incident_date}
                                           onChange={(e) => setClaimForm(prev => ( {
                                               ...prev,
                                               incident_date: e.target.value
                                           } ))} required/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Description</Label>
                                    <Textarea value={claimForm.description} onChange={(e) => setClaimForm(prev => ( {
                                        ...prev,
                                        description: e.target.value
                                    } ))} required/>
                                </div>
                                <Button type="submit" className="w-full" disabled={submitting}>Submit Claim</Button>
                            </form>
                        </DialogContent>
                    </Dialog>
                )}
            </div>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Claim #</TableHead>
                                <TableHead>Insurer</TableHead>
                                <TableHead>Incident Date</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {claims.map(claim => (
                                <TableRow key={claim.id}>
                                    <TableCell className="font-medium">{claim.claim_number || 'Pending'}</TableCell>
                                    <TableCell>{claim.insurer}</TableCell>
                                    <TableCell>{formatDate(claim.incident_date)}</TableCell>
                                    <TableCell><Badge variant="outline"
                                                      className="capitalize">{claim.status}</Badge></TableCell>
                                    <TableCell>
                                        <Button variant="ghost" size="sm">View Details</Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {!loading && claims.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center py-12 text-muted-foreground">
                                        No insurance claims reported
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
};

export default InsuranceClaimsPage;
