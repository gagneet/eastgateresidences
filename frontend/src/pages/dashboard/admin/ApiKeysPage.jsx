// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import {
    AlertTriangle,
    Check,
    Copy,
    Eye,
    EyeOff,
    Info,
    KeyRound,
    Loader2,
    Lock,
    Plus,
    RefreshCw,
    Shield,
    Trash2
} from 'lucide-react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Badge } from '../../../components/ui/badge';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../../components/ui/dialog';

const ALL_SCOPES = [
    {value: 'read:building', label: 'Read Building', description: 'View building information and settings'},
    {value: 'read:finance', label: 'Read Finance', description: 'View financial summaries and levy data'},
    {value: 'read:maintenance', label: 'Read Maintenance', description: 'View maintenance requests'},
    {value: 'write:maintenance', label: 'Write Maintenance', description: 'Create and update maintenance requests'},
    {value: 'read:work-orders', label: 'Read Work Orders', description: 'View work orders'},
    {value: 'write:work-orders', label: 'Write Work Orders', description: 'Create and update work orders'},
    {value: 'write:service', label: 'Write Service', description: 'Submit service provider data'},
    {value: 'manage:webhooks', label: 'Manage Webhooks', description: 'Create and manage webhook endpoints'},
];
/**
 * @generated FunctionHeader
 * Function: ScopeCheckbox
 * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ScopeCheckbox({scope, checked, onChange}) {
    return (
        <label
            className="flex items-start gap-3 rounded-lg border p-3 cursor-pointer hover:bg-gray-50 transition-colors">
            <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600 cursor-pointer"
                checked={checked}
                onChange={e => onChange(scope.value, e.target.checked)}
            />
            <div>
                <p className="text-sm font-medium text-gray-800">{scope.label}</p>
                <p className="text-xs text-gray-500">{scope.description}</p>
            </div>
        </label>
    );
}
/**
 * @generated FunctionHeader
 * Function: CopyButton
 * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function CopyButton({text, size = 'sm'}) {
    const [copied, setCopied] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: handleCopy
     * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCopy = () => {
        navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
    };
    return (
        <Button variant="outline" size={size} onClick={handleCopy} className="shrink-0">
            {copied ? <Check className="h-4 w-4 text-green-600"/> : <Copy className="h-4 w-4"/>}
            <span className="ml-1">{copied ? 'Copied' : 'Copy'}</span>
        </Button>
    );
}
/**
 * @generated FunctionHeader
 * Function: formatDate
 * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatDate(dateStr) {
    if (!dateStr) return '—';
    try {
        return new Date(dateStr).toLocaleDateString('en-AU', {day: '2-digit', month: 'short', year: 'numeric'});
    } catch {
        return dateStr;
    }
}
/**
 * @generated FunctionHeader
 * Function: ApiKeysPage
 * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ApiKeysPage = () => {
    const {user, api, isAdmin, selectedBuilding} = useAuth();
    const router = useRouter();

    const [keys, setKeys] = useState([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    // Create dialog state
    const [createOpen, setCreateOpen] = useState(false);
    const [creating, setCreating] = useState(false);
    const [newKeyName, setNewKeyName] = useState('');
    const [selectedScopes, setSelectedScopes] = useState(['read:building']);

    // One-time key reveal dialog
    const [revealOpen, setRevealOpen] = useState(false);
    const [revealedKey, setRevealedKey] = useState('');
    const [revealedKeyName, setRevealedKeyName] = useState('');
    const [showKey, setShowKey] = useState(false);

    // Revoke confirm dialog
    const [revokeOpen, setRevokeOpen] = useState(false);
    const [revokeTarget, setRevokeTarget] = useState(null);
    const [revoking, setRevoking] = useState(false);

    const canAccess = user && isAdmin && isAdmin();

    const fetchKeys = useCallback(async () => {
        setRefreshing(true);
        try {
            const res = await api.get('/v1/api-keys');
            setKeys(res.data || []);
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to load API keys';
            toast.error(msg);
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [api]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchKeys();
    }, [canAccess, fetchKeys, router]);
    /**
     * @generated FunctionHeader
     * Function: handleScopeChange
     * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleScopeChange = (scope, checked) => {
        setSelectedScopes(prev =>
            checked ? [...prev, scope] : prev.filter(s => s !== scope)
        );
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreate
     * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreate = async () => {
        if (!newKeyName.trim()) {
            toast.error('Please enter a key name');
            return;
        }
        if (selectedScopes.length === 0) {
            toast.error('Select at least one scope');
            return;
        }
        setCreating(true);
        try {
            // building_id is intentionally NOT sent — POST /v1/api-keys' request model
            // (APIKeyCreate) has no building_id field; the backend self-resolves it via
            // resolve_building_id_for_key() instead. Sending an extra field the API
            // contract doesn't define is dead weight today and a latent bug risk if the
            // model ever adds strict validation or starts honoring the field.
            const res = await api.post('/v1/api-keys', {
                name: newKeyName.trim(),
                scopes: selectedScopes,
            });
            const data = res.data;
            setCreateOpen(false);
            setNewKeyName('');
            setSelectedScopes(['read:building']);
            await fetchKeys();
            // Show the full key one time
            setRevealedKey(data.api_key || '');
            setRevealedKeyName(data.name || newKeyName.trim());
            setShowKey(false);
            setRevealOpen(true);
            toast.success('API key created');
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to create API key';
            toast.error(msg);
        } finally {
            setCreating(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRevoke
     * Path: frontend/src/pages/dashboard/admin/ApiKeysPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRevoke = async () => {
        if (!revokeTarget) return;
        setRevoking(true);
        try {
            await api.delete(`/v1/api-keys/${revokeTarget.id}`);
            toast.success(`Key "${revokeTarget.name}" revoked`);
            setRevokeOpen(false);
            setRevokeTarget(null);
            await fetchKeys();
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to revoke key';
            toast.error(msg);
        } finally {
            setRevoking(false);
        }
    };

    if (!canAccess) return null;

    const activeKeys = keys.filter(k => k.is_active !== false);
    const revokedKeys = keys.filter(k => k.is_active === false);

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                        <KeyRound className="h-6 w-6 text-indigo-600"/>
                        External API Keys
                    </h1>
                    <p className="text-sm text-gray-500 mt-0.5">
                        Manage API keys for third-party integrations and external access
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={fetchKeys} disabled={refreshing}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>
                        Refresh
                    </Button>
                    <Button size="sm" onClick={() => setCreateOpen(true)}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white">
                        <Plus className="h-4 w-4 mr-2"/>
                        Create New Key
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Card>
                    <CardContent className="pt-5 pb-4">
                        <p className="text-xs text-gray-500 mb-1">Total Keys</p>
                        <p className="text-2xl font-bold text-gray-900">{keys.length}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-5 pb-4">
                        <p className="text-xs text-gray-500 mb-1">Active</p>
                        <p className="text-2xl font-bold text-green-700">{activeKeys.length}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-5 pb-4">
                        <p className="text-xs text-gray-500 mb-1">Revoked</p>
                        <p className="text-2xl font-bold text-gray-400">{revokedKeys.length}</p>
                    </CardContent>
                </Card>
            </div>

            {/* Security notice */}
            <div className="flex items-start gap-3 rounded-lg p-4 bg-blue-50 border border-blue-200">
                <Shield className="h-5 w-5 text-blue-600 shrink-0 mt-0.5"/>
                <div className="text-sm text-blue-800">
                    <p className="font-semibold">API Key Security</p>
                    <p className="mt-0.5">
                        API keys grant programmatic access to this building's data. Only share keys with trusted
                        integrations. Full key values are shown only at creation — store them securely immediately.
                        Revoke any key that may have been compromised.
                    </p>
                </div>
            </div>

            {/* Keys Table */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">API Keys</CardTitle>
                    {selectedBuilding?.name && (
                        <CardDescription className="text-xs">Building: {selectedBuilding.name}</CardDescription>
                    )}
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-12">
                            <Loader2 className="h-8 w-8 animate-spin text-gray-400"/>
                        </div>
                    ) : keys.length === 0 ? (
                        <div className="py-12 text-center">
                            <KeyRound className="h-10 w-10 text-gray-300 mx-auto mb-3"/>
                            <p className="text-gray-500 font-medium">No API keys yet</p>
                            <p className="text-sm text-gray-400 mt-1">Create your first key to enable external
                                integrations</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                <tr className="border-b text-left text-gray-500">
                                    <th className="pb-3 font-medium">Name</th>
                                    <th className="pb-3 font-medium">Key Prefix</th>
                                    <th className="pb-3 font-medium">Scopes</th>
                                    <th className="pb-3 font-medium">Building</th>
                                    <th className="pb-3 font-medium">Status</th>
                                    <th className="pb-3 font-medium">Created</th>
                                    <th className="pb-3 font-medium text-right">Actions</th>
                                </tr>
                                </thead>
                                <tbody className="divide-y">
                                {keys.map(key => (
                                    <tr key={key.id}
                                        className={`hover:bg-gray-50 ${key.is_active === false ? 'opacity-50' : ''}`}>
                                        <td className="py-3 font-medium text-gray-900">{key.name || '—'}</td>
                                        <td className="py-3">
                                            <code
                                                className="bg-gray-100 px-2 py-0.5 rounded text-xs font-mono text-gray-700">
                                                {key.key_prefix ? `${key.key_prefix}...` : '—'}
                                            </code>
                                        </td>
                                        <td className="py-3">
                                            <div className="flex flex-wrap gap-1">
                                                {( key.scopes || [] ).slice(0, 3).map(s => (
                                                    <Badge key={s} variant="outline" className="text-xs px-1.5 py-0">
                                                        {s}
                                                    </Badge>
                                                ))}
                                                {( key.scopes || [] ).length > 3 && (
                                                    <Badge variant="outline"
                                                           className="text-xs px-1.5 py-0 text-gray-400">
                                                        +{key.scopes.length - 3}
                                                    </Badge>
                                                )}
                                            </div>
                                        </td>
                                        <td className="py-3 text-gray-500">
                                            {key.building_id || <span className="text-gray-300">—</span>}
                                        </td>
                                        <td className="py-3">
                                            {key.is_active !== false ? (
                                                <span
                                                    className="inline-flex items-center rounded-full bg-green-100 text-green-800 px-2 py-0.5 text-xs font-medium">
                            Active
                          </span>
                                            ) : (
                                                <span
                                                    className="inline-flex items-center rounded-full bg-gray-100 text-gray-500 px-2 py-0.5 text-xs font-medium">
                            Revoked
                          </span>
                                            )}
                                        </td>
                                        <td className="py-3 text-gray-500">{formatDate(key.created_at)}</td>
                                        <td className="py-3 text-right">
                                            {key.is_active !== false && (
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    className="text-red-600 border-red-200 hover:bg-red-50 hover:text-red-700"
                                                    onClick={() => {
                                                        setRevokeTarget(key);
                                                        setRevokeOpen(true);
                                                    }}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5 mr-1"/>
                                                    Revoke
                                                </Button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Create API Key Dialog */}
            <Dialog open={createOpen} onOpenChange={setCreateOpen}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <KeyRound className="h-5 w-5 text-indigo-600"/>
                            Create API Key
                        </DialogTitle>
                        <DialogDescription>
                            Choose a name and scopes for the new API key. The full key will be shown once after
                            creation.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-5 py-2">
                        {/* Name */}
                        <div className="space-y-1.5">
                            <Label htmlFor="key-name">Key Name <span className="text-red-500">*</span></Label>
                            <Input
                                id="key-name"
                                placeholder="e.g. Maintenance Integration, Property Management System"
                                value={newKeyName}
                                onChange={e => setNewKeyName(e.target.value)}
                                maxLength={80}
                            />
                            <p className="text-xs text-gray-400">A descriptive name to identify where this key is
                                used</p>
                        </div>

                        {/* Building */}
                        {selectedBuilding && (
                            <div className="space-y-1.5">
                                <Label>Building</Label>
                                <div className="rounded-md border bg-gray-50 px-3 py-2 text-sm text-gray-700">
                                    {selectedBuilding.name || selectedBuilding.id}
                                    <span className="ml-2 text-xs text-gray-400">(pre-filled)</span>
                                </div>
                            </div>
                        )}

                        {/* Scopes */}
                        <div className="space-y-2">
                            <Label>Scopes <span className="text-red-500">*</span></Label>
                            <p className="text-xs text-gray-500">Select the permissions this key should have</p>
                            <div className="grid grid-cols-1 gap-2 max-h-60 overflow-y-auto pr-1">
                                {ALL_SCOPES.map(scope => (
                                    <ScopeCheckbox
                                        key={scope.value}
                                        scope={scope}
                                        checked={selectedScopes.includes(scope.value)}
                                        onChange={handleScopeChange}
                                    />
                                ))}
                            </div>
                            <p className="text-xs text-gray-400">
                                {selectedScopes.length} scope{selectedScopes.length !== 1 ? 's' : ''} selected
                            </p>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleCreate}
                            disabled={creating || !newKeyName.trim() || selectedScopes.length === 0}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white"
                        >
                            {creating && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                            Create Key
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* One-Time Key Reveal Dialog */}
            <Dialog open={revealOpen} onOpenChange={open => {
                if (!open) {
                    setRevealOpen(false);
                    setRevealedKey('');
                }
            }}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Lock className="h-5 w-5 text-amber-600"/>
                            Save Your API Key
                        </DialogTitle>
                        <DialogDescription>
                            The key for <strong>{revealedKeyName}</strong> has been created. Copy and store it now.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        <div className="flex items-start gap-3 rounded-lg p-4 bg-amber-50 border border-amber-200">
                            <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5"/>
                            <p className="text-sm text-amber-800 font-medium">
                                Save this key now — it will not be shown again. Once you close this dialog, the full key
                                cannot be recovered.
                            </p>
                        </div>

                        <div className="space-y-1.5">
                            <Label>API Key</Label>
                            <div className="flex items-center gap-2">
                                <div className="flex-1 relative">
                                    <Input
                                        readOnly
                                        value={revealedKey}
                                        type={showKey ? 'text' : 'password'}
                                        className="font-mono text-sm pr-10"
                                    />
                                    <button
                                        type="button"
                                        className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                                        onClick={() => setShowKey(v => !v)}
                                    >
                                        {showKey ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}
                                    </button>
                                </div>
                                <CopyButton text={revealedKey}/>
                            </div>
                        </div>

                        <div className="flex items-start gap-2 text-xs text-gray-500">
                            <Info className="h-3.5 w-3.5 shrink-0 mt-0.5"/>
                            <span>
                Store this key in a secure location such as an environment variable, secrets manager (AWS Secrets Manager, HashiCorp Vault), or your deployment platform's secrets store. Never commit API keys to source control.
              </span>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button
                            onClick={() => {
                                setRevealOpen(false);
                                setRevealedKey('');
                            }}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white"
                        >
                            I've saved the key
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Revoke Confirm Dialog */}
            <Dialog open={revokeOpen} onOpenChange={open => {
                if (!open && !revoking) {
                    setRevokeOpen(false);
                    setRevokeTarget(null);
                }
            }}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-red-700">
                            <AlertTriangle className="h-5 w-5"/>
                            Revoke API Key
                        </DialogTitle>
                        <DialogDescription>
                            Are you sure you want to revoke <strong>{revokeTarget?.name}</strong>? Any integrations
                            using this key will immediately lose access and cannot be restored.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => {
                                setRevokeOpen(false);
                                setRevokeTarget(null);
                            }}
                            disabled={revoking}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleRevoke}
                            disabled={revoking}
                        >
                            {revoking && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                            Revoke Key
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default ApiKeysPage;
