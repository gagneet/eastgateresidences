import React, { useCallback, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Input } from '../../components/ui/input';
import { Button } from '../../components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import { Download, Loader2, Mail, MapPin, Phone, Search, ShieldCheck, User } from 'lucide-react';
import { useApiData } from '../../hooks/useApiData';
import { toast } from 'sonner';
import { downloadFile } from '../../lib/utils';
/**
 * @generated FunctionHeader
 * Function: StrataRollPage
 * Path: frontend/src/pages/dashboard/StrataRollPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StrataRollPage = () => {
    const {api} = useAuth();
    const [searchTerm, setSearchTerm] = useState('');

    const {
        data: roll,
        loading
    } = useApiData(
        useCallback(() => api.get('/building/assets/strata-roll'), [api])
    );

    const filteredRoll = roll?.filter(entry =>
        entry.owner_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        entry.unit_number.toString().includes(searchTerm) ||
        entry.lot_number?.toString().includes(searchTerm)
    ) || [];

    const [exporting, setExporting] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/pages/dashboard/StrataRollPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = async () => {
        setExporting(true);
        try {
            const response = await api.get('/building/assets/strata-roll/pdf', {responseType: 'blob'});
            const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
            downloadFile(new Blob([response.data], {type: 'application/pdf'}), `strata_roll_${today}.pdf`);
            toast.success('Strata Roll exported successfully');
        } catch {
            toast.error('Failed to generate PDF export');
        } finally {
            setExporting(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Strata Roll</h1>
                    <p className="text-muted-foreground">Formal Corporate Register (ACT UTMA 2011, s.91)</p>
                </div>
                <Button variant="outline" onClick={handleExport} disabled={exporting}>
                    {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                        <Download className="mr-2 h-4 w-4"/>}
                    {exporting ? 'Generating PDF...' : 'Export Register'}
                </Button>
            </div>

            <Card className="card-dashboard overflow-hidden">
                <CardHeader className="border-b pb-4">
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                        <div className="relative flex-1 max-w-md">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                placeholder="Search by owner, unit, or lot..."
                                className="pl-10"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                            />
                        </div>
                        <div className="flex items-center gap-2">
                            <ShieldCheck className="h-4 w-4 text-emerald-600"/>
                            <span
                                className="text-xs font-bold text-emerald-700 uppercase">Legislatively Compliant</span>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex justify-center py-12">
                            <Loader2 className="h-8 w-8 animate-spin text-primary"/>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader className="bg-muted/30">
                                    <TableRow>
                                        <TableHead className="pl-6 w-[150px]">Unit / Lot</TableHead>
                                        <TableHead>Ownership (Public Record)</TableHead>
                                        <TableHead>Registered Platform Users</TableHead>
                                        <TableHead className="w-[100px]">UOE</TableHead>
                                        <TableHead className="pr-6">Status</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredRoll.map((entry) => (
                                        <TableRow key={entry.unit_number}
                                                  className="hover:bg-muted/20 transition-colors">
                                            <TableCell className="pl-6 py-5">
                                                <div className="flex flex-col">
                                                    <span className="font-bold text-sm">Unit {entry.unit_number}</span>
                                                    <span
                                                        className="text-[10px] text-muted-foreground uppercase font-bold">Lot {entry.lot_number}</span>
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="space-y-1">
                                                    {/* Show all legal owners joined by & */}
                                                    <div className="text-sm font-semibold">
                                                        {entry.all_owner_names || entry.owner_name || '—'}
                                                    </div>
                                                    <div
                                                        className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                                                        <Mail className="h-3 w-3"/> {entry.owner_email || 'N/A'}
                                                    </div>
                                                    {entry.purchase_date && (
                                                        <div
                                                            className="text-[10px] text-muted-foreground italic">Acquired: {entry.purchase_date}</div>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="space-y-3">
                                                    {entry.registered_owners.length > 0 ? (
                                                        entry.registered_owners.map((ro, i) => (
                                                            <div key={i}
                                                                 className="p-2 rounded-lg bg-primary/5 border border-primary/10">
                                                                <div
                                                                    className="text-[11px] font-bold text-primary mb-1 flex items-center gap-1">
                                                                    <User className="h-2.5 w-2.5"/> {ro.full_name}
                                                                </div>
                                                                <div
                                                                    className="grid grid-cols-1 gap-1 text-[10px] text-muted-foreground">
                                                                    <div className="flex items-center gap-1.5"><Mail
                                                                        className="h-2.5 w-2.5"/> {ro.email}</div>
                                                                    {ro.phone &&
                                                                        <div className="flex items-center gap-1.5">
                                                                            <Phone className="h-2.5 w-2.5"/> {ro.phone}
                                                                        </div>}
                                                                    {ro.postal_address &&
                                                                        <div className="flex items-start gap-1.5">
                                                                            <MapPin
                                                                                className="h-2.5 w-2.5 mt-0.5"/> {ro.postal_address}
                                                                        </div>}
                                                                </div>
                                                            </div>
                                                        ))
                                                    ) : (
                                                        <span className="text-[10px] text-muted-foreground italic">No users registered on platform</span>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <Badge variant="outline"
                                                       className="font-mono text-sm">{entry.uoe}</Badge>
                                            </TableCell>
                                            <TableCell className="pr-6">
                                                {entry.is_owner_occupied ? (
                                                    <Badge
                                                        className="bg-emerald-50 text-emerald-700 hover:bg-emerald-50 border-none shadow-none text-[10px]">OCCUPIER</Badge>
                                                ) : (
                                                    <Badge
                                                        className="bg-blue-50 text-blue-700 hover:bg-blue-50 border-none shadow-none text-[10px]">LANDLORD</Badge>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
};

export default StrataRollPage;
