// @featuretrace:outstanding-issues — Resident-visible governance register with EC-maintained issue tracking.
// Layer: frontend
// Data flow: OutstandingIssuesPage.jsx → /outstanding-issues → db.outstanding_issues (building-scoped).
// Related: backend/server.py outstanding issues routes
//          frontend/src/components/layout/DashboardLayout.tsx
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../components/ui/table';
import { Textarea } from '../../components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { ClipboardList, Pencil, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

const STATUS_OPTIONS = [
    {value: 'all_good', label: 'All good', className: 'bg-green-100 text-green-800 border-green-200'},
    {value: 'in_progress', label: 'In progress', className: 'bg-blue-100 text-blue-800 border-blue-200'},
    {value: 'not_good', label: 'Not good', className: 'bg-red-100 text-red-800 border-red-200'},
    {value: 'ec', label: 'EC', className: 'bg-purple-100 text-purple-800 border-purple-200'},
    {value: 'all_else', label: 'All else', className: 'bg-slate-100 text-slate-800 border-slate-200'},
];

const EMPTY_FORM = {
    issue: '',
    details: '',
    status: 'in_progress',
    updates_notes: '',
    strata_web_meeting_tba: '',
    chair_notes: '',
};

const EDITOR_ROLES = new Set(['super_admin', 'ec_member']);
/**
 * @generated FunctionHeader
 * Function: OutstandingIssuesPage
 * Path: frontend/src/pages/dashboard/OutstandingIssuesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function OutstandingIssuesPage() {
    const {api, user} = useAuth();
    const [issues, setIssues] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingIssue, setEditingIssue] = useState(null);
    const [formState, setFormState] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);

    const canEdit = EDITOR_ROLES.has(user?.role || '');

    const statusMap = useMemo(
        () => Object.fromEntries(STATUS_OPTIONS.map(option => [option.value, option])),
        []
    );

    const fetchIssues = useCallback(async () => {
        try {
            setLoading(true);
            const response = await api.get('/outstanding-issues');
            setIssues(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch outstanding issues:', error);
            toast.error('Failed to load the outstanding issues register');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchIssues();
    }, [fetchIssues]);
    /**
     * @generated FunctionHeader
     * Function: openCreateDialog
     * Path: frontend/src/pages/dashboard/OutstandingIssuesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCreateDialog = () => {
        setEditingIssue(null);
        setFormState(EMPTY_FORM);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openEditDialog
     * Path: frontend/src/pages/dashboard/OutstandingIssuesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditDialog = (issue) => {
        setEditingIssue(issue);
        setFormState({
            issue: issue.issue || '',
            details: issue.details || '',
            status: issue.status || 'in_progress',
            updates_notes: issue.updates_notes || '',
            strata_web_meeting_tba: issue.strata_web_meeting_tba || '',
            chair_notes: issue.chair_notes || '',
        });
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/OutstandingIssuesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        if (!formState.issue.trim() || !formState.details.trim()) {
            toast.error('Issue and details are required');
            return;
        }

        const payload = {
            ...formState,
            issue: formState.issue.trim(),
            details: formState.details.trim(),
            updates_notes: formState.updates_notes.trim() || null,
            strata_web_meeting_tba: formState.strata_web_meeting_tba.trim() || null,
            chair_notes: formState.chair_notes.trim() || null,
        };

        try {
            setSaving(true);
            if (editingIssue) {
                await api.put(`/outstanding-issues/${editingIssue.id}`, payload);
                toast.success('Issue register entry updated');
            } else {
                await api.post('/outstanding-issues', payload);
                toast.success('Issue register entry created');
            }
            setDialogOpen(false);
            fetchIssues();
        } catch (error) {
            console.error('Failed to save outstanding issue:', error);
            toast.error(error.response?.data?.detail || 'Failed to save issue register entry');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Outstanding Issues Register</h1>
                    <p className="text-muted-foreground">
                        Shared building-wide register for live issues, EC updates, Strata Web meeting follow-ups, and chair
                        notes.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={fetchIssues}>
                        <RefreshCw className="mr-2 h-4 w-4"/>
                        Refresh
                    </Button>
                    {canEdit && (
                        <Button onClick={openCreateDialog}>
                            <Plus className="mr-2 h-4 w-4"/>
                            New Issue
                        </Button>
                    )}
                </div>
            </div>

            <Card className="border-blue-200 bg-blue-50">
                <CardHeader className="pb-3">
                    <CardTitle className="text-base">Status legend</CardTitle>
                    <CardDescription>
                        Colour coding used throughout the register.
                    </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                    {STATUS_OPTIONS.map(option => (
                        <Badge key={option.value} variant="outline" className={option.className}>
                            {option.label}
                        </Badge>
                    ))}
                </CardContent>
            </Card>

            <Card>
                <CardContent className="pt-6">
                    {loading ? (
                        <div className="py-12 text-center">Loading...</div>
                    ) : issues.length === 0 ? (
                        <div className="py-16 text-center text-muted-foreground">
                            <ClipboardList className="mx-auto mb-4 h-12 w-12 opacity-50"/>
                            <p>No outstanding issues are currently listed.</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>#</TableHead>
                                        <TableHead>Issue</TableHead>
                                        <TableHead>Details</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Updates / Notes</TableHead>
                                        <TableHead>Strata Web meeting TBA</TableHead>
                                        <TableHead>Chair Notes</TableHead>
                                        {canEdit && <TableHead className="text-right">Action</TableHead>}
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {issues.map((issue, index) => (
                                        <TableRow key={issue.id}>
                                            <TableCell className="font-mono text-xs">{index + 1}</TableCell>
                                            <TableCell className="font-medium min-w-[180px]">{issue.issue}</TableCell>
                                            <TableCell
                                                className="min-w-[240px] whitespace-pre-wrap">{issue.details}</TableCell>
                                            <TableCell>
                                                <Badge variant="outline"
                                                       className={statusMap[ issue.status ]?.className || ''}>
                                                    {statusMap[ issue.status ]?.label || issue.status}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="min-w-[220px] whitespace-pre-wrap">
                                                {issue.updates_notes || ' - '}
                                            </TableCell>
                                            <TableCell className="min-w-[180px] whitespace-pre-wrap">
                                                {issue.strata_web_meeting_tba || ' - '}
                                            </TableCell>
                                            <TableCell className="min-w-[220px] whitespace-pre-wrap">
                                                {issue.chair_notes || ' - '}
                                            </TableCell>
                                            {canEdit && (
                                                <TableCell className="text-right">
                                                    <Button variant="outline" size="sm"
                                                            onClick={() => openEditDialog(issue)}>
                                                        <Pencil className="mr-2 h-4 w-4"/>
                                                        Edit
                                                    </Button>
                                                </TableCell>
                                            )}
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-w-3xl">
                    <DialogHeader>
                        <DialogTitle>{editingIssue ? 'Edit issue register entry' : 'New issue register entry'}</DialogTitle>
                        <DialogDescription>
                            Record the current state of a building issue and any follow-up notes for the wider resident
                            community.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div>
                            <Label htmlFor="issue-title">Issue</Label>
                            <Input
                                id="issue-title"
                                value={formState.issue}
                                onChange={e => setFormState(prev => ( {...prev, issue: e.target.value} ))}
                                className="mt-2"
                                placeholder="Short issue title"
                            />
                        </div>

                        <div>
                            <Label htmlFor="issue-details">Details</Label>
                            <Textarea
                                id="issue-details"
                                value={formState.details}
                                onChange={e => setFormState(prev => ( {...prev, details: e.target.value} ))}
                                className="mt-2"
                                rows={4}
                                placeholder="Describe the issue, impact, and current context"
                            />
                        </div>

                        <div>
                            <Label>Status</Label>
                            <Select
                                value={formState.status}
                                onValueChange={value => setFormState(prev => ( {...prev, status: value} ))}
                            >
                                <SelectTrigger className="mt-2">
                                    <SelectValue placeholder="Select status"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {STATUS_OPTIONS.map(option => (
                                        <SelectItem key={option.value} value={option.value}>
                                            {option.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div>
                            <Label htmlFor="issue-updates">Updates / Notes</Label>
                            <Textarea
                                id="issue-updates"
                                value={formState.updates_notes}
                                onChange={e => setFormState(prev => ( {...prev, updates_notes: e.target.value} ))}
                                className="mt-2"
                                rows={4}
                                placeholder="Latest progress update or note"
                            />
                        </div>

                        <div>
                            <Label htmlFor="issue-strata_web">Strata Web meeting TBA</Label>
                            <Input
                                id="issue-strata_web"
                                value={formState.strata_web_meeting_tba}
                                onChange={e => setFormState(prev => ( {...prev, strata_web_meeting_tba: e.target.value} ))}
                                className="mt-2"
                                placeholder="Next Strata Web discussion date or placeholder"
                            />
                        </div>

                        <div>
                            <Label htmlFor="issue-chair-notes">Chair Notes</Label>
                            <Textarea
                                id="issue-chair-notes"
                                value={formState.chair_notes}
                                onChange={e => setFormState(prev => ( {...prev, chair_notes: e.target.value} ))}
                                className="mt-2"
                                rows={4}
                                placeholder="Chair-specific comments or decisions"
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>
                            Cancel
                        </Button>
                        <Button onClick={handleSave} disabled={saving}>
                            {saving ? 'Saving...' : editingIssue ? 'Save Changes' : 'Create Entry'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
