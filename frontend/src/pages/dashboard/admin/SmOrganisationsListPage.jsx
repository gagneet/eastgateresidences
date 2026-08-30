import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../../contexts/AuthContext';
import {Badge} from '../../../components/ui/badge';
import {Button} from '../../../components/ui/button';
import {Input} from '../../../components/ui/input';
import {
    Building2,
    ChevronRight,
    Layers,
    Loader2,
    Plus,
    RefreshCw,
    Search,
    Users,
} from 'lucide-react';
import {toast} from 'sonner';

const STATUS_COLORS = {
    active: 'bg-green-100 text-green-800 border-green-200',
    inactive: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    archived: 'bg-gray-100 text-gray-500 border-gray-200',
};
/**
 * @generated FunctionHeader
 * Function: SmOrganisationsListPage
 * Path: frontend/src/pages/dashboard/admin/SmOrganisationsListPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SmOrganisationsListPage = () => {
    const router = useRouter();
    const {api, isAdmin, loading: authLoading} = useAuth();

    const [orgs, setOrgs] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [includeSelfManaged, setIncludeSelfManaged] = useState(true);

    const fetchOrgs = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.get('/admin/sm-organisations', {
                params: {include_self_managed: includeSelfManaged},
            });
            setOrgs(res.data.items || []);
        } catch {
            toast.error('Failed to load organisations.');
        } finally {
            setLoading(false);
        }
    }, [api, includeSelfManaged]);

    useEffect(() => {
        if (!authLoading) fetchOrgs();
    }, [authLoading, fetchOrgs]);

    const filtered = useMemo(() =>
        orgs.filter(o =>
            o.tenant_name?.toLowerCase().includes(search.toLowerCase()) ||
            o.abn?.includes(search) ||
            o.legal_name?.toLowerCase().includes(search.toLowerCase()),
        ),
    [orgs, search]);

    if (authLoading) {
        return (
            <div className="flex items-center justify-center min-h-[40vh]">
                <Loader2 className="h-6 w-6 animate-spin text-primary"/>
            </div>
        );
    }
    if (!isAdmin()) {
        return <div className="p-8 text-sm text-muted-foreground">Super admin access required.</div>;
    }

    return (
        <div className="p-6 max-w-5xl">

            <div className="flex items-center justify-between mb-6">
                <div>
                    <h1 className="text-xl font-bold flex items-center gap-2">
                        <Building2 className="h-5 w-5 text-primary"/>
                        SM Organisations
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Strata management firms and self-managed owner corporations.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => router.push('/admin/sm-organisations/new')}
                        data-testid="sm-org-new-btn"
                    >
                        <Plus className="h-4 w-4 mr-1"/>
                        New SM Org
                    </Button>
                    <Button
                        size="sm"
                        onClick={() => router.push('/admin/sm-organisations/new-self-managed')}
                        data-testid="sm-org-new-self-managed-btn"
                    >
                        <Plus className="h-4 w-4 mr-1"/>
                        New Self-Managed
                    </Button>
                </div>
            </div>


            <div className="flex items-center gap-3 mb-4">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                    <Input
                        placeholder="Search name or ABN…"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        className="pl-9"
                        data-testid="sm-org-search"
                    />
                </div>
                <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                    <input
                        type="checkbox"
                        checked={includeSelfManaged}
                        onChange={e => setIncludeSelfManaged(e.target.checked)}
                        className="rounded"
                        data-testid="sm-org-include-self-managed"
                    />
                    Include self-managed
                </label>
                <Button variant="ghost" size="sm" onClick={fetchOrgs} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`}/>
                </Button>
            </div>


            {loading ? (
                <div className="flex items-center justify-center py-16">
                    <Loader2 className="h-6 w-6 animate-spin text-primary"/>
                </div>
            ) : filtered.length === 0 ? (
                <div className="text-center py-16 text-muted-foreground">
                    <Building2 className="h-10 w-10 mx-auto mb-3 opacity-30"/>
                    <p className="text-sm">No organisations found.</p>
                    <Button
                        className="mt-4"
                        size="sm"
                        onClick={() => router.push('/admin/sm-organisations/new-self-managed')}
                    >
                        <Plus className="h-4 w-4 mr-1"/> Create first organisation
                    </Button>
                </div>
            ) : (
                <div className="border rounded-xl overflow-hidden">
                    <table className="w-full text-sm" data-testid="sm-org-table">
                        <thead className="bg-muted/50 border-b">
                        <tr>
                            <th className="text-left px-4 py-3 font-medium text-muted-foreground">Organisation</th>
                            <th className="text-left px-4 py-3 font-medium text-muted-foreground">ABN</th>
                            <th className="text-left px-4 py-3 font-medium text-muted-foreground">Type</th>
                            <th className="text-center px-4 py-3 font-medium text-muted-foreground">
                                <Layers className="h-4 w-4 inline-block mr-1"/>Schemes
                            </th>
                            <th className="text-center px-4 py-3 font-medium text-muted-foreground">
                                <Users className="h-4 w-4 inline-block mr-1"/>Members
                            </th>
                            <th className="text-left px-4 py-3 font-medium text-muted-foreground">Status</th>
                            <th className="w-8"/>
                        </tr>
                        </thead>
                        <tbody>
                        {filtered.map(org => (
                            <tr
                                key={org.tenant_id}
                                className="border-b last:border-0 hover:bg-muted/30 cursor-pointer transition-colors"
                                onClick={() => router.push(`/admin/sm-organisations/${org.tenant_id}`)}
                                data-testid={`sm-org-row-${org.tenant_id}`}
                            >
                                <td className="px-4 py-3">
                                    <div className="font-medium">{org.tenant_name}</div>
                                    {org.legal_name && org.legal_name !== org.tenant_name && (
                                        <div className="text-xs text-muted-foreground">{org.legal_name}</div>
                                    )}
                                </td>
                                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                                    {org.abn || '—'}
                                </td>
                                <td className="px-4 py-3">
                                    <Badge variant="outline" className="text-xs">
                                        {org.is_self_managed ? 'Self-managed' : 'SM firm'}
                                    </Badge>
                                </td>
                                <td className="px-4 py-3 text-center">{org.scheme_count ?? 0}</td>
                                <td className="px-4 py-3 text-center">{org.member_count ?? 0}</td>
                                <td className="px-4 py-3">
                                    <span
                                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[org.status] ?? STATUS_COLORS.inactive}`}>
                                        {org.status ?? 'unknown'}
                                    </span>
                                </td>
                                <td className="px-4 py-3">
                                    <ChevronRight className="h-4 w-4 text-muted-foreground"/>
                                </td>
                            </tr>
                        ))}
                        </tbody>
                    </table>
                </div>
            )}

            <p className="text-xs text-muted-foreground mt-3">
                {filtered.length} of {orgs.length} organisation{orgs.length !== 1 ? 's' : ''}
            </p>
        </div>
    );
};

export default SmOrganisationsListPage;
