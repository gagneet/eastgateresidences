import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Badge } from '../../../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../../components/ui/table';
import { Filter, History, RefreshCw, Search } from 'lucide-react';
import { formatDate } from '../../../lib/utils';
import { toast } from 'sonner';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "../../../components/ui/select";
/**
 * @generated FunctionHeader
 * Function: AuditLogPage
 * Path: frontend/src/pages/dashboard/admin/AuditLogPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AuditLogPage = () => {
    const {api} = useAuth();
    const [logs, setLogs] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [resourceFilter, setResourceFilter] = useState('all');

    const fetchLogs = useCallback(async () => {
        setLoading(true);
        try {
            let url = '/notifications/admin/audit-logs?limit=200';
            if (resourceFilter !== 'all') {
                url += `&resource_type=${resourceFilter}`;
            }
            const response = await api.get(url);
            setLogs(response.data);
        } catch (error) {
            console.error('Failed to fetch audit logs:', error);
            toast.error('Failed to load audit logs');
        } finally {
            setLoading(false);
        }
    }, [api, resourceFilter]);

    useEffect(() => {
        fetchLogs();
    }, [fetchLogs]);

    const filteredLogs = logs.filter(log =>
        log.user_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.action.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.resource_type.toLowerCase().includes(searchTerm.toLowerCase()) ||
        ( log.details && JSON.stringify(log.details).toLowerCase().includes(searchTerm.toLowerCase()) )
    );
    /**
     * @generated FunctionHeader
     * Function: getActionBadge
     * Path: frontend/src/pages/dashboard/admin/AuditLogPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getActionBadge = (action) => {
        const colors = {
            created: 'bg-green-100 text-green-800',
            updated: 'bg-blue-100 text-blue-800',
            deleted: 'bg-red-100 text-red-800',
            status_updated: 'bg-purple-100 text-purple-800',
            payment_recorded: 'bg-emerald-100 text-emerald-800',
            reviewed: 'bg-amber-100 text-amber-800',
        };
        return colors[ action ] || 'bg-gray-100 text-gray-800';
    };
    /**
     * @generated FunctionHeader
     * Function: renderDetails
     * Path: frontend/src/pages/dashboard/admin/AuditLogPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderDetails = (details) => {
        if (!details) return '-';

        // Attempt to make details more readable
        return (
            <div className="space-y-1">
                {Object.entries(details).map(([key, value]) => (
                    <div key={key} className="flex gap-2">
                        <span className="font-semibold capitalize">{key.replaceAll('_', ' ')}:</span>
                        <span className="truncate max-w-[200px]" title={String(value)}>
              {typeof value === 'object' ? JSON.stringify(value) : String(value)}
            </span>
                    </div>
                ))}
            </div>
        );
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Audit Logs</h1>
                    <p className="text-muted-foreground">Track all significant system activities and changes</p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchLogs} disabled={loading}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`}/>
                    Refresh
                </Button>
            </div>

            <div className="flex flex-col md:flex-row gap-4">
                <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                    <Input
                        placeholder="Search logs..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="pl-10"
                    />
                </div>
                <div className="w-full md:w-48">
                    <Select value={resourceFilter} onValueChange={setResourceFilter}>
                        <SelectTrigger>
                            <Filter className="h-4 w-4 mr-2"/>
                            <SelectValue placeholder="Filter by type"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Resources</SelectItem>
                            <SelectItem value="maintenance_request">Maintenance</SelectItem>
                            <SelectItem value="announcement">Announcements</SelectItem>
                            <SelectItem value="notice">Notices</SelectItem>
                            <SelectItem value="chat_group">Chat Groups</SelectItem>
                            <SelectItem value="levy_payment">Levy Payments</SelectItem>
                            <SelectItem value="unit_change_request">Unit Changes</SelectItem>
                            <SelectItem value="user">Users</SelectItem>
                            <SelectItem value="pet_request">Pet Requests</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
            </div>

            <Card className="card-dashboard">
                <CardContent className="p-0">
                    {loading && logs.length === 0 ? (
                        <div className="p-8 space-y-4">
                            {[1, 2, 3, 4, 5].map((i) => (
                                <div key={i} className="skeleton h-12 w-full"/>
                            ))}
                        </div>
                    ) : filteredLogs.length === 0 ? (
                        <div className="py-12 text-center">
                            <History className="h-12 w-12 text-muted-foreground/50 mx-auto mb-4"/>
                            <p className="text-muted-foreground">No audit logs found</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Time</TableHead>
                                        <TableHead>User</TableHead>
                                        <TableHead>Action</TableHead>
                                        <TableHead>Resource</TableHead>
                                        <TableHead>Details</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredLogs.map((log) => (
                                        <TableRow key={log.id}>
                                            <TableCell className="whitespace-nowrap text-sm">
                                                {formatDate(log.created_at)}
                                            </TableCell>
                                            <TableCell>
                                                <span className="font-medium">{log.user_name}</span>
                                            </TableCell>
                                            <TableCell>
                                                <Badge className={getActionBadge(log.action)}>
                                                    {log.action.replaceAll('_', ' ')}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-sm font-mono">
                                                {log.resource_type.replaceAll('_', ' ')}
                                            </TableCell>
                                            <TableCell className="max-w-md">
                                                <div className="text-xs text-muted-foreground">
                                                    {renderDetails(log.details)}
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
        </div>
    );
};

export default AuditLogPage;
