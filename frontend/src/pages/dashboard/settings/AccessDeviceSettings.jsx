import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {Badge} from '../../../components/ui/badge';
import {Button} from '../../../components/ui/button';
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '../../../components/ui/card';
import {Input} from '../../../components/ui/input';
import {Label} from '../../../components/ui/label';
import {Switch} from '../../../components/ui/switch';
import {Textarea} from '../../../components/ui/textarea';
import {Archive, KeyRound, Loader2, Plus, Save} from 'lucide-react';
import {toast} from 'sonner';

const blankDeviceType = {
    name: '',
    type_key: '',
    description: '',
    request_available: true,
    fee_cents: 0,
    deposit_cents: 0,
    replacement_lost_fee_cents: 0,
    max_quantity: 1,
    requires_approval: true,
    requires_return: true,
    effective_date: '',
    is_active: true,
};

const dollarsToCents = (value) => Math.max(0, Math.round((parseFloat(value || '0') || 0) * 100));
const centsToDollars = (value) => ((parseInt(value || 0, 10) || 0) / 100).toFixed(2);

const normaliseForForm = (deviceType) => ({
    ...blankDeviceType,
    ...deviceType,
    effective_date: deviceType.effective_date || '',
});

const toPayload = (form) => ({
    type_key: form.type_key || undefined,
    name: form.name,
    description: form.description ?? '',
    request_available: Boolean(form.request_available),
    fee_cents: parseInt(form.fee_cents || 0, 10) || 0,
    deposit_cents: parseInt(form.deposit_cents || 0, 10) || 0,
    replacement_lost_fee_cents: parseInt(form.replacement_lost_fee_cents || 0, 10) || 0,
    max_quantity: parseInt(form.max_quantity || 1, 10) || 1,
    requires_approval: Boolean(form.requires_approval),
    requires_return: Boolean(form.requires_return),
    effective_date: form.effective_date || null,
    is_active: Boolean(form.is_active),
});

