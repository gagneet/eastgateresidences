import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import {
    Camera,
    DoorOpen,
    Edit2,
    Eye,
    EyeOff,
    Info,
    Key,
    Loader2,
    Lock,
    Plus,
    RotateCcw,
    Search,
    Settings,
    Shield,
    Thermometer,
    Trash2,
    Zap
} from 'lucide-react';
import { toast } from 'sonner';
import { useApiData } from '../../hooks/useApiData';
/**
 * @generated FunctionHeader
 * Function: AssetRegisterPage
 * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AssetRegisterPage = () => {
    const {api, user} = useAuth();
    const [activeTab, setActiveTab] = useState('assets');
    const [searchTerm, setSearchTerm] = useState('');
    const [selectedCategory, setSelectedCategory] = useState('all');
    const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
    const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
    const [currentAsset, setCurrentAsset] = useState(null);
    const [showSensitive, setShowSensitive] = useState({});

    // Keys & Fobs state
    const [keyFobs, setKeyFobs] = useState([]);
    const [keyFobsLoading, setKeyFobsLoading] = useState(false);
    const [kfSearch, setKfSearch] = useState('');
    const [kfFilter, setKfFilter] = useState('all');
    const [isKeyFobDialogOpen, setIsKeyFobDialogOpen] = useState(false);
    const [editingFob, setEditingFob] = useState(null);
    const [kfProcessing, setKfProcessing] = useState(false);

    const MANAGER_ROLES = ['super_admin', 'ec_member', 'strata_manager'];
    const isManager = MANAGER_ROLES.includes(user?.role);

    const fetchKeyFobs = useCallback(async () => {
        setKeyFobsLoading(true);
        try {
            const res = await api.get('/building/keys-fobs');
            setKeyFobs(res.data || []);
        } catch {
            setKeyFobs([]);
        } finally {
            setKeyFobsLoading(false);
        }
    }, [api]);

    useEffect(() => {
        if (activeTab === 'keyfobs') fetchKeyFobs();
    }, [activeTab, fetchKeyFobs]);
    /**
     * @generated FunctionHeader
     * Function: handleIssueKeyFob
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleIssueKeyFob = async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        setKfProcessing(true);
        try {
            const data = {
                unit_number: fd.get('unit_number'),
                key_type: fd.get('key_type'),
                serial_number: fd.get('serial_number') || null,
                issued_to_name: fd.get('issued_to_name'),
                issued_date: fd.get('issued_date') || null,
                notes: fd.get('notes') || null,
            };
            if (editingFob) {
                await api.put(`/building/keys-fobs/${editingFob.id}`, data);
                toast.success('Key/Fob record updated');
            } else {
                await api.post('/building/keys-fobs', data);
                toast.success('Key/Fob issued successfully');
            }
            setIsKeyFobDialogOpen(false);
            setEditingFob(null);
            fetchKeyFobs();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to save');
        } finally {
            setKfProcessing(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleUpdateFobStatus
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateFobStatus = async (fob, newStatus) => {
        setKfProcessing(true);
        try {
            const update = {status: newStatus};
            if (newStatus === 'returned') {
                update.returned_date = new Date().toISOString().slice(0, 10);
            }
            await api.put(`/building/keys-fobs/${fob.id}`, update);
            toast.success(`Marked as ${newStatus}`);
            fetchKeyFobs();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to update');
        } finally {
            setKfProcessing(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDeleteFob
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteFob = async (fob) => {
        if (!window.confirm(`Delete record for ${fob.key_type.replace(/_/g, ' ')} — ${fob.unit_number}?`)) return;
        try {
            await api.delete(`/building/keys-fobs/${fob.id}`);
            toast.success('Record deleted');
            fetchKeyFobs();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to delete');
        }
    };

    const filteredKeyFobs = keyFobs.filter(f => {
        const matchSearch = !kfSearch || f.unit_number?.toLowerCase().includes(kfSearch.toLowerCase()) || f.issued_to_name?.toLowerCase().includes(kfSearch.toLowerCase()) || f.serial_number?.toLowerCase().includes(kfSearch.toLowerCase());
        const matchStatus = kfFilter === 'all' || f.status === kfFilter;
        return matchSearch && matchStatus;
    });
    /**
     * @generated FunctionHeader
     * Function: keyTypeLabel
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const keyTypeLabel = t => ( {
        entry_key: 'Entry Key', garage_fob: 'Garage Fob', letterbox_key: 'Letterbox Key',
        common_area_fob: 'Common Area Fob', master_key: 'Master Key', other: 'Other'
    } )[ t ] || t;
    /**
     * @generated FunctionHeader
     * Function: fobStatusColor
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fobStatusColor = s => ( {
        active: 'bg-green-100 text-green-800', returned: 'bg-gray-100 text-gray-700',
        lost: 'bg-red-100 text-red-800', replacement: 'bg-amber-100 text-amber-800'
    } )[ s ] || 'bg-gray-100 text-gray-700';

    const {
        data: assets,
        loading,
        refetch
    } = useApiData(
        useCallback(() => api.get('/building/assets'), [api])
    );

    const categories = [
        {id: 'all', label: 'All Assets', icon: Info},
        {id: 'Keys & Locks', label: 'Keys & Locks', icon: Key},
        {id: 'Utilities', label: 'Utilities', icon: Zap},
        {id: 'HVAC', label: 'HVAC', icon: Thermometer},
        {id: 'Fire Safety', label: 'Fire Safety', icon: Shield},
        {id: 'Security', label: 'Security', icon: Camera},
        {id: 'Door System', label: 'Door System', icon: DoorOpen},
        {id: 'General', label: 'General', icon: Settings},
    ];

    const filteredAssets = assets?.filter(asset => {
        const matchesSearch = asset.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
            asset.description?.toLowerCase().includes(searchTerm.toLowerCase());
        const matchesCategory = selectedCategory === 'all' || asset.category === selectedCategory;
        return matchesSearch && matchesCategory;
    }) || [];
    /**
     * @generated FunctionHeader
     * Function: handleAddAsset
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddAsset = async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const data = {
            name: formData.get('name'),
            category: formData.get('category'),
            description: formData.get('description'),
            location: formData.get('location'),
            serial_number: formData.get('serial_number'),
            details: {},
            sensitive_data: {}
        };

        // Add specific details if provided
        const meterNum = formData.get('meter_number');
        if (meterNum) data.details.meter_number = meterNum;

        const lockCode = formData.get('lock_code');
        if (lockCode) data.sensitive_data.lock_code = lockCode;

        const keyNum = formData.get('key_number');
        if (keyNum) data.sensitive_data.key_number = keyNum;

        try {
            await api.post('/building/assets', data);
            toast.success('Asset added successfully');
            setIsAddDialogOpen(false);
            refetch();
        } catch (error) {
            toast.error('Failed to add asset');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleUpdateAsset
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateAsset = async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const data = {
            name: formData.get('name'),
            category: formData.get('category'),
            description: formData.get('description'),
            location: formData.get('location'),
            serial_number: formData.get('serial_number'),
            details: {...currentAsset.details},
            sensitive_data: {...currentAsset.sensitive_data}
        };

        const meterNum = formData.get('meter_number');
        if (meterNum) data.details.meter_number = meterNum;

        const lockCode = formData.get('lock_code');
        if (lockCode) data.sensitive_data.lock_code = lockCode;

        const keyNum = formData.get('key_number');
        if (keyNum) data.sensitive_data.key_number = keyNum;

        try {
            await api.put(`/building/assets/${currentAsset.id}`, data);
            toast.success('Asset updated successfully');
            setIsEditDialogOpen(false);
            refetch();
        } catch (error) {
            toast.error('Failed to update asset');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDeleteAsset
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteAsset = async (id) => {
        if (window.confirm('Are you sure you want to delete this asset?')) {
            try {
                await api.delete(`/building/assets/${id}`);
                toast.success('Asset deleted');
                refetch();
            } catch (error) {
                toast.error('Failed to delete asset');
            }
        }
    };
    /**
     * @generated FunctionHeader
     * Function: toggleSensitive
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleSensitive = (id) => {
        setShowSensitive(prev => ( {...prev, [ id ]: !prev[ id ]} ));
    };
    /**
     * @generated FunctionHeader
     * Function: getCategoryIcon
     * Path: frontend/src/pages/dashboard/AssetRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getCategoryIcon = (catName) => {
        const cat = categories.find(c => c.id === catName);
        const Icon = cat?.icon || Settings;
        return <Icon className="h-4 w-4"/>;
    };

    return (
        <div className="space-y-6">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Asset Register</h1>
                    <p className="text-muted-foreground">Manage building assets, keys, fobs, and master codes</p>
                </div>
                {activeTab === 'assets' && (
                    <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
                        <DialogTrigger asChild>
                            <Button>
                                <Plus className="mr-2 h-4 w-4"/>
                                Add Asset
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-md">
                            <DialogHeader>
                                <DialogTitle>Add Building Asset</DialogTitle>
                                <DialogDescription>Record a new asset or important building code</DialogDescription>
                            </DialogHeader>
                            <form onSubmit={handleAddAsset} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="name">Asset Name</Label>
                                    <Input id="name" name="name" placeholder="e.g., Main Gas Meter" required/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="category">Category</Label>
                                    <Select name="category" defaultValue="General">
                                        <SelectTrigger>
                                            <SelectValue placeholder="Select category"/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {categories.filter(c => c.id !== 'all').map(c => (
                                                <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="location">Location</Label>
                                    <Input id="location" name="location" placeholder="e.g., Basement B1, North Wall"/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="description">Description</Label>
                                    <Textarea id="description" name="description" placeholder="Additional details..."/>
                                </div>
                                <div className="grid grid-cols-2 gap-4 border-t pt-4 mt-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="meter_number">Meter/Serial Number</Label>
                                        <Input id="meter_number" name="meter_number" placeholder="Optional"/>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="lock_code" className="flex items-center gap-1 text-red-600">
                                            <Lock className="h-3 w-3"/> Lock/Code
                                        </Label>
                                        <Input id="lock_code" name="lock_code" type="password" placeholder="Sensitive"/>
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="key_number" className="flex items-center gap-1 text-red-600">
                                        <Key className="h-3 w-3"/> Master Key Number
                                    </Label>
                                    <Input id="key_number" name="key_number" placeholder="e.g., MK-101"/>
                                </div>
                                <DialogFooter>
                                    <Button type="submit" className="w-full">Save Asset</Button>
                                </DialogFooter>
                            </form>
                        </DialogContent>
                    </Dialog>
                )}
                {activeTab === 'keyfobs' && isManager && (
                    <Button onClick={() => {
                        setEditingFob(null);
                        setIsKeyFobDialogOpen(true);
                    }}>
                        <Plus className="mr-2 h-4 w-4"/> Issue Key/Fob
                    </Button>
                )}
            </div>

            {/* Tab switcher */}
            <div className="flex gap-1 border-b" role="tablist">
                <button
                    role="tab"
                    aria-selected={activeTab === 'assets'}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'assets' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
                    onClick={() => setActiveTab('assets')}
                >
                    <Settings className="inline h-4 w-4 mr-1.5"/>Asset Register
                </button>
                <button
                    role="tab"
                    aria-selected={activeTab === 'keyfobs'}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'keyfobs' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
                    onClick={() => setActiveTab('keyfobs')}
                >
                    <Key className="inline h-4 w-4 mr-1.5"/>Keys &amp; Fobs
                </button>
            </div>

            {/* ───── ASSET REGISTER TAB ───── */}
            {activeTab === 'assets' && ( <>
                <div className="flex flex-col md:flex-row gap-4">
                    <div className="relative flex-1">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                        <Input
                            placeholder="Search assets..."
                            className="pl-10"
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                        />
                    </div>
                    <div className="flex gap-2 overflow-x-auto pb-2 md:pb-0">
                        {categories.map((cat) => (
                            <Button
                                key={cat.id}
                                variant={selectedCategory === cat.id ? 'default' : 'outline'}
                                size="sm"
                                onClick={() => setSelectedCategory(cat.id)}
                                className="whitespace-nowrap"
                            >
                                <cat.icon className="mr-2 h-4 w-4"/>
                                {cat.label}
                            </Button>
                        ))}
                    </div>
                </div>

                {loading ? (
                    <div className="flex justify-center py-12">
                        <Loader2 className="h-8 w-8 animate-spin text-primary"/>
                    </div>
                ) : filteredAssets.length === 0 ? (
                    <Card>
                        <CardContent className="py-12 text-center">
                            <Settings className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4"/>
                            <h3 className="text-lg font-medium">No assets found</h3>
                            <p className="text-muted-foreground">Try adjusting your search or filters</p>
                        </CardContent>
                    </Card>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {filteredAssets.map((asset) => (
                            <Card key={asset.id} className="overflow-hidden border-l-4 border-l-primary/50">
                                <CardHeader className="pb-3">
                                    <div className="flex items-start justify-between">
                                        <div className="flex items-center gap-2">
                                            <div className="p-2 rounded-md bg-primary/10 text-primary">
                                                {getCategoryIcon(asset.category)}
                                            </div>
                                            <div>
                                                <CardTitle className="text-base">{asset.name}</CardTitle>
                                                <Badge variant="outline"
                                                       className="text-[10px] uppercase font-bold mt-1">
                                                    {asset.category}
                                                </Badge>
                                            </div>
                                        </div>
                                        <div className="flex gap-1">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8"
                                                onClick={() => {
                                                    setCurrentAsset(asset);
                                                    setIsEditDialogOpen(true);
                                                }}
                                            >
                                                <Edit2 className="h-3.5 w-3.5"/>
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-destructive"
                                                onClick={() => handleDeleteAsset(asset.id)}
                                            >
                                                <Trash2 className="h-3.5 w-3.5"/>
                                            </Button>
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-4">
                                    <div className="space-y-2 text-sm">
                                        <div className="flex justify-between">
                                            <span className="text-muted-foreground">Location:</span>
                                            <span className="font-medium">{asset.location || 'Not specified'}</span>
                                        </div>
                                        {asset.details?.meter_number && (
                                            <div className="flex justify-between">
                                                <span className="text-muted-foreground">Meter/Serial:</span>
                                                <span
                                                    className="font-mono font-medium">{asset.details.meter_number}</span>
                                            </div>
                                        )}
                                        {asset.description && (
                                            <p className="text-xs text-muted-foreground mt-2 line-clamp-2 italic">
                                                "{asset.description}"
                                            </p>
                                        )}
                                    </div>

                                    {( asset.sensitive_data?.lock_code || asset.sensitive_data?.key_number ) && (
                                        <div
                                            className="bg-red-50 dark:bg-red-950/20 p-3 rounded-md border border-red-100 dark:border-red-900/30">
                                            <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-bold text-red-600 flex items-center gap-1 uppercase">
                        <Lock className="h-3 w-3"/> Sensitive Data
                      </span>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="h-6 px-2 text-[10px] text-red-600 hover:bg-red-100"
                                                    onClick={() => toggleSensitive(asset.id)}
                                                >
                                                    {showSensitive[ asset.id ] ? <EyeOff className="h-3 w-3 mr-1"/> :
                                                        <Eye className="h-3 w-3 mr-1"/>}
                                                    {showSensitive[ asset.id ] ? 'Hide' : 'Show'}
                                                </Button>
                                            </div>
                                            <div className="space-y-1.5 font-mono text-sm">
                                                {asset.sensitive_data.key_number && (
                                                    <div className="flex justify-between items-center">
                                                    <span
                                                        className="text-[10px] text-red-800/60 uppercase">Key #:</span>
                                                        <span className="font-bold text-red-700">
                            {showSensitive[ asset.id ] ? asset.sensitive_data.key_number : '••••••••'}
                          </span>
                                                    </div>
                                                )}
                                                {asset.sensitive_data.lock_code && (
                                                    <div className="flex justify-between items-center">
                                                        <span
                                                            className="text-[10px] text-red-800/60 uppercase">Code:</span>
                                                        <span className="font-bold text-red-700">
                            {showSensitive[ asset.id ] ? asset.sensitive_data.lock_code : '••••••••'}
                          </span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                )}
            </> )}

            {/* ───── KEYS & FOBS TAB ───── */}
            {activeTab === 'keyfobs' && (
                <div className="space-y-4">
                    <div className="rounded-xl border border-amber-100 bg-amber-50 p-4">
                        <div className="flex items-start gap-3">
                            <Key className="h-5 w-5 text-amber-500 shrink-0 mt-0.5"/>
                            <div className="text-sm text-amber-800 space-y-1">
                                <p className="font-semibold">Keys &amp; Fob Management</p>
                                <p>Track all entry keys, garage fobs, letterbox keys, and common area fobs issued to
                                    each unit. Record when they are returned or reported lost. Each unit may hold
                                    multiple fobs — all are tracked here with an audit trail.</p>
                                <p className="text-xs text-amber-600">Garage fobs and entry fobs must be returned upon
                                    unit ownership transfer. Lost fobs should be marked accordingly so deactivation can
                                    be arranged.</p>
                            </div>
                        </div>
                    </div>

                    <div className="flex flex-col sm:flex-row gap-3">
                        <div className="relative flex-1">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input placeholder="Search by unit, name, serial…" className="pl-10" value={kfSearch}
                                   onChange={e => setKfSearch(e.target.value)}/>
                        </div>
                        <div className="flex gap-2">
                            {['all', 'active', 'returned', 'lost'].map(s => (
                                <button key={s} onClick={() => setKfFilter(s)}
                                        className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${kfFilter === s ? 'bg-primary text-primary-foreground border-primary' : 'border-slate-200 text-slate-600 hover:border-primary/40'}`}>
                                    {s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
                                </button>
                            ))}
                        </div>
                    </div>

                    {keyFobsLoading ? (
                        <div className="flex justify-center py-10"><Loader2
                            className="h-7 w-7 animate-spin text-primary"/></div>
                    ) : filteredKeyFobs.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center">
                                <Key className="h-10 w-10 text-muted-foreground/30 mx-auto mb-3"/>
                                <p className="font-medium">No key/fob records found</p>
                                <p className="text-sm text-muted-foreground mt-1">{isManager ? 'Use "Issue Key/Fob" above to add the first record.' : 'No keys or fobs registered for your unit.'}</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="overflow-x-auto rounded-xl border">
                            <table className="w-full text-sm">
                                <thead className="bg-muted/50 text-muted-foreground text-xs uppercase">
                                <tr>
                                    <th className="px-4 py-3 text-left">Unit</th>
                                    <th className="px-4 py-3 text-left">Type</th>
                                    <th className="px-4 py-3 text-left">Serial #</th>
                                    <th className="px-4 py-3 text-left">Issued To</th>
                                    <th className="px-4 py-3 text-left">Issued</th>
                                    <th className="px-4 py-3 text-left">Returned</th>
                                    <th className="px-4 py-3 text-left">Status</th>
                                    {isManager && <th className="px-4 py-3 text-right">Actions</th>}
                                </tr>
                                </thead>
                                <tbody className="divide-y">
                                {filteredKeyFobs.map(fob => (
                                    <tr key={fob.id} className="hover:bg-muted/20 transition-colors">
                                        <td className="px-4 py-3 font-mono font-semibold">{fob.unit_number}</td>
                                        <td className="px-4 py-3">{keyTypeLabel(fob.key_type)}</td>
                                        <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{fob.serial_number || '—'}</td>
                                        <td className="px-4 py-3">{fob.issued_to_name}</td>
                                        <td className="px-4 py-3 text-xs text-muted-foreground">{fob.issued_date || '—'}</td>
                                        <td className="px-4 py-3 text-xs text-muted-foreground">{fob.returned_date || '—'}</td>
                                        <td className="px-4 py-3">
                                            <Badge className={`text-[10px] ${fobStatusColor(fob.status)}`}>
                                                {fob.status?.charAt(0).toUpperCase() + fob.status?.slice(1)}
                                            </Badge>
                                        </td>
                                        {isManager && (
                                            <td className="px-4 py-3 text-right">
                                                <div className="flex items-center justify-end gap-1">
                                                    {fob.status === 'active' && (
                                                        <Button variant="ghost" size="sm" className="h-7 text-xs"
                                                                onClick={() => handleUpdateFobStatus(fob, 'returned')}
                                                                disabled={kfProcessing}>
                                                            <RotateCcw className="h-3 w-3 mr-1"/>Return
                                                        </Button>
                                                    )}
                                                    {fob.status === 'active' && (
                                                        <Button variant="ghost" size="sm"
                                                                className="h-7 text-xs text-red-600 hover:text-red-700"
                                                                onClick={() => handleUpdateFobStatus(fob, 'lost')}
                                                                disabled={kfProcessing}>
                                                            Lost
                                                        </Button>
                                                    )}
                                                    <Button variant="ghost" size="icon" className="h-7 w-7"
                                                            onClick={() => {
                                                                setEditingFob(fob);
                                                                setIsKeyFobDialogOpen(true);
                                                            }}>
                                                        <Edit2 className="h-3.5 w-3.5"/>
                                                    </Button>
                                                    <Button variant="ghost" size="icon"
                                                            className="h-7 w-7 text-destructive"
                                                            onClick={() => handleDeleteFob(fob)}>
                                                        <Trash2 className="h-3.5 w-3.5"/>
                                                    </Button>
                                                </div>
                                            </td>
                                        )}
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* Keys & Fobs Issue/Edit Dialog */}
            <Dialog open={isKeyFobDialogOpen} onOpenChange={v => {
                setIsKeyFobDialogOpen(v);
                if (!v) setEditingFob(null);
            }}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>{editingFob ? 'Edit Key/Fob Record' : 'Issue Key or Fob'}</DialogTitle>
                        <DialogDescription>{editingFob ? 'Update details for this key or fob.' : 'Register a new key or fob issued to a unit.'}</DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleIssueKeyFob} className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="kf_unit">Unit Number *</Label>
                                <Input id="kf_unit" name="unit_number" placeholder="e.g. UA042 or TH071"
                                       defaultValue={editingFob?.unit_number} required/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="kf_type">Key/Fob Type *</Label>
                                <Select name="key_type" defaultValue={editingFob?.key_type || 'entry_key'}>
                                    <SelectTrigger id="kf_type"><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="entry_key">Entry Key</SelectItem>
                                        <SelectItem value="garage_fob">Garage Fob</SelectItem>
                                        <SelectItem value="letterbox_key">Letterbox Key</SelectItem>
                                        <SelectItem value="common_area_fob">Common Area Fob</SelectItem>
                                        <SelectItem value="master_key">Master Key</SelectItem>
                                        <SelectItem value="other">Other</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="kf_serial">Serial / Tag # </Label>
                                <Input id="kf_serial" name="serial_number" placeholder="Optional"
                                       defaultValue={editingFob?.serial_number}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="kf_issued_date">Date Issued</Label>
                                <Input id="kf_issued_date" name="issued_date" type="date"
                                       defaultValue={editingFob?.issued_date || new Date().toISOString().slice(0, 10)}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="kf_name">Issued To (Name) *</Label>
                            <Input id="kf_name" name="issued_to_name" placeholder="Owner / tenant full name"
                                   defaultValue={editingFob?.issued_to_name} required/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="kf_notes">Notes</Label>
                            <Textarea id="kf_notes" name="notes" placeholder="Any relevant notes…" rows={2}
                                      defaultValue={editingFob?.notes}/>
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => {
                                setIsKeyFobDialogOpen(false);
                                setEditingFob(null);
                            }}>Cancel</Button>
                            <Button type="submit" disabled={kfProcessing}>
                                {kfProcessing && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                                {editingFob ? 'Update' : 'Issue Key/Fob'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            {/* Edit Asset Dialog */}
            <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Edit Asset</DialogTitle>
                    </DialogHeader>
                    {currentAsset && (
                        <form onSubmit={handleUpdateAsset} className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="edit_name">Asset Name</Label>
                                <Input id="edit_name" name="name" defaultValue={currentAsset.name} required/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="edit_category">Category</Label>
                                <Select name="category" defaultValue={currentAsset.category}>
                                    <SelectTrigger>
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {categories.filter(c => c.id !== 'all').map(c => (
                                            <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="edit_location">Location</Label>
                                <Input id="edit_location" name="location" defaultValue={currentAsset.location}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="edit_description">Description</Label>
                                <Textarea id="edit_description" name="description"
                                          defaultValue={currentAsset.description}/>
                            </div>
                            <div className="grid grid-cols-2 gap-4 border-t pt-4 mt-4">
                                <div className="space-y-2">
                                    <Label htmlFor="edit_meter_number">Meter/Serial Number</Label>
                                    <Input id="edit_meter_number" name="meter_number"
                                           defaultValue={currentAsset.details?.meter_number}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="edit_lock_code" className="text-red-600">Lock/Code</Label>
                                    <Input id="edit_lock_code" name="lock_code" type="password"
                                           placeholder="Change or leave blank"/>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="edit_key_number" className="text-red-600">Master Key Number</Label>
                                <Input id="edit_key_number" name="key_number"
                                       defaultValue={currentAsset.sensitive_data?.key_number}/>
                            </div>
                            <DialogFooter>
                                <Button type="submit" className="w-full">Update Asset</Button>
                            </DialogFooter>
                        </form>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default AssetRegisterPage;
