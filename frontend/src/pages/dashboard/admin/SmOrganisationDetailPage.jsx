import React, {useCallback, useEffect, useState} from 'react';
import {useParams, useRouter} from 'next/navigation';
import {useAuth} from '../../../contexts/AuthContext';
import {Badge} from '../../../components/ui/badge';
import {Button} from '../../../components/ui/button';
import {Input} from '../../../components/ui/input';
import {Label} from '../../../components/ui/label';
import {
    Building2,
    ChevronLeft,
    Edit2,
    Layers,
    Loader2,
    Save,
    Users,
    X,
} from 'lucide-react';
import {toast} from 'sonner';
import {formatDate, roleDisplayNames} from '../../../lib/utils';

const STATUS_COLORS = {
    active: 'bg-green-100 text-green-800 border-green-200',
    inactive: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    archived: 'bg-gray-100 text-gray-500 border-gray-200',
};

const TAB_SCHEMES = 'schemes';
const TAB_MEMBERS = 'members';
/**
 * @generated FunctionHeader
 * Function: SmOrganisationDetailPage
 * Path: frontend/src/pages/dashboard/admin/SmOrganisationDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SmOrganisationDetailPage = () => {
    const params = useParams();
    const tenantId = params?.tenant_id;
    const router = useRouter();
    const {api, isAdmin, loading: authLoading} = useAuth();

    const [org, setOrg] = useState(null);
    const [schemes, setSchemes] = useState([]);
    const [members, setMembers] = useState([]);
    const [tab, setTab] = useState(TAB_SCHEMES);
    const [loading, setLoading] = useState(true);
    const [tabLoading, setTabLoading] = useState(false);

    // Edit state
    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState({});
    const [saving, setSaving] = useState(false);

    const fetchOrg = useCallback(async () => {
        if (!tenantId) return;
        setLoading(true);
        try {
            const res = await api.get(`/admin/sm-organisations/${tenantId}`);
            setOrg(res.data);
            setEditForm({
                tenant_name: res.data.tenant_name || '',
                legal_name: res.data.legal_name || '',
                abn: res.data.abn || '',
                status: res.data.status || 'active',
            });
        } catch (err) {
            if (err?.response?.status === 404) {
                toast.error('Organisation not found.');
                router.push('/admin/sm-organisations');
            } else {
                toast.error('Failed to load organisation.');
            }
        } finally {
            setLoading(false);
        }
    }, [api, tenantId, router]);

    const fetchTabData = useCallback(async (path, setter) => {
        if (!tenantId) return;
        setTabLoading(true);
        try {
            const res = await api.get(path);
            setter(res.data.items || []);
        } catch {
            toast.error('Failed to load data.');
        } finally {
            setTabLoading(false);
        }
    }, [api, tenantId]);

    useEffect(() => {
        if (!authLoading) fetchOrg();
    }, [authLoading, fetchOrg]);

    useEffect(() => {
        if (tab === TAB_SCHEMES)
            fetchTabData(`/admin/sm-organisations/${tenantId}/schemes`, setSchemes);
        else
            fetchTabData(`/admin/sm-organisations/${tenantId}/members`, setMembers);
    }, [tab, tenantId, fetchTabData]);
    /**
     * @generated FunctionHeader
     * Function: onSave
     * Path: frontend/src/pages/dashboard/admin/SmOrganisationDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const onSave = async () => {
        setSaving(true);
        try {
            const payload = {};
            if (editForm.tenant_name !== org.tenant_name) payload.tenant_name = editForm.tenant_name;
            if ((editForm.legal_name || '') !== (org.legal_name || '')) payload.legal_name = editForm.legal_name || null;
            if ((editForm.abn || '') !== (org.abn || '')) payload.abn = editForm.abn || null;
            if (editForm.status !== org.status) payload.status = editForm.status;

            if (Object.keys(payload).length === 0) {
                setEditing(false);
                return;
            }
            const res = await api.patch(`/admin/sm-organisations/${tenantId}`, payload);
            setOrg(prev => ({...prev, ...res.data}));
            toast.success('Organisation updated.');
            setEditing(false);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to save.');
        } finally {
            setSaving(false);
        }
    };

    if (authLoading || loading) {
        return (
            <div className="flex items-center justify-center min-h-[40vh]">
                <Loader2 className="h-6 w-6 animate-spin text-primary" aria-label="Loading"/>
            </div>
        );
    }
    if (!isAdmin()) {
        return <div className="p-8 text-sm text-muted-foreground">Super admin access required.</div>;
    }
    if (!org) return null;

    return (
        <div className="p-6 max-w-4xl">

            <Button
                variant="ghost"
                onClick={() => router.push('/admin/sm-organisations')}
                className="mb-4 -ml-2"
                aria-label="Back to organisations"
            >
                <ChevronLeft className="h-4 w-4 mr-1"/> Back to organisations
            </Button>


            <div className="border rounded-xl p-5 mb-6 bg-card">
                <div className="flex items-start justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                        <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                            <Building2 className="h-5 w-5 text-primary"/>
                        </div>
                        <div className="min-w-0">
                            {editing ? (
                                <Input
                                    value={editForm.tenant_name}
                                    onChange={e => setEditForm(f => ({...f, tenant_name: e.target.value}))}
                                    className="text-lg font-bold h-8 px-2"
                                    aria-label="Organisation name"
                                    data-testid="sm-org-edit-name"
                                />
                            ) : (
                                <h1 className="text-xl font-bold truncate">{org.tenant_name}</h1>
                            )}
                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                                <Badge variant="outline" className="text-xs">
                                    {org.is_self_managed ? 'Self-managed' : 'SM firm'}
                                </Badge>
                                <span
                                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[org.status] ?? STATUS_COLORS.inactive}`}>
                                    {org.status}
                                </span>
                                <span className="text-xs text-muted-foreground font-mono">
                                    {tenantId}
                                </span>
                            </div>
                        </div>
                    </div>

                    <div className="flex gap-2 flex-shrink-0">
                        {editing ? (
                            <>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => {
                                        setEditing(false);
                                        setEditForm({
                                            tenant_name: org.tenant_name || '',
                                            legal_name: org.legal_name || '',
                                            abn: org.abn || '',
                                            status: org.status || 'active',
                                        });
                                    }}
                                    disabled={saving}
                                    aria-label="Cancel edit"
                                >
                                    <X className="h-4 w-4 mr-1"/> Cancel
                                </Button>
                                <Button size="sm" onClick={onSave} disabled={saving} data-testid="sm-org-save-btn">
                                    {saving
                                        ? <><Loader2 className="h-4 w-4 mr-1 animate-spin"/> Saving…</>
                                        : <><Save className="h-4 w-4 mr-1"/> Save</>
                                    }
                                </Button>
                            </>
                        ) : (
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setEditing(true)}
                                data-testid="sm-org-edit-btn"
                                aria-label="Edit organisation"
                            >
                                <Edit2 className="h-4 w-4 mr-1"/> Edit
                            </Button>
                        )}
                    </div>
                </div>


                <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t">
                    <div>
                        <Label className="text-xs text-muted-foreground">Legal name</Label>
                        {editing ? (
                            <Input
                                value={editForm.legal_name}
                                onChange={e => setEditForm(f => ({...f, legal_name: e.target.value}))}
                                placeholder="Full registered name"
                                className="mt-1 h-8 text-sm"
                                aria-label="Legal name"
                            />
                        ) : (
                            <p className="text-sm mt-0.5">{org.legal_name || <span className="text-muted-foreground">—</span>}</p>
                        )}
                    </div>
                    <div>
                        <Label className="text-xs text-muted-foreground">ABN</Label>
                        {editing ? (
                            <Input
                                value={editForm.abn}
                                onChange={e => setEditForm(f => ({...f, abn: e.target.value}))}
                                placeholder="11 digits"
                                inputMode="numeric"
                                maxLength={11}
                                className="mt-1 h-8 text-sm font-mono"
                                aria-label="ABN"
                            />
                        ) : (
                            <p className="text-sm mt-0.5 font-mono">{org.abn || <span className="text-muted-foreground">—</span>}</p>
                        )}
                    </div>
                    <div>
                        <Label className="text-xs text-muted-foreground">Status</Label>
                        {editing ? (
                            <select
                                value={editForm.status}
                                onChange={e => setEditForm(f => ({...f, status: e.target.value}))}
                                className="mt-1 h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
                                aria-label="Status"
                            >
                                <option value="active">Active</option>
                                <option value="inactive">Inactive</option>
                                <option value="archived">Archived</option>
                            </select>
                        ) : (
                            <p className="text-sm mt-0.5">{org.status}</p>
                        )}
                    </div>
                </div>
            </div>


            <div
                role="tablist"
                aria-label="Organisation details"
                className="flex border-b mb-4 gap-1"
            >
                {[
                    {id: TAB_SCHEMES, label: 'Buildings / Schemes', icon: Layers},
                    {id: TAB_MEMBERS, label: 'Members', icon: Users},
                ].map(({id, label, icon: Icon}) => (
                    <button
                        key={id}
                        role="tab"
                        aria-selected={tab === id}
                        aria-controls={`tab-panel-${id}`}
                        id={`tab-${id}`}
                        onClick={() => setTab(id)}
                        className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                            tab === id
                                ? 'border-primary text-primary'
                                : 'border-transparent text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        <Icon className="h-4 w-4"/>
                        {label}
                    </button>
                ))}
            </div>


            {tabLoading ? (
                <div className="flex items-center justify-center py-12" aria-live="polite" aria-busy="true">
                    <Loader2 className="h-5 w-5 animate-spin text-primary"/>
                </div>
            ) : (
                <>

                    <div
                        role="tabpanel"
                        id={`tab-panel-${TAB_SCHEMES}`}
                        aria-labelledby={`tab-${TAB_SCHEMES}`}
                        hidden={tab !== TAB_SCHEMES}
                    >
                        {schemes.length === 0 ? (
                            <div className="text-center py-12 text-muted-foreground">
                                <Layers className="h-8 w-8 mx-auto mb-2 opacity-30"/>
                                <p className="text-sm">No buildings onboarded yet.</p>
                                <Button
                                    className="mt-3"
                                    size="sm"
                                    onClick={() => router.push('/admin/buildings/onboard')}
                                >
                                    Onboard a building
                                </Button>
                            </div>
                        ) : (
                            <div className="border rounded-xl overflow-hidden" data-testid="sm-org-schemes-table">
                                <table className="w-full text-sm">
                                    <thead className="bg-muted/50 border-b">
                                    <tr>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Scheme</th>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Plan #</th>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Jurisdiction</th>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Status</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    {schemes.map(s => (
                                        <tr key={s.scheme_id} className="border-b last:border-0 hover:bg-muted/20">
                                            <td className="px-4 py-2.5">
                                                <div className="font-medium">{s.scheme_name}</div>
                                                {s.legal_name && <div className="text-xs text-muted-foreground">{s.legal_name}</div>}
                                            </td>
                                            <td className="px-4 py-2.5 font-mono text-xs">{s.scheme_number}</td>
                                            <td className="px-4 py-2.5">
                                                <Badge variant="outline" className="text-xs">{s.jurisdiction}</Badge>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[s.status] ?? STATUS_COLORS.inactive}`}>
                                                    {s.status}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>


                    <div
                        role="tabpanel"
                        id={`tab-panel-${TAB_MEMBERS}`}
                        aria-labelledby={`tab-${TAB_MEMBERS}`}
                        hidden={tab !== TAB_MEMBERS}
                    >
                        {members.length === 0 ? (
                            <div className="text-center py-12 text-muted-foreground">
                                <Users className="h-8 w-8 mx-auto mb-2 opacity-30"/>
                                <p className="text-sm">No active members yet.</p>
                            </div>
                        ) : (
                            <div className="border rounded-xl overflow-hidden" data-testid="sm-org-members-table">
                                <table className="w-full text-sm">
                                    <thead className="bg-muted/50 border-b">
                                    <tr>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Member</th>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Role</th>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Building</th>
                                        <th className="text-left px-4 py-2.5 font-medium text-muted-foreground">Granted</th>
                                    </tr>
                                    </thead>
                                    <tbody>
                                    {members.map(m => (
                                        <tr key={`${m.user_id}-${m.scheme_id}`} className="border-b last:border-0 hover:bg-muted/20">
                                            <td className="px-4 py-2.5">
                                                <div className="font-medium">{m.full_name || '—'}</div>
                                                <div className="text-xs text-muted-foreground">{m.email}</div>
                                            </td>
                                            <td className="px-4 py-2.5">
                                                <Badge variant="secondary" className="text-xs">
                                                    {roleDisplayNames[m.role] || m.role}
                                                </Badge>
                                            </td>
                                            <td className="px-4 py-2.5 text-sm text-muted-foreground">
                                                {m.scheme_name || <span className="italic">Tenant-wide</span>}
                                            </td>
                                            <td className="px-4 py-2.5 text-xs text-muted-foreground">
                                                {formatDate(m.granted_at) || '—'}
                                            </td>
                                        </tr>
                                    ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
};

export default SmOrganisationDetailPage;