const AccessDeviceSettings = ({api}) => {
    const [deviceTypes, setDeviceTypes] = useState([]);
    const [loading, setLoading] = useState(true);
    const [savingId, setSavingId] = useState(null);
    const [newDevice, setNewDevice] = useState(blankDeviceType);
    const [drafts, setDrafts] = useState({});

    const fetchDeviceTypes = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.get('/access/device-types?include_inactive=true');
            const rows = Array.isArray(res.data) ? res.data : [];
            setDeviceTypes(rows);
            setDrafts(Object.fromEntries(rows.map(row => [row.device_type_id, normaliseForForm(row)])));
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || 'Failed to load access device settings');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchDeviceTypes();
    }, [fetchDeviceTypes]);

    const activeCount = useMemo(() => deviceTypes.filter(type => type.is_active).length, [deviceTypes]);

    const updateDraft = (id, field, value) => {
        setDrafts(prev => ({...prev, [id]: {...prev[id], [field]: value}}));
    };

    const saveDeviceType = async (id) => {
        const draft = drafts[id];
        if (!draft?.name) {
            toast.error('Device name is required');
            return;
        }
        setSavingId(id);
        try {
            await api.put(`/access/device-types/${id}`, toPayload(draft));
            toast.success('Access device settings saved');
            await fetchDeviceTypes();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to save access device settings');
        } finally {
            setSavingId(null);
        }
    };

    const createDeviceType = async () => {
        if (!newDevice.name) {
            toast.error('Device name is required');
            return;
        }
        setSavingId('new');
        try {
            await api.post('/access/device-types', toPayload(newDevice));
            toast.success('Access device type added');
            setNewDevice(blankDeviceType);
            await fetchDeviceTypes();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to add access device type');
        } finally {
            setSavingId(null);
        }
    };

    const archiveDeviceType = async (id) => {
        setSavingId(id);
        try {
            await api.delete(`/access/device-types/${id}`);
            toast.success('Access device type archived');
            await fetchDeviceTypes();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to archive access device type');
        } finally {
            setSavingId(null);
        }
    };

    const renderMoneyInput = (id, label, value, onChange) => (
        <div className="space-y-1">
            <Label htmlFor={id}>{label}</Label>
            <Input
                id={id}
                type="number"
                min="0"
                step="0.01"
                value={centsToDollars(value)}
                onChange={(event) => onChange(dollarsToCents(event.target.value))}
            />
        </div>
    );

    const renderRuleToggles = (prefix, form, onChange) => (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
                ['request_available', 'Available for requests'],
                ['requires_approval', 'Requires approval'],
                ['requires_return', 'Requires return'],
                ['is_active', 'Active'],
            ].map(([field, label]) => (
                <div key={field} className="flex items-center justify-between gap-3 rounded-md border p-3">
                    <Label htmlFor={`${prefix}-${field}`} className="text-sm">{label}</Label>
                    <Switch
                        id={`${prefix}-${field}`}
                        checked={Boolean(form[field])}
                        onCheckedChange={(checked) => onChange(field, checked)}
                    />
                </div>
            ))}
        </div>
    );

    if (loading) {
        return (
            <Card>
                <CardContent className="flex min-h-48 items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-primary"/>
                </CardContent>
            </Card>
        );
    }

    return (
        <div className="space-y-6">
            <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                <CardHeader className="bg-muted/30 pb-4">
                    <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                            <KeyRound className="h-5 w-5 text-primary"/>
                            <CardTitle>Access Devices</CardTitle>
                        </div>
                        <Badge variant="outline">{activeCount} active</Badge>
                    </div>
                    <CardDescription>
                        Configure the fobs, remotes and access cards residents can request for this building.
                    </CardDescription>
                </CardHeader>
                <CardContent className="pt-6 space-y-5">
                    <div className="grid gap-4 md:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="new-access-device-name">Device Name</Label>
                            <Input
                                id="new-access-device-name"
                                value={newDevice.name}
                                onChange={(event) => setNewDevice(prev => ({...prev, name: event.target.value}))}
                                placeholder="Garage remote"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="new-access-device-key">Type Key</Label>
                            <Input
                                id="new-access-device-key"
                                value={newDevice.type_key}
                                onChange={(event) => setNewDevice(prev => ({...prev, type_key: event.target.value}))}
                                placeholder="garage_remote"
                            />
                        </div>
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="new-access-device-description">Description</Label>
                        <Textarea
                            id="new-access-device-description"
                            value={newDevice.description}
                            onChange={(event) => setNewDevice(prev => ({...prev, description: event.target.value}))}
                            rows={2}
                        />
                    </div>
                    <div className="grid gap-4 md:grid-cols-4">
                        {renderMoneyInput('new-access-device-fee', 'Fee', newDevice.fee_cents, value => setNewDevice(prev => ({...prev, fee_cents: value})))}
                        {renderMoneyInput('new-access-device-deposit', 'Refundable Deposit', newDevice.deposit_cents, value => setNewDevice(prev => ({...prev, deposit_cents: value})))}
                        {renderMoneyInput('new-access-device-lost-fee', 'Lost Device Fee', newDevice.replacement_lost_fee_cents, value => setNewDevice(prev => ({...prev, replacement_lost_fee_cents: value})))}
                        <div className="space-y-1">
                            <Label htmlFor="new-access-device-max">Maximum Quantity</Label>
                            <Input
                                id="new-access-device-max"
                                type="number"
                                min="1"
                                max="50"
                                value={newDevice.max_quantity}
                                onChange={(event) => setNewDevice(prev => ({...prev, max_quantity: event.target.value}))}
                            />
                        </div>
                    </div>
                    <div className="grid gap-4 md:grid-cols-[1fr_180px]">
                        {renderRuleToggles('new-access-device', newDevice, (field, value) => setNewDevice(prev => ({...prev, [field]: value})))}
                        <div className="space-y-1">
                            <Label htmlFor="new-access-device-effective">Effective Date</Label>
                            <Input
                                id="new-access-device-effective"
                                type="date"
                                value={newDevice.effective_date}
                                onChange={(event) => setNewDevice(prev => ({...prev, effective_date: event.target.value}))}
                            />
                        </div>
                    </div>
                    <Button type="button" onClick={createDeviceType} disabled={savingId === 'new'}>
                        {savingId === 'new' ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <Plus className="mr-2 h-4 w-4"/>}
                        Add Device Type
                    </Button>
                </CardContent>
            </Card>

            {deviceTypes.map((deviceType) => {
                const draft = drafts[deviceType.device_type_id] || normaliseForForm(deviceType);
                return (
                    <Card key={deviceType.device_type_id} className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div>
                                    <CardTitle className="text-lg">{deviceType.name}</CardTitle>
                                    <CardDescription>{deviceType.type_key}</CardDescription>
                                </div>
                                <Badge variant={deviceType.is_active ? 'default' : 'secondary'}>
                                    {deviceType.is_active ? 'Active' : 'Archived'}
                                </Badge>
                            </div>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor={`access-device-name-${deviceType.device_type_id}`}>Device Name</Label>
                                    <Input
                                        id={`access-device-name-${deviceType.device_type_id}`}
                                        value={draft.name}
                                        onChange={(event) => updateDraft(deviceType.device_type_id, 'name', event.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor={`access-device-key-${deviceType.device_type_id}`}>Type Key</Label>
                                    <Input
                                        id={`access-device-key-${deviceType.device_type_id}`}
                                        value={draft.type_key}
                                        onChange={(event) => updateDraft(deviceType.device_type_id, 'type_key', event.target.value)}
                                    />
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor={`access-device-description-${deviceType.device_type_id}`}>Description</Label>
                                <Textarea
                                    id={`access-device-description-${deviceType.device_type_id}`}
                                    value={draft.description || ''}
                                    onChange={(event) => updateDraft(deviceType.device_type_id, 'description', event.target.value)}
                                    rows={2}
                                />
                            </div>
                            <div className="grid gap-4 md:grid-cols-4">
                                {renderMoneyInput(`access-device-fee-${deviceType.device_type_id}`, 'Fee', draft.fee_cents, value => updateDraft(deviceType.device_type_id, 'fee_cents', value))}
                                {renderMoneyInput(`access-device-deposit-${deviceType.device_type_id}`, 'Refundable Deposit', draft.deposit_cents, value => updateDraft(deviceType.device_type_id, 'deposit_cents', value))}
                                {renderMoneyInput(`access-device-lost-fee-${deviceType.device_type_id}`, 'Lost Device Fee', draft.replacement_lost_fee_cents, value => updateDraft(deviceType.device_type_id, 'replacement_lost_fee_cents', value))}
                                <div className="space-y-1">
                                    <Label htmlFor={`access-device-max-${deviceType.device_type_id}`}>Maximum Quantity</Label>
                                    <Input
                                        id={`access-device-max-${deviceType.device_type_id}`}
                                        type="number"
                                        min="1"
                                        max="50"
                                        value={draft.max_quantity}
                                        onChange={(event) => updateDraft(deviceType.device_type_id, 'max_quantity', event.target.value)}
                                    />
                                </div>
                            </div>
                            <div className="grid gap-4 md:grid-cols-[1fr_180px]">
                                {renderRuleToggles(`access-device-${deviceType.device_type_id}`, draft, (field, value) => updateDraft(deviceType.device_type_id, field, value))}
                                <div className="space-y-1">
                                    <Label htmlFor={`access-device-effective-${deviceType.device_type_id}`}>Effective Date</Label>
                                    <Input
                                        id={`access-device-effective-${deviceType.device_type_id}`}
                                        type="date"
                                        value={draft.effective_date || ''}
                                        onChange={(event) => updateDraft(deviceType.device_type_id, 'effective_date', event.target.value)}
                                    />
                                </div>
                            </div>
                            <div className="flex flex-wrap gap-2">
                                <Button type="button" onClick={() => saveDeviceType(deviceType.device_type_id)} disabled={savingId === deviceType.device_type_id}>
                                    {savingId === deviceType.device_type_id ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <Save className="mr-2 h-4 w-4"/>}
                                    Save
                                </Button>
                                {deviceType.is_active && (
                                    <Button type="button" variant="outline" onClick={() => archiveDeviceType(deviceType.device_type_id)} disabled={savingId === deviceType.device_type_id}>
                                        <Archive className="mr-2 h-4 w-4"/>
                                        Archive
                                    </Button>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                );
            })}
        </div>
    );
};

export default AccessDeviceSettings;
