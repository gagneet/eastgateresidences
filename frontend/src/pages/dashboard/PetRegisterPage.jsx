import React, { useCallback, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../components/ui/table';
import {
    AlertCircle, Bird, Cat, CheckCircle, Clock, Dog, Fish, Heart, Info, Loader2, Search, XCircle
} from 'lucide-react';
import { useApiData } from '../../hooks/useApiData';
import { toast } from 'sonner';

const WORKFLOW_STEPS = [
    {label: 'Submitted', status: 'pending', icon: Clock, color: 'text-amber-500'},
    {label: 'Under Review', status: 'under_review', icon: AlertCircle, color: 'text-blue-500'},
    {label: 'Approved', status: 'approved', icon: CheckCircle, color: 'text-green-600'},
    {label: 'Rejected', status: 'rejected', icon: XCircle, color: 'text-red-500'},
];
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const statusBadge = (status) => {
    const map = {
        pending: 'bg-amber-100 text-amber-800',
        under_review: 'bg-blue-100 text-blue-800',
        approved: 'bg-green-100 text-green-800',
        rejected: 'bg-red-100 text-red-800',
    };
    return map[ status ] || 'bg-gray-100 text-gray-700';
};
/**
 * @generated FunctionHeader
 * Function: getPetIcon
 * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const getPetIcon = (type = '') => {
    switch (type.toLowerCase()) {
        case 'dog':
            return <Dog className="h-4 w-4"/>;
        case 'cat':
            return <Cat className="h-4 w-4"/>;
        case 'bird':
            return <Bird className="h-4 w-4"/>;
        case 'fish':
            return <Fish className="h-4 w-4"/>;
        default:
            return <Heart className="h-4 w-4"/>;
    }
};
/**
 * @generated FunctionHeader
 * Function: PetRegisterPage
 * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PetRegisterPage = () => {
    const {api, user} = useAuth();
    const [searchTerm, setSearchTerm] = useState('');
    const [processing, setProcessing] = useState(null);

    const {data: response, loading, refetch} = useApiData(
        useCallback(() => api.get('/building/assets/pet-register'), [api])
    );

    const isAdmin = response?.view === 'admin';
    const allPets = response?.pets || [];

    const approved = allPets.filter(p => p.status === 'approved');
    const pending = allPets.filter(p => p.status === 'pending' || p.status === 'under_review');
    const rejected = allPets.filter(p => p.status === 'rejected');
    /**
     * @generated FunctionHeader
     * Function: filter
     * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const filter = (list) => list.filter(pet =>
        ( pet.pet_name || '' ).toLowerCase().includes(searchTerm.toLowerCase()) ||
        ( pet.unit_number || '' ).toString().includes(searchTerm) ||
        ( pet.breed || '' ).toLowerCase().includes(searchTerm.toLowerCase())
    );
    /**
     * @generated FunctionHeader
     * Function: handleAction
     * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAction = async (petId, action, notes = '') => {
        setProcessing(petId);
        try {
            await api.put(`/requests/pets/${petId}/status`, {status: action, notes});
            toast.success(`Request ${action} successfully`);
            refetch();
        } catch (err) {
            toast.error(`Failed to ${action} request`);
        } finally {
            setProcessing(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: PetRow
     * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const PetRow = ({pet, showActions}) => (
        <TableRow key={pet.id} className="hover:bg-muted/20 transition-colors">
            <TableCell className="pl-6 py-4">
                <div className="flex items-center gap-3">
                    {pet.pet_photo
                        ? <img src={pet.pet_photo} alt={pet.pet_name}
                               className="h-10 w-10 rounded-full object-cover border-2 border-primary/20"/>
                        : <div
                            className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center text-primary">{getPetIcon(pet.pet_type)}</div>
                    }
                    <div>
                        <p className="font-bold text-sm">{pet.pet_name}</p>
                        <p className="text-[10px] text-muted-foreground uppercase">{pet.age} yrs · {pet.size}</p>
                    </div>
                </div>
            </TableCell>
            <TableCell>
                <Badge variant="secondary" className="text-[10px] uppercase mr-2">{pet.pet_type}</Badge>
                <span className="text-xs text-muted-foreground">{pet.breed}</span>
            </TableCell>
            <TableCell>
                <Badge variant="outline" className="font-bold">Unit {pet.unit_number}</Badge>
            </TableCell>
            <TableCell>
                <div className="text-sm font-medium">{pet.submitted_by_name}</div>
            </TableCell>
            <TableCell>
                <span
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${statusBadge(pet.status)}`}>
                    {pet.status === 'pending' && <Clock className="h-3 w-3"/>}
                    {pet.status === 'approved' && <CheckCircle className="h-3 w-3"/>}
                    {pet.status === 'rejected' && <XCircle className="h-3 w-3"/>}
                    {pet.status}
                </span>
            </TableCell>
            {showActions && (
                <TableCell className="pr-4">
                    <div className="flex gap-2">
                        <Button size="sm" variant="outline"
                                className="text-green-700 border-green-300 hover:bg-green-50 text-xs h-7"
                                disabled={processing === pet.id || pet.status === 'approved'}
                                onClick={() => handleAction(pet.id, 'approved')}>
                            Approve
                        </Button>
                        <Button size="sm" variant="outline"
                                className="text-red-600 border-red-300 hover:bg-red-50 text-xs h-7"
                                disabled={processing === pet.id || pet.status === 'rejected'}
                                onClick={() => handleAction(pet.id, 'rejected')}>
                            Reject
                        </Button>
                    </div>
                </TableCell>
            )}
            {!showActions && (
                <TableCell className="pr-6">
                    {pet.conditions
                        ? <div className="flex items-start gap-1.5 text-xs text-muted-foreground"><Info
                            className="h-3 w-3 mt-0.5 shrink-0 text-primary"/><span
                            className="italic">"{pet.conditions}"</span></div>
                        : <span className="text-[10px] text-muted-foreground italic">Standard Rules</span>
                    }
                </TableCell>
            )}
        </TableRow>
    );
    /**
     * @generated FunctionHeader
     * Function: PetTable
     * Path: frontend/src/pages/dashboard/PetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const PetTable = ({list, showActions}) => (
        <Table>
            <TableHeader className="bg-muted/30">
                <TableRow>
                    <TableHead className="pl-6">Pet</TableHead>
                    <TableHead>Type & Breed</TableHead>
                    <TableHead>Unit</TableHead>
                    <TableHead>Resident</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="pr-6">{showActions ? 'Actions' : 'Conditions'}</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                {list.map(pet => <PetRow key={pet.id} pet={pet} showActions={showActions}/>)}
            </TableBody>
        </Table>
    );

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold">Pet Register</h1>
                <p className="text-muted-foreground">
                    {isAdmin
                        ? 'Manage all pet applications and view the approved register for your building.'
                        : 'Schemewide register of approved pets for emergency and compliance use.'}
                </p>
            </div>

            {/* Workflow information card for admins */}
            {isAdmin && (
                <Card className="border-blue-200 bg-blue-50/50">
                    <CardContent className="pt-4 pb-3">
                        <p className="text-xs font-semibold text-blue-800 mb-3 uppercase tracking-wide">Pet Approval
                            Workflow</p>
                        <div className="flex items-start gap-6 flex-wrap">
                            {WORKFLOW_STEPS.map((step, i) => (
                                <div key={step.status} className="flex items-center gap-2">
                                    <step.icon className={`h-4 w-4 ${step.color}`}/>
                                    <span className="text-xs font-medium text-gray-700">{i + 1}. {step.label}</span>
                                    {i < WORKFLOW_STEPS.length - 2 && <span className="text-gray-400">→</span>}
                                </div>
                            ))}
                        </div>
                        <p className="text-[11px] text-blue-700 mt-2">
                            Residents submit via <em>Requests → Pet Request</em>. EC/Strata Manager reviews and
                            approves/rejects.
                            Approved pets appear in the building register for emergency use. Rejection requires a
                            written reason
                            under the by-laws.
                        </p>
                    </CardContent>
                </Card>
            )}

            {/* Search */}
            <div className="flex items-center gap-4">
                <div className="relative flex-1 max-w-md">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                    <Input placeholder="Search by name, unit, or breed..." className="pl-10"
                           value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)}/>
                </div>
                <Badge variant="outline" className="h-8 px-3 shrink-0">
                    {isAdmin ? `${allPets.length} TOTAL REQUESTS` : `${approved.length} REGISTERED PETS`}
                </Badge>
            </div>

            {loading ? (
                <div className="flex justify-center py-12"><Loader2 className="h-8 w-8 animate-spin text-primary"/>
                </div>
            ) : isAdmin ? (
                <Tabs defaultValue={pending.length > 0 ? 'pending' : 'approved'}>
                    <TabsList>
                        <TabsTrigger value="pending">
                            Pending Review
                            {pending.length > 0 && <Badge
                                className="ml-2 bg-amber-500 text-white text-[10px] h-4 px-1">{pending.length}</Badge>}
                        </TabsTrigger>
                        <TabsTrigger value="approved">Approved ({approved.length})</TabsTrigger>
                        <TabsTrigger value="rejected">Rejected ({rejected.length})</TabsTrigger>
                    </TabsList>
                    <TabsContent value="pending">
                        <Card className="card-dashboard overflow-hidden">
                            <CardContent className="p-0">
                                {filter(pending).length === 0
                                    ? <div className="py-12 text-center text-muted-foreground">No pending requests</div>
                                    : <PetTable list={filter(pending)} showActions={true}/>
                                }
                            </CardContent>
                        </Card>
                    </TabsContent>
                    <TabsContent value="approved">
                        <Card className="card-dashboard overflow-hidden">
                            <CardContent className="p-0">
                                {filter(approved).length === 0
                                    ?
                                    <div className="py-12 text-center text-muted-foreground">No approved pets yet</div>
                                    : <PetTable list={filter(approved)} showActions={false}/>
                                }
                            </CardContent>
                        </Card>
                    </TabsContent>
                    <TabsContent value="rejected">
                        <Card className="card-dashboard overflow-hidden">
                            <CardContent className="p-0">
                                {filter(rejected).length === 0
                                    ?
                                    <div className="py-12 text-center text-muted-foreground">No rejected requests</div>
                                    : <PetTable list={filter(rejected)} showActions={true}/>
                                }
                            </CardContent>
                        </Card>
                    </TabsContent>
                </Tabs>
            ) : (
                <Card className="card-dashboard overflow-hidden">
                    <CardContent className="p-0">
                        {filter(approved).length === 0 ? (
                            <div className="py-24 text-center">
                                <div
                                    className="bg-primary/5 h-20 w-20 rounded-full flex items-center justify-center mx-auto mb-4">
                                    <Dog className="h-10 w-10 text-primary/30"/>
                                </div>
                                <h3 className="text-lg font-medium">No approved pets found</h3>
                                <p className="text-muted-foreground">No pets have been approved for this building
                                    yet.</p>
                            </div>
                        ) : (
                            <PetTable list={filter(approved)} showActions={false}/>
                        )}
                    </CardContent>
                </Card>
            )}

            <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 flex items-start gap-3">
                <Info className="h-5 w-5 text-blue-600 mt-0.5"/>
                <div className="text-xs text-blue-800 leading-relaxed">
                    <strong>Legislation Note:</strong> This register is maintained for health, safety, and emergency
                    management purposes. In the event of an evacuation, building management uses this list to ensure all
                    animal occupants are accounted for. Submit pet requests via the
                    <em>Requests &amp; Applications</em> portal (Maintenance &rarr; Requests &amp;
                    Applications &rarr; Pet application).
                </div>
            </div>
        </div>
    );
};

export default PetRegisterPage;
