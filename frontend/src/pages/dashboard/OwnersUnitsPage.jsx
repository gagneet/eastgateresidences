import React, { useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
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
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '../../components/ui/alert-dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Label } from '../../components/ui/label';
import { Checkbox } from '../../components/ui/checkbox';
import { Textarea } from '../../components/ui/textarea';
import {
    AlertCircle,
    ArrowDown,
    ArrowUp,
    ArrowUpDown,
    Bath,
    BedDouble,
    Building,
    Car,
    Check,
    Download,
    Edit,
    LayoutGrid,
    Loader2,
    Search,
    ShieldCheck,
    Trash2,
    Upload,
    UserCheck,
    Users as UsersIcon,
    UserX,
    X
} from 'lucide-react';
import { toast } from 'sonner';
import { downloadFile } from '../../lib/utils';
/**
 * @generated FunctionHeader
 * Function: OwnersUnitsPage
 * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const OwnersUnitsPage = () => {
    const searchParams = useSearchParams();
    const router = useRouter();
    const {api, isAdmin, isManager, hasFeatureAccess} = useAuth();
    const [units, setUnits] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [balanceFilter, setBalanceFilter] = useState(searchParams.get('filter') || 'all');
    const [columnFilters, setColumnFilters] = useState({
        unit_lot: '',
        owner: '',
        bedrooms: '',
        bathrooms: '',
        garage: '',
        uoe: '',
        status: '',
        platform: '',
        balance: ''
    });
    const [sortConfig, setSortConfig] = useState({key: 'unit_number', direction: 'asc'});
    const [selectedUnit, setSelectedUnit] = useState(null);
    const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
    const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
    const [unitToDelete, setUnitToDelete] = useState(null);
    const [isImporting, setIsImporting] = useState(false);
    const [isExporting, setIsExporting] = useState(false);
    const [fetchError, setFetchError] = useState(false);

    const [formData, setFormData] = useState({
        unit_number: '',
        lot_number: '',
        owner_name: '',
        owner_name_b: '',
        owner_email: '',
        owner_email_b: '',
        bedrooms: '',
        bathrooms: '',
        garage_spaces: '',
        level: '',
        unit_entitlement: '',
        is_owner_occupied: true,
        tenant_name: '',
        tenant_email: '',
        purchase_date: '',
        approval_date: '',
        notes: '',
        permissions: {}
    });

    const fetchUnits = React.useCallback(async () => {
        try {
            setLoading(true);
            setFetchError(false);
            const response = await api.get('/owners-units');
            setUnits(response.data);
        } catch (error) {
            console.error('Failed to fetch units:', error);
            setFetchError(true);
            toast.error('Inventory synchronization failed');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchUnits();
    }, [fetchUnits]);

    // Runtime-derived filter options from loaded data
    const bedroomOpts = useMemo(() =>
            [...new Set(units.map(u => u.bedrooms).filter(v => v != null))].sort((a, b) => a - b),
        [units]);
    const bathroomOpts = useMemo(() =>
            [...new Set(units.map(u => u.bathrooms).filter(v => v != null))].sort((a, b) => a - b),
        [units]);
    const garageOpts = useMemo(() =>
            [...new Set(units.map(u => u.garage_spaces).filter(v => v != null))].sort((a, b) => a - b),
        [units]);
    const uoeOptions = useMemo(() =>
            [...new Set(units.map(u => u.unit_entitlement).filter(v => v != null))].sort((a, b) => a - b),
        [units]);
    /**
     * @generated FunctionHeader
     * Function: handleExportStrataRoll
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExportStrataRoll = async () => {
        setIsExporting(true);
        try {
            const response = await api.get('/building/assets/strata-roll/pdf', {responseType: 'blob'});
            const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
            downloadFile(new Blob([response.data], {type: 'application/pdf'}), `strata_roll_${today}.pdf`);
            toast.success('Strata Roll exported successfully');
        } catch {
            toast.error('Failed to generate Strata Roll PDF');
        } finally {
            setIsExporting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleImportFromPDF
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleImportFromPDF = async () => {
        setIsImporting(true);
        try {
            const response = await api.post('/owners-units/import-from-pdf');
            toast.success(`Infrastructure updated: ${response.data.units_imported} records processed.`);
            fetchUnits();
        } catch (error) {
            // Surface the server's reason rather than a blanket failure. This endpoint
            // now returns 410 with an explanation (the underlying importer is archived
            // and not building-scoped — see GAP-SEC-001), and "Bulk import process
            // failed" gave an admin no way to know that.
            //
            // Read `data.error.message` FIRST: this API wraps errors in the envelope
            // built by utils/error_response.py — `{"error": {code, message, status,
            // request_id, retryable}}` — and emits no top-level `detail`, so reading
            // `data.detail` alone (as several older call sites do) always falls
            // through to the generic string. `data.detail` is kept as a fallback for
            // any handler that bypasses the envelope.
            const serverMessage = error?.response?.data?.error?.message
                || error?.response?.data?.detail;
            toast.error(serverMessage || 'Bulk import process failed');
        } finally {
            setIsImporting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleEdit
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleEdit = (unit) => {
        setSelectedUnit(unit);
        setFormData({
            ...unit,
            owner_name: unit.owner_name || '',
            owner_name_b: unit.owner_name_b || '',
            owner_email: unit.owner_email || '',
            owner_email_b: unit.owner_email_b || '',
            tenant_name: unit.tenant_name || '',
            tenant_email: unit.tenant_email || '',
            notes: unit.notes || '',
            permissions: unit.permissions || {}
        });
        setIsEditDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        try {
            const {
                owner_name,
                owner_name_b,
                owner_email,
                owner_email_b,
                ...payload
            } = {...formData};
            ['bedrooms', 'bathrooms', 'garage_spaces', 'level', 'unit_entitlement'].forEach(key => {
                if (payload[ key ] === '') payload[ key ] = null;
                else if (payload[ key ]) payload[ key ] = parseInt(payload[ key ]);
            });

            await api.put(`/owners-units/${selectedUnit.unit_number}`, payload);
            toast.success(`Unit ${selectedUnit.unit_number} record updated`);
            setIsEditDialogOpen(false);
            fetchUnits();
        } catch (error) {
            toast.error('Failed to commit changes');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDeleteConfirm
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteConfirm = async () => {
        if (!unitToDelete) return;
        try {
            await api.delete(`/owners-units/${unitToDelete.unit_number}`);
            toast.success('Asset record purged');
            fetchUnits();
        } catch (error) {
            toast.error('Deletion operation failed');
        } finally {
            setIsDeleteDialogOpen(false);
            setUnitToDelete(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSort
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSort = (key) => {
        setSortConfig(prev => ( {
            key,
            direction: prev.key === key && prev.direction === 'asc' ? 'desc' : 'asc'
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: setFilter
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const setFilter = (field, value) => setColumnFilters(prev => ( {...prev, [ field ]: value} ));
    /**
     * @generated FunctionHeader
     * Function: SortIcon
     * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const SortIcon = ({columnKey}) => {
        if (sortConfig.key !== columnKey) return <ArrowUpDown className="h-3 w-3 opacity-40"/>;
        return sortConfig.direction === 'asc'
            ? <ArrowUp className="h-3 w-3 text-primary"/>
            : <ArrowDown className="h-3 w-3 text-primary"/>;
    };

    const sortedAndFilteredUnits = useMemo(() => {
        let result = units.filter(u => {
            // Global Search — includes bathrooms
            const matchesGlobal = !searchTerm ||
                u.owner_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
                u.unit_number?.toString().includes(searchTerm) ||
                u.lot_number?.toString().includes(searchTerm) ||
                `${u.bedrooms}BR`.includes(searchTerm) ||
                `${u.bathrooms}BA`.includes(searchTerm) ||
                `${u.garage_spaces}G`.includes(searchTerm);
            if (!matchesGlobal) return false;

            // Balance Type Filter (Stat Cards) — use current balance_owing / balance_credit
            if (balanceFilter === 'arrears' && ( u.balance_owing ?? 0 ) <= 0.01) return false;
            if (balanceFilter === 'credit' && ( u.balance_credit ?? 0 ) <= 0.01) return false;

            // Column Filters
            const cf = columnFilters;
            if (cf.unit_lot && !`${u.unit_number} ${u.lot_number}`.toLowerCase().includes(cf.unit_lot.toLowerCase())) return false;
            if (cf.owner && !u.owner_name?.toLowerCase().includes(cf.owner.toLowerCase()) && !u.owner_name_b?.toLowerCase().includes(cf.owner.toLowerCase())) return false;
            if (cf.bedrooms && cf.bedrooms !== 'all' && u.bedrooms?.toString() !== cf.bedrooms) return false;
            if (cf.bathrooms && cf.bathrooms !== 'all' && u.bathrooms?.toString() !== cf.bathrooms) return false;
            if (cf.garage && cf.garage !== 'all' && u.garage_spaces?.toString() !== cf.garage) return false;
            if (cf.uoe && cf.uoe !== 'all' && u.unit_entitlement?.toString() !== cf.uoe) return false;
            if (cf.status === 'owner_occupied' && !u.is_owner_occupied) return false;
            if (cf.status === 'tenanted' && u.is_owner_occupied) return false;
            if (cf.platform === 'active' && !u.is_on_platform) return false;
            if (cf.platform === 'offline' && u.is_on_platform) return false;
            if (cf.balance && cf.balance !== 'all') {
                if (cf.balance === 'arrears' && ( u.balance_owing ?? 0 ) <= 0.01) return false;
                if (cf.balance === 'credit' && ( u.balance_credit ?? 0 ) <= 0.01) return false;
                if (cf.balance === 'clear' && ( ( u.balance_owing ?? 0 ) > 0.01 || ( u.balance_credit ?? 0 ) > 0.01 )) return false;
            }

            return true;
        });

        // Sorting
        if (sortConfig.key) {
            result.sort((a, b) => {
                let aVal = a[ sortConfig.key ];
                let bVal = b[ sortConfig.key ];

                if (typeof aVal === 'string' && !isNaN(aVal) && aVal !== '') aVal = parseFloat(aVal);
                if (typeof bVal === 'string' && !isNaN(bVal) && bVal !== '') bVal = parseFloat(bVal);

                if (aVal < bVal) return sortConfig.direction === 'asc' ? -1 : 1;
                if (aVal > bVal) return sortConfig.direction === 'asc' ? 1 : -1;
                return 0;
            });
        }

        return result;
    }, [units, searchTerm, balanceFilter, columnFilters, sortConfig]);

    return (
        <div className="container max-w-7xl py-8 space-y-8" data-testid="owners-units-page">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Owner Details</h1>
                    <p className="text-muted-foreground mt-1">Master database of units, ownership structures, and
                        financial status.</p>
                </div>
                <div className="flex gap-2">
                    <Button
                        onClick={handleExportStrataRoll}
                        disabled={isExporting}
                        variant="outline"
                        className="shadow-sm bg-background/50 backdrop-blur-sm border-primary/20 hover:bg-primary/5"
                    >
                        {isExporting ? <><Loader2 className="mr-2 h-4 w-4 animate-spin"/>Generating...</> : <><Download
                            className="mr-2 h-4 w-4"/>Export Strata Roll</>}
                    </Button>
                    <Button
                        onClick={handleImportFromPDF}
                        disabled={isImporting}
                        variant="outline"
                        className="shadow-sm bg-background/50 backdrop-blur-sm border-primary/20 hover:bg-primary/5"
                    >
                        {isImporting ? <><Loader2 className="mr-2 h-4 w-4 animate-spin"/>Syncing...</> : <><Upload
                            className="mr-2 h-4 w-4"/>Import from PDF</>}
                    </Button>
                </div>
            </div>

            <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-4">
                <StatCard
                    title="Total Units"
                    value={units.length}
                    icon={<LayoutGrid className="h-4 w-4"/>}
                    onClick={() => setBalanceFilter('all')}
                    isActive={balanceFilter === 'all'}
                />
                <StatCard
                    title="In Arrears"
                    value={units.filter(u => ( u.balance_owing ?? 0 ) > 0.01).length}
                    icon={<X className="h-4 w-4 text-rose-500"/>}
                    onClick={() => setBalanceFilter('arrears')}
                    isActive={balanceFilter === 'arrears'}
                />
                <StatCard
                    title="Credit Balance"
                    value={units.filter(u => ( u.balance_credit ?? 0 ) > 0.01).length}
                    icon={<Check className="h-4 w-4 text-emerald-500"/>}
                    onClick={() => setBalanceFilter('credit')}
                    isActive={balanceFilter === 'credit'}
                />
                <StatCard title="Total Entitlement"
                          value={units.reduce((acc, u) => acc + ( u.unit_entitlement || 0 ), 0)}
                          icon={<ShieldCheck className="h-4 w-4"/>}/>
            </div>

            <Card className="border-none shadow-xl bg-card/60 backdrop-blur-md overflow-hidden">
                <CardHeader className="bg-muted/30 pb-6 border-b">
                    <div className="flex flex-col lg:flex-row gap-4 lg:items-center justify-between">
                        <div className="relative flex-1 max-w-md">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                id="units-search"
                                aria-label="Search units by name, unit number, lot, or asset type"
                                placeholder="Search by name, unit, lot, or asset..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-10 bg-background/50"
                            />
                        </div>
                        <div className="flex bg-muted/50 p-1 rounded-xl border border-border/50" role="group"
                             aria-label="Filter by balance status">
                            {['all', 'arrears', 'credit'].map(f => (
                                <Button
                                    key={f}
                                    variant={balanceFilter === f ? 'secondary' : 'ghost'}
                                    size="sm"
                                    onClick={() => setBalanceFilter(f)}
                                    aria-pressed={balanceFilter === f}
                                    className={`capitalize px-4 rounded-lg h-9 text-xs font-semibold ${balanceFilter === f ? 'shadow-sm bg-background' : ''}`}
                                >
                                    {f}
                                </Button>
                            ))}
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    {loading ? (
                        <div className="p-12 space-y-4" aria-label="Loading units" aria-busy="true">
                            {[1, 2, 3].map(i => <div key={i}
                                                     className="h-16 w-full bg-muted/20 animate-pulse rounded-lg"/>)}
                        </div>
                    ) : fetchError ? (
                        <div className="flex flex-col items-center justify-center gap-3 py-20 text-center" role="alert">
                            <AlertCircle className="h-10 w-10 text-rose-400"/>
                            <p className="text-sm font-semibold text-foreground">Failed to load unit records</p>
                            <p className="text-xs text-muted-foreground">Check your connection or try again.</p>
                            <Button variant="outline" size="sm" onClick={fetchUnits} className="mt-2">Retry</Button>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table aria-label="Units and owners">
                                <TableHeader className="bg-muted/5">
                                    <TableRow>
                                        {[
                                            {key: 'unit_number', label: 'Unit / Lot', className: 'py-4 pl-6'},
                                            {key: 'owner_name', label: 'Owner(s)', className: ''},
                                            {key: null, label: 'Asset Details', className: ''},
                                            {key: 'unit_entitlement', label: 'UOE', className: ''},
                                            {key: 'is_owner_occupied', label: 'Status', className: ''},
                                            {key: 'is_on_platform', label: 'On Platform', className: ''},
                                            {key: 'net_balance', label: 'Balance Status', className: ''},
                                        ].map(({key, label, className}) => key ? (
                                            <TableHead
                                                key={key}
                                                className={`cursor-pointer select-none ${className}`}
                                                tabIndex={0}
                                                aria-sort={sortConfig.key === key ? ( sortConfig.direction === 'asc' ? 'ascending' : 'descending' ) : 'none'}
                                                onClick={() => handleSort(key)}
                                                onKeyDown={(e) => ( e.key === 'Enter' || e.key === ' ' ) && handleSort(key)}
                                            >
                                                <div className="flex items-center gap-2">{label} <SortIcon
                                                    columnKey={key}/></div>
                                            </TableHead>
                                        ) : (
                                            <TableHead key={label} className={className}>{label}</TableHead>
                                        ))}
                                        <TableHead className="text-right pr-6">Management</TableHead>
                                    </TableRow>
                                    {/* Column filter row */}
                                    <TableRow className="bg-muted/5 border-b">
                                        <TableHead className="py-2 pl-6">
                                            <Input
                                                aria-label="Filter by unit or lot number"
                                                placeholder="Unit / Lot..."
                                                value={columnFilters.unit_lot}
                                                onChange={(e) => setFilter('unit_lot', e.target.value)}
                                                className="h-8 text-[10px] bg-background/50 border-muted"
                                            />
                                        </TableHead>
                                        <TableHead>
                                            <Input
                                                aria-label="Filter by owner name"
                                                placeholder="Owner..."
                                                value={columnFilters.owner}
                                                onChange={(e) => setFilter('owner', e.target.value)}
                                                className="h-8 text-[10px] bg-background/50 border-muted"
                                            />
                                        </TableHead>
                                        {/* Asset Details — three compact selects */}
                                        <TableHead>
                                            <div className="flex gap-1">
                                                <Select value={columnFilters.bedrooms || 'all'}
                                                        onValueChange={v => setFilter('bedrooms', v === 'all' ? '' : v)}>
                                                    <SelectTrigger
                                                        aria-label="Filter by bedrooms"
                                                        className="h-8 text-[10px] bg-background/50 border-muted w-[60px] px-1.5">
                                                        <BedDouble className="h-3 w-3 shrink-0"/>
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="all">All</SelectItem>
                                                        {bedroomOpts.map(v => <SelectItem key={v}
                                                                                          value={String(v)}>{v} BR</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                                <Select value={columnFilters.bathrooms || 'all'}
                                                        onValueChange={v => setFilter('bathrooms', v === 'all' ? '' : v)}>
                                                    <SelectTrigger
                                                        aria-label="Filter by bathrooms"
                                                        className="h-8 text-[10px] bg-background/50 border-muted w-[60px] px-1.5">
                                                        <Bath className="h-3 w-3 shrink-0"/>
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="all">All</SelectItem>
                                                        {bathroomOpts.map(v => <SelectItem key={v}
                                                                                           value={String(v)}>{v} BA</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                                <Select value={columnFilters.garage || 'all'}
                                                        onValueChange={v => setFilter('garage', v === 'all' ? '' : v)}>
                                                    <SelectTrigger
                                                        aria-label="Filter by garage spaces"
                                                        className="h-8 text-[10px] bg-background/50 border-muted w-[60px] px-1.5">
                                                        <Car className="h-3 w-3 shrink-0"/>
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="all">All</SelectItem>
                                                        {garageOpts.map(v => <SelectItem key={v}
                                                                                         value={String(v)}>{v}G</SelectItem>)}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                        </TableHead>
                                        {/* UOE select */}
                                        <TableHead>
                                            <Select value={columnFilters.uoe || 'all'}
                                                    onValueChange={v => setFilter('uoe', v === 'all' ? '' : v)}>
                                                <SelectTrigger
                                                    aria-label="Filter by unit entitlement"
                                                    className="h-8 text-[10px] bg-background/50 border-muted">
                                                    <SelectValue placeholder="All"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All</SelectItem>
                                                    {uoeOptions.map(v => <SelectItem key={v}
                                                                                     value={String(v)}>{v}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </TableHead>
                                        {/* Status select */}
                                        <TableHead>
                                            <Select value={columnFilters.status || 'all'}
                                                    onValueChange={v => setFilter('status', v === 'all' ? '' : v)}>
                                                <SelectTrigger
                                                    aria-label="Filter by occupancy status"
                                                    className="h-8 text-[10px] bg-background/50 border-muted">
                                                    <SelectValue placeholder="All"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All</SelectItem>
                                                    <SelectItem value="owner_occupied">Owner Occupied</SelectItem>
                                                    <SelectItem value="tenanted">Tenanted</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </TableHead>
                                        {/* On Platform select */}
                                        <TableHead>
                                            <Select value={columnFilters.platform || 'all'}
                                                    onValueChange={v => setFilter('platform', v === 'all' ? '' : v)}>
                                                <SelectTrigger
                                                    aria-label="Filter by platform status"
                                                    className="h-8 text-[10px] bg-background/50 border-muted">
                                                    <SelectValue placeholder="All"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All</SelectItem>
                                                    <SelectItem value="active">Active</SelectItem>
                                                    <SelectItem value="offline">Offline</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </TableHead>
                                        {/* Balance select */}
                                        <TableHead>
                                            <Select value={columnFilters.balance || 'all'}
                                                    onValueChange={v => setFilter('balance', v === 'all' ? '' : v)}>
                                                <SelectTrigger
                                                    aria-label="Filter by balance type"
                                                    className="h-8 text-[10px] bg-background/50 border-muted">
                                                    <SelectValue placeholder="All"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All</SelectItem>
                                                    <SelectItem value="arrears">In Arrears</SelectItem>
                                                    <SelectItem value="credit">Credit</SelectItem>
                                                    <SelectItem value="clear">Clear</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </TableHead>
                                        <TableHead className="text-right pr-6"></TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sortedAndFilteredUnits.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={8} className="py-20 text-center">
                                                <div className="flex flex-col items-center gap-2 text-muted-foreground">
                                                    <LayoutGrid className="h-8 w-8 opacity-30"/>
                                                    <p className="text-sm font-medium">No units match your filters</p>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="text-xs mt-1"
                                                        onClick={() => {
                                                            setSearchTerm('');
                                                            setBalanceFilter('all');
                                                            setColumnFilters({
                                                                unit_lot: '',
                                                                owner: '',
                                                                bedrooms: '',
                                                                bathrooms: '',
                                                                garage: '',
                                                                uoe: '',
                                                                status: '',
                                                                platform: '',
                                                                balance: ''
                                                            });
                                                        }}
                                                    >
                                                        Clear all filters
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    )}
                                    {sortedAndFilteredUnits.map((u) => (
                                        <TableRow key={u.id} className="group hover:bg-muted/30 transition-colors">
                                            <TableCell className="py-4 pl-6">
                                                <div className="flex items-center gap-2">
                                                    <Badge
                                                        variant="outline"
                                                        className="bg-primary/10 text-primary border-primary/20 font-bold text-xs tracking-tight px-2 py-0.5 w-auto whitespace-nowrap"
                                                    >
                                                        {u.unit_number}
                                                    </Badge>
                                                    <div className="flex flex-col leading-none">
                            <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-tighter">
                              {u.lot_number}
                            </span>
                                                        <span
                                                            className="text-[11px] text-muted-foreground/70 capitalize">
                              {u.unit_type || 'Residential'}
                            </span>
                                                    </div>
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex flex-col">
                                                    <span className="font-semibold text-sm">{u.owner_name}</span>
                                                    {u.owner_name_b && <span
                                                        className="text-[10px] text-muted-foreground">& {u.owner_name_b}</span>}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div
                                                    className="flex gap-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/80">
                                                    {u.bedrooms != null &&
                                                        <span className="flex items-center gap-1"><BedDouble
                                                            className="h-3 w-3"/>{u.bedrooms}BR</span>}
                                                    {u.bathrooms != null &&
                                                        <span className="flex items-center gap-1"><Bath
                                                            className="h-3 w-3"/>{u.bathrooms}BA</span>}
                                                    {u.garage_spaces != null &&
                                                        <span className="flex items-center gap-1"><Car
                                                            className="h-3 w-3"/>{u.garage_spaces}G</span>}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <span className="text-sm font-semibold">{u.unit_entitlement}</span>
                                            </TableCell>
                                            <TableCell>
                                                {u.is_owner_occupied ? (
                                                    <Badge variant="outline"
                                                           className="bg-emerald-50 text-emerald-700 border-emerald-200 text-[10px]">Owner
                                                        Occupied</Badge>
                                                ) : (
                                                    <Badge variant="outline"
                                                           className="bg-blue-50 text-blue-700 border-blue-200 text-[10px]">Tenanted</Badge>
                                                )}
                                            </TableCell>
                                            <TableCell>
                                                {u.is_on_platform ? (
                                                    <div
                                                        className="flex items-center gap-1.5 text-emerald-600 font-bold text-[10px] uppercase">
                                                        <UserCheck className="h-3.5 w-3.5"/>
                                                        Active
                                                    </div>
                                                ) : (
                                                    <div
                                                        className="flex items-center gap-1.5 text-muted-foreground/70 font-medium text-[10px] uppercase">
                                                        <UserX className="h-3.5 w-3.5"/>
                                                        Offline
                                                    </div>
                                                )}
                                            </TableCell>
                                            <TableCell>
                                                {( () => {
                                                    if (( u.balance_credit ?? 0 ) > 0.01) return <Badge
                                                        className="bg-emerald-100 text-emerald-700 border-none text-[10px]">Credit</Badge>;
                                                    if (( u.balance_owing ?? 0 ) > 0.01) return <Badge
                                                        className="bg-rose-100 text-rose-700 border-none text-[10px]">Arrears</Badge>;
                                                    return <Badge
                                                        className="bg-slate-100 text-slate-600 border-none text-[10px]">Paid
                                                        Up</Badge>;
                                                } )()}
                                            </TableCell>
                                            <TableCell className="text-right pr-6">
                                                <div className="flex gap-2 justify-end">
                                                    <Button
                                                        size="sm"
                                                        variant="ghost"
                                                        className="h-8 px-2 text-xs font-bold hover:bg-indigo-50 text-indigo-600"
                                                        aria-label={`View financial details for unit ${u.unit_number}`}
                                                        onClick={() => router.push(`/financials/unit/${u.unit_number}`)}
                                                    >
                                                        Details
                                                    </Button>
                                                    <Button size="icon" variant="ghost"
                                                            className="h-8 w-8 hover:bg-primary/5 hover:text-primary"
                                                            onClick={() => handleEdit(u)}
                                                            aria-label={`Edit unit ${u.unit_number}`}>
                                                        <Edit className="h-4 w-4"/>
                                                    </Button>
                                                    <Button size="icon" variant="ghost"
                                                            className="h-8 w-8 text-rose-500 hover:bg-rose-50"
                                                            onClick={() => {
                                                                setUnitToDelete(u);
                                                                setIsDeleteDialogOpen(true);
                                                            }}
                                                            aria-label={`Delete unit ${u.unit_number}`}>
                                                        <Trash2 className="h-4 w-4"/>
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Edit Dialog */}
            <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
                <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto border-none shadow-2xl">
                    <DialogHeader className="border-b pb-4 mb-6">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-primary/10 rounded-lg"><Building className="h-5 w-5 text-primary"/>
                            </div>
                            <div>
                                <DialogTitle>Edit Unit Details: Unit {selectedUnit?.unit_number}</DialogTitle>
                                <DialogDescription>Update unit metadata, occupancy, and notes. Ownership identity is
                                    managed through the ownership transfer workflow.</DialogDescription>
                            </div>
                        </div>
                    </DialogHeader>

                    <div className="grid gap-8 py-2 md:grid-cols-2">
                        <div className="space-y-6">
                            <SectionTitle icon={<UsersIcon className="h-4 w-4"/>} title="Ownership Overview"/>
                            <div className="grid gap-4">
                                <div className="rounded-xl border bg-muted/20 p-4 space-y-3">
                                    <div>
                                        <Label className="text-xs uppercase font-bold text-muted-foreground">Canonical
                                            owner record</Label>
                                        <p className="mt-1 text-sm font-semibold text-foreground">
                                            {[formData.owner_name, formData.owner_name_b].filter(Boolean).join(' & ') || 'No owner linked'}
                                        </p>
                                        {( formData.owner_email || formData.owner_email_b ) && (
                                            <p className="text-xs text-muted-foreground mt-1">
                                                {[formData.owner_email, formData.owner_email_b].filter(Boolean).join(' · ')}
                                            </p>
                                        )}
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        Primary owner changes now flow through ownership transfers so the API stays in
                                        sync with
                                        <code className="mx-1">user_units</code>
                                        and
                                        <code className="mx-1">users</code>.
                                    </p>
                                    {hasFeatureAccess?.('owner_transfers') && isManager() && (
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            onClick={() => {
                                                setIsEditDialogOpen(false);
                                                router.push('/admin/owner-transfers');
                                            }}
                                        >
                                            Open Ownership Transfers
                                        </Button>
                                    )}
                                </div>
                                <div
                                    className="flex items-center space-x-3 bg-muted/30 p-3 rounded-lg border border-dashed">
                                    <Checkbox checked={formData.is_owner_occupied}
                                              onCheckedChange={(c) => setFormData({...formData, is_owner_occupied: c})}
                                              id="occ"/>
                                    <Label htmlFor="occ" className="text-sm font-semibold cursor-pointer">Owner
                                        Occupied</Label>
                                </div>
                            </div>
                        </div>

                        <div className="space-y-6">
                            <SectionTitle icon={<LayoutGrid className="h-4 w-4"/>} title="Unit Configuration"/>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="grid gap-2">
                                    <Label
                                        className="text-xs uppercase font-bold text-muted-foreground">Bedrooms</Label>
                                    <Input type="number" value={formData.bedrooms}
                                           onChange={(e) => setFormData({...formData, bedrooms: e.target.value})}/>
                                </div>
                                <div className="grid gap-2">
                                    <Label
                                        className="text-xs uppercase font-bold text-muted-foreground">Bathrooms</Label>
                                    <Input type="number" value={formData.bathrooms}
                                           onChange={(e) => setFormData({...formData, bathrooms: e.target.value})}/>
                                </div>
                                <div className="grid gap-2">
                                    <Label className="text-xs uppercase font-bold text-muted-foreground">Car
                                        Spaces</Label>
                                    <Input type="number" value={formData.garage_spaces}
                                           onChange={(e) => setFormData({...formData, garage_spaces: e.target.value})}/>
                                </div>
                                <div className="grid gap-2">
                                    <Label className="text-xs uppercase font-bold text-muted-foreground">Entitlement
                                        (UOE)</Label>
                                    <Input type="number" value={formData.unit_entitlement}
                                           onChange={(e) => setFormData({
                                               ...formData,
                                               unit_entitlement: e.target.value
                                           })}/>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="mt-8 space-y-3">
                        <Label className="text-xs uppercase font-bold text-muted-foreground">Administrative
                            Notes</Label>
                        <Textarea value={formData.notes}
                                  onChange={(e) => setFormData({...formData, notes: e.target.value})} rows={3}
                                  className="bg-muted/20"/>
                    </div>

                    <DialogFooter className="mt-8 border-t pt-6">
                        <Button variant="ghost" onClick={() => setIsEditDialogOpen(false)}>Discard</Button>
                        <Button onClick={handleSave} className="shadow-md min-w-[120px]">Commit Changes</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Delete Dialog */}
            <AlertDialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
                <AlertDialogContent className="border-none shadow-2xl">
                    <AlertDialogHeader>
                        <AlertDialogTitle>Purge Asset Record?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Permanently delete Unit <strong>{unitToDelete?.unit_number}</strong>. This will also remove
                            historical balance data for this unit.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleDeleteConfirm} className="bg-rose-600 hover:bg-rose-700">Confirm
                            Deletion</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: StatCard
 * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatCard = ({title, value, icon, onClick, isActive}) => (
    <Card
        className={`border-none shadow-sm transition-all duration-200 ${onClick ? 'cursor-pointer hover:shadow-md hover:translate-y-[-2px]' : ''} ${isActive ? 'bg-primary/10 ring-1 ring-primary/20' : 'bg-card/40 backdrop-blur-sm'}`}
        onClick={onClick}
        role={onClick ? 'button' : undefined}
        tabIndex={onClick ? 0 : undefined}
        aria-pressed={onClick ? isActive : undefined}
        aria-label={onClick ? `Filter by ${title}` : undefined}
        onKeyDown={onClick ? (e) => ( e.key === 'Enter' || e.key === ' ' ) && onClick() : undefined}
    >
        <CardContent className="p-6">
            <div className="flex items-center justify-between space-y-0 pb-2">
                <p className={`text-xs font-bold uppercase tracking-wider ${isActive ? 'text-primary' : 'text-muted-foreground'}`}>{title}</p>
                <div
                    className={`p-1.5 rounded-md ${isActive ? 'bg-primary/20 text-primary' : 'bg-muted/50 text-muted-foreground'}`}>{icon}</div>
            </div>
            <div className={`text-2xl font-bold ${isActive ? 'text-primary' : ''}`}>{value}</div>
        </CardContent>
    </Card>
);
/**
 * @generated FunctionHeader
 * Function: SectionTitle
 * Path: frontend/src/pages/dashboard/OwnersUnitsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SectionTitle = ({icon, title}) => (
    <div className="flex items-center gap-2 border-b pb-2">
        <div className="text-primary">{icon}</div>
        <h4 className="text-sm font-bold uppercase tracking-widest">{title}</h4>
    </div>
);


export default OwnersUnitsPage;
