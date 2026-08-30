// @featuretrace:decisions — Formal OC/EC resolution register with DEC-YYYY-NNNN numbering.
// Layer: frontend
// Data flow: DecisionRegisterPage.jsx → /decisions → db.decisions (building-scoped).
// Related: backend/routers/decisions.py
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { FileCheck, Loader2, Plus, Search, X } from 'lucide-react';
import { toast } from 'sonner';

const MEETING_TYPES = ['agm', 'egm', 'ec_meeting', 'special'];
const RESOLUTION_TYPES = ['ordinary', 'special', 'unopposed', 'unanimous'];
const DECISION_RESULTS = ['passed', 'failed', 'lapsed', 'deferred'];

const RESULT_BADGE = {
    passed: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
    lapsed: 'bg-yellow-100 text-yellow-800',
    deferred: 'bg-blue-100 text-blue-800',
};

const MEETING_LABEL = {
    agm: 'AGM', egm: 'EGM', ec_meeting: 'EC Meeting', special: 'Special Meeting',
};

const emptyForm = {
    meeting_type: 'ec_meeting',
    meeting_date: '',
    motion_title: '',
    motion_text: '',
    resolution_type: 'ordinary',
    vote_for: '',
    vote_against: '',
    vote_abstain: '',
    result: 'passed',
    proposer: '',
    seconder: '',
    notes: '',
    tags: '',
};
/**
 * @generated FunctionHeader
 * Function: DecisionRegisterPage
 * Path: frontend/src/pages/dashboard/DecisionRegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const DecisionRegisterPage = () => {
    const {api, user, isManager} = useAuth();
    const [decisions, setDecisions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [formData, setFormData] = useState(emptyForm);
    const [search, setSearch] = useState('');
    const [filterResult, setFilterResult] = useState('');
    const [filterType, setFilterType] = useState('');
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const PAGE_SIZE = 20;

    const canCreate = isManager();

    const fetchDecisions = useCallback(async () => {
        try {
            setLoading(true);
            let url = `/decisions?page=${page}&page_size=${PAGE_SIZE}`;
            if (filterResult) url += `&result=${filterResult}`;
            if (filterType) url += `&meeting_type=${filterType}`;
            const res = await api.get(url);
            setDecisions(res.data?.data || []);
            setTotal(res.data?.total || 0);
        } catch {
            toast.error('Failed to load decisions');
        } finally {
            setLoading(false);
        }
    }, [api, page, filterResult, filterType]);

    const searchDecisions = useCallback(async () => {
        if (!search.trim()) {
            fetchDecisions();
            return;
        }
        try {
            setLoading(true);
            const res = await api.get(`/decisions/search?q=${encodeURIComponent(search)}&page_size=${PAGE_SIZE}`);
            setDecisions(res.data?.data || []);
            setTotal(res.data?.total || 0);
        } catch {
            toast.error('Search failed');
        } finally {
            setLoading(false);
        }
    }, [api, search, fetchDecisions]);

    useEffect(() => {
        fetchDecisions();
    }, [fetchDecisions]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/DecisionRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.meeting_date || !formData.motion_title || !formData.motion_text) {
            toast.error('Meeting date, motion title, and motion text are required');
            return;
        }
        setSubmitting(true);
        try {
            const payload = {
                ...formData,
                building_id: user?.building_id,
                vote_for: formData.vote_for ? parseInt(formData.vote_for) : undefined,
                vote_against: formData.vote_against ? parseInt(formData.vote_against) : undefined,
                vote_abstain: formData.vote_abstain ? parseInt(formData.vote_abstain) : undefined,
                tags: formData.tags ? formData.tags.split(',').map(t => t.trim()).filter(Boolean) : [],
            };
            await api.post('/decisions', payload);
            toast.success('Decision recorded');
            setDialogOpen(false);
            setFormData(emptyForm);
            fetchDecisions();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to record decision');
        } finally {
            setSubmitting(false);
        }
    };

    const totalPages = Math.ceil(total / PAGE_SIZE);

    return (
        <div className="p-6 max-w-5xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <h1 className="text-2xl font-bold text-[#2F4F4F] flex items-center gap-2">
                        <FileCheck className="h-6 w-6"/> Decision Register
                    </h1>
                    <p className="text-sm text-gray-500 mt-1">
                        Formal owners corporation and EC resolutions — ACT UTMA 2011 / NSW SSMA 2015
                    </p>
                </div>
                {canCreate && (
                    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                        <DialogTrigger asChild>
                            <Button className="rounded-full bg-[#2F4F4F] hover:bg-[#1a3333] active:scale-95">
                                <Plus className="h-4 w-4 mr-2"/> Record Decision
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                            <DialogHeader>
                                <DialogTitle>Record a Formal Decision</DialogTitle>
                                <DialogDescription>
                                    Auto-assigns a DEC-YYYY-NNNN reference number.
                                </DialogDescription>
                            </DialogHeader>
                            <form onSubmit={handleSubmit} className="space-y-4 pt-2">
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <Label>Meeting Type *</Label>
                                        <select
                                            className="w-full border rounded px-3 py-2 text-sm mt-1"
                                            value={formData.meeting_type}
                                            onChange={e => setFormData(f => ( {...f, meeting_type: e.target.value} ))}
                                        >
                                            {MEETING_TYPES.map(t => (
                                                <option key={t} value={t}>{MEETING_LABEL[ t ] || t}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div>
                                        <Label>Meeting Date *</Label>
                                        <Input
                                            type="date"
                                            className="mt-1"
                                            value={formData.meeting_date}
                                            onChange={e => setFormData(f => ( {...f, meeting_date: e.target.value} ))}
                                            required
                                        />
                                    </div>
                                </div>

                                <div>
                                    <Label>Motion Title *</Label>
                                    <Input
                                        className="mt-1"
                                        placeholder="e.g. Approve 2026 Administration Fund Budget"
                                        value={formData.motion_title}
                                        onChange={e => setFormData(f => ( {...f, motion_title: e.target.value} ))}
                                        required
                                    />
                                </div>

                                <div>
                                    <Label>Full Motion Text *</Label>
                                    <Textarea
                                        className="mt-1"
                                        rows={4}
                                        placeholder="Full text of the motion as read at the meeting…"
                                        value={formData.motion_text}
                                        onChange={e => setFormData(f => ( {...f, motion_text: e.target.value} ))}
                                        required
                                    />
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <Label>Resolution Type *</Label>
                                        <select
                                            className="w-full border rounded px-3 py-2 text-sm mt-1"
                                            value={formData.resolution_type}
                                            onChange={e => setFormData(f => ( {
                                                ...f,
                                                resolution_type: e.target.value
                                            } ))}
                                        >
                                            {RESOLUTION_TYPES.map(t => (
                                                <option key={t}
                                                        value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div>
                                        <Label>Result *</Label>
                                        <select
                                            className="w-full border rounded px-3 py-2 text-sm mt-1"
                                            value={formData.result}
                                            onChange={e => setFormData(f => ( {...f, result: e.target.value} ))}
                                        >
                                            {DECISION_RESULTS.map(r => (
                                                <option key={r}
                                                        value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>

                                <div className="grid grid-cols-3 gap-4">
                                    {[['vote_for', 'Votes For'], ['vote_against', 'Votes Against'], ['vote_abstain', 'Abstentions']].map(([k, label]) => (
                                        <div key={k}>
                                            <Label>{label}</Label>
                                            <Input
                                                type="number"
                                                min={0}
                                                className="mt-1"
                                                value={formData[ k ]}
                                                onChange={e => setFormData(f => ( {...f, [ k ]: e.target.value} ))}
                                            />
                                        </div>
                                    ))}
                                </div>

                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <Label>Proposer</Label>
                                        <Input className="mt-1" value={formData.proposer}
                                               onChange={e => setFormData(f => ( {...f, proposer: e.target.value} ))}/>
                                    </div>
                                    <div>
                                        <Label>Seconder</Label>
                                        <Input className="mt-1" value={formData.seconder}
                                               onChange={e => setFormData(f => ( {...f, seconder: e.target.value} ))}/>
                                    </div>
                                </div>

                                <div>
                                    <Label>Tags (comma-separated)</Label>
                                    <Input
                                        className="mt-1"
                                        placeholder="finance, maintenance, bylaw"
                                        value={formData.tags}
                                        onChange={e => setFormData(f => ( {...f, tags: e.target.value} ))}
                                    />
                                </div>

                                <div>
                                    <Label>Notes</Label>
                                    <Textarea className="mt-1" rows={2} value={formData.notes}
                                              onChange={e => setFormData(f => ( {...f, notes: e.target.value} ))}/>
                                </div>

                                <div className="flex justify-end gap-2 pt-2">
                                    <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
                                        Cancel
                                    </Button>
                                    <Button type="submit" disabled={submitting}
                                            className="rounded-full bg-[#2F4F4F] hover:bg-[#1a3333]">
                                        {submitting ? <Loader2 className="h-4 w-4 animate-spin"/> : 'Record Decision'}
                                    </Button>
                                </div>
                            </form>
                        </DialogContent>
                    </Dialog>
                )}
            </div>

            {/* Filters */}
            <Card>
                <CardContent className="pt-4 pb-3">
                    <div className="flex gap-3 flex-wrap items-end">
                        <div className="flex-1 min-w-48">
                            <div className="relative">
                                <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400"/>
                                <Input
                                    className="pl-9"
                                    placeholder="Search decisions…"
                                    value={search}
                                    onChange={e => setSearch(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && searchDecisions()}
                                    data-testid="decision-search"
                                />
                            </div>
                        </div>
                        <Button variant="outline" size="sm" onClick={searchDecisions}>Search</Button>
                        <select
                            className="border rounded px-3 py-2 text-sm"
                            value={filterResult}
                            onChange={e => {
                                setFilterResult(e.target.value);
                                setPage(1);
                            }}
                        >
                            <option value="">All results</option>
                            {DECISION_RESULTS.map(r => <option key={r} value={r}>{r}</option>)}
                        </select>
                        <select
                            className="border rounded px-3 py-2 text-sm"
                            value={filterType}
                            onChange={e => {
                                setFilterType(e.target.value);
                                setPage(1);
                            }}
                        >
                            <option value="">All meeting types</option>
                            {MEETING_TYPES.map(t => <option key={t} value={t}>{MEETING_LABEL[ t ]}</option>)}
                        </select>
                        {( search || filterResult || filterType ) && (
                            <Button variant="ghost" size="sm" onClick={() => {
                                setSearch('');
                                setFilterResult('');
                                setFilterType('');
                                setPage(1);
                                fetchDecisions();
                            }}>
                                <X className="h-4 w-4 mr-1"/> Clear
                            </Button>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* Results */}
            {loading ? (
                <div className="flex justify-center py-16">
                    <Loader2 className="h-8 w-8 animate-spin text-[#2F4F4F]"/>
                </div>
            ) : decisions.length === 0 ? (
                <Card>
                    <CardContent className="py-16 text-center text-gray-500">
                        <FileCheck className="h-12 w-12 mx-auto mb-3 text-gray-300"/>
                        <p className="font-medium">No decisions recorded yet</p>
                        <p className="text-sm mt-1">
                            {canCreate ? 'Use "Record Decision" to log a formal OC or EC resolution.' : 'No decisions have been recorded for your building.'}
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-3" data-testid="decisions-list">
                    {decisions.map(d => (
                        <Card key={d.id || d._id} className="border shadow-sm hover:shadow-md transition-shadow">
                            <CardContent className="pt-4 pb-4">
                                <div className="flex items-start justify-between gap-3 flex-wrap">
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap mb-1">
                                            <span className="font-mono text-xs text-gray-400 font-medium">
                                                {d.decision_number}
                                            </span>
                                            <Badge variant="outline" className="text-xs">
                                                {MEETING_LABEL[ d.meeting_type ] || d.meeting_type}
                                            </Badge>
                                            <Badge variant="outline" className="text-xs capitalize">
                                                {d.resolution_type}
                                            </Badge>
                                        </div>
                                        <p className="font-semibold text-gray-900 truncate">{d.motion_title}</p>
                                        <p className="text-sm text-gray-500 mt-0.5">
                                            {d.meeting_date?.slice(0, 10)}
                                            {d.proposer && <> · Moved: {d.proposer}</>}
                                            {d.seconder && <> · Seconded: {d.seconder}</>}
                                        </p>
                                        {d.vote_for != null && (
                                            <p className="text-xs text-gray-400 mt-1">
                                                For: {d.vote_for} · Against: {d.vote_against ?? 0} ·
                                                Abstain: {d.vote_abstain ?? 0}
                                            </p>
                                        )}
                                        {d.tags?.length > 0 && (
                                            <div className="flex gap-1 flex-wrap mt-2">
                                                {d.tags.map(tag => (
                                                    <span key={tag}
                                                          className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                                                        {tag}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                    <Badge
                                        className={`shrink-0 capitalize ${RESULT_BADGE[ d.result ] || 'bg-gray-100 text-gray-700'}`}>
                                        {d.result}
                                    </Badge>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            {/* Pagination */}
            {totalPages > 1 && (
                <div className="flex justify-center gap-2 pt-2">
                    <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                        Previous
                    </Button>
                    <span className="py-2 px-3 text-sm text-gray-500">
                        Page {page} of {totalPages} ({total} decisions)
                    </span>
                    <Button variant="outline" size="sm" disabled={page >= totalPages}
                            onClick={() => setPage(p => p + 1)}>
                        Next
                    </Button>
                </div>
            )}
        </div>
    );
};

export default DecisionRegisterPage;
