import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { AlertCircle, CheckCircle2, Clock, DollarSign, Loader2, Plus, Vote, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { proposalsApi } from '../../lib/api/community-os';

const MANAGE_ROLES = ['super_admin', 'ec_member', 'strata_manager'];
const CREATE_ROLES = [...MANAGE_ROLES, 'owner'];
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function statusBadge(status) {
    const map = {
        open: {label: 'Open', className: 'bg-green-100 text-green-800'},
        closed: {label: 'Closed', className: 'bg-gray-100 text-gray-700'},
        draft: {label: 'Draft', className: 'bg-yellow-100 text-yellow-800'},
        passed: {label: 'Passed', className: 'bg-blue-100 text-blue-800'},
        failed: {label: 'Failed', className: 'bg-red-100 text-red-800'},
    };
    const cfg = map[ status ] || {label: status, className: 'bg-gray-100 text-gray-700'};
    return <Badge className={cfg.className}>{cfg.label}</Badge>;
}
/**
 * @generated FunctionHeader
 * Function: timeRemaining
 * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function timeRemaining(closesAt) {
    if (!closesAt) return null;
    const diff = new Date(closesAt).getTime() - Date.now();
    if (diff <= 0) return 'Voting closed';
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor(( diff % 86400000 ) / 3600000);
    return days > 0 ? `${days}d ${hours}h left` : `${hours}h left`;
}
/**
 * @generated FunctionHeader
 * Function: VoteBar
 * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function VoteBar({votesFor = 0, votesAgainst = 0, votesAbstain = 0}) {
    const total = votesFor + votesAgainst + votesAbstain;
    if (total === 0) return <p className="text-xs text-muted-foreground">No votes yet</p>;
    const forPct = ( votesFor / total ) * 100;
    const againstPct = ( votesAgainst / total ) * 100;
    const abstainPct = ( votesAbstain / total ) * 100;
    return (
        <div className="space-y-1">
            <div className="flex h-2.5 w-full rounded-full overflow-hidden bg-muted">
                <div className="bg-green-500 h-full transition-all" style={{width: `${forPct}%`}}/>
                <div className="bg-red-400 h-full transition-all" style={{width: `${againstPct}%`}}/>
                <div className="bg-gray-300 h-full transition-all" style={{width: `${abstainPct}%`}}/>
            </div>
            <div className="flex gap-3 text-xs text-muted-foreground">
                <span className="flex items-center gap-0.5"><CheckCircle2
                    className="h-3 w-3 text-green-500"/>{votesFor} for</span>
                <span className="flex items-center gap-0.5"><XCircle
                    className="h-3 w-3 text-red-400"/>{votesAgainst} against</span>
                <span>{votesAbstain} abstain</span>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: ProposalsPage
 * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ProposalsPage() {
    const {user, api} = useAuth();
    const pApi = proposalsApi(api);

    const [proposals, setProposals] = useState([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('active');

    const [showCreate, setShowCreate] = useState(false);
    const [creating, setCreating] = useState(false);
    const [form, setForm] = useState({
        title: '', description: '', proposal_type: 'general', voting_type: 'simple_majority',
        fund_source: '', estimated_cost: '',
    });

    const [voteDialog, setVoteDialog] = useState(null); // { proposal, vote: 'for'|'against'|'abstain' }
    const [voting, setVoting] = useState(false);

    const isManager = MANAGE_ROLES.includes(user?.role || '');
    const canCreate = CREATE_ROLES.includes(user?.role || '');
    const isOwner = user?.role === 'owner';

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const params = activeTab === 'active' ? {status: 'open'} : {};
            const res = await pApi.list(params);
            setProposals(res.data?.proposals || res.data || []);
        } catch {
            setProposals([]);
        } finally {
            setLoading(false);
        }
    }, [activeTab]);

    useEffect(() => {
        load();
    }, [load]);
    /**
     * @generated FunctionHeader
     * Function: handleCreate
     * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreate = async (e) => {
        e.preventDefault();
        setCreating(true);
        try {
            await pApi.create({
                ...form,
                estimated_cost: form.estimated_cost ? parseInt(form.estimated_cost) * 100 : undefined,
            });
            toast.success('Proposal created');
            setShowCreate(false);
            setForm({
                title: '',
                description: '',
                proposal_type: 'general',
                voting_type: 'simple_majority',
                fund_source: '',
                estimated_cost: ''
            });
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to create proposal');
        } finally {
            setCreating(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleVote
     * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleVote = async () => {
        if (!voteDialog) return;
        setVoting(true);
        try {
            await pApi.vote(voteDialog.proposal.id, {vote: voteDialog.vote});
            toast.success('Vote recorded');
            setVoteDialog(null);
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to record vote');
        } finally {
            setVoting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleToggleStatus
     * Path: frontend/src/pages/dashboard/ProposalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleToggleStatus = async (proposal) => {
        try {
            if (proposal.status === 'open') {
                await pApi.close(proposal.id);
                toast.success('Proposal closed');
            } else {
                await pApi.open(proposal.id);
                toast.success('Proposal opened');
            }
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to update status');
        }
    };

    return (
        <div className="p-6 max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Vote className="h-6 w-6 text-purple-500"/>
                        Proposals & Voting
                    </h1>
                    <p className="text-muted-foreground text-sm">Community decision-making</p>
                </div>
                {canCreate && (
                    <Button onClick={() => setShowCreate(true)}>
                        <Plus className="h-4 w-4 mr-2"/>
                        Create Proposal
                    </Button>
                )}
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="active">Active Proposals</TabsTrigger>
                    {isManager && <TabsTrigger value="all">All Proposals</TabsTrigger>}
                </TabsList>

                <TabsContent value={activeTab} className="space-y-4 mt-4">
                    {loading ? (
                        <div className="flex justify-center py-10">
                            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
                        </div>
                    ) : proposals.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center text-muted-foreground">
                                <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50"/>
                                <p>No proposals found.</p>
                            </CardContent>
                        </Card>
                    ) : (
                        proposals.map((proposal) => {
                            const remaining = timeRemaining(proposal.voting_closes_at);
                            return (
                                <Card key={proposal.id}>
                                    <CardContent className="pt-5 space-y-3">
                                        <div className="flex items-start justify-between gap-3 flex-wrap">
                                            <div className="space-y-1">
                                                <div className="flex items-center gap-2 flex-wrap">
                                                    {proposal.proposal_number && (
                                                        <span
                                                            className="text-xs text-muted-foreground font-mono">#{proposal.proposal_number}</span>
                                                    )}
                                                    {statusBadge(proposal.status)}
                                                    {proposal.proposal_type && (
                                                        <Badge variant="outline" className="text-xs">
                                                            {proposal.proposal_type.replace(/_/g, ' ')}
                                                        </Badge>
                                                    )}
                                                </div>
                                                <h3 className="font-semibold text-base">{proposal.title}</h3>
                                                {proposal.description && (
                                                    <p className="text-sm text-muted-foreground line-clamp-2">{proposal.description}</p>
                                                )}
                                                {proposal.estimated_cost && (
                                                    <p className="text-xs flex items-center gap-1 text-muted-foreground">
                                                        <DollarSign className="h-3 w-3"/>
                                                        Est.
                                                        cost: {( proposal.estimated_cost / 100 ).toLocaleString('en-AU', {
                                                        style: 'currency',
                                                        currency: 'AUD'
                                                    })}
                                                    </p>
                                                )}
                                            </div>
                                            <div className="flex gap-2 flex-wrap">
                                                {remaining && (
                                                    <Badge variant="secondary"
                                                           className="flex items-center gap-1 text-xs">
                                                        <Clock className="h-3 w-3"/>{remaining}
                                                    </Badge>
                                                )}
                                            </div>
                                        </div>

                                        <VoteBar
                                            votesFor={proposal.votes_for}
                                            votesAgainst={proposal.votes_against}
                                            votesAbstain={proposal.votes_abstain}
                                        />

                                        <div className="flex gap-2 flex-wrap pt-1">
                                            {proposal.status === 'open' && isOwner && (
                                                <>
                                                    <Button size="sm" variant="default"
                                                            onClick={() => setVoteDialog({proposal, vote: 'for'})}>
                                                        <CheckCircle2 className="h-3 w-3 mr-1"/>Vote For
                                                    </Button>
                                                    <Button size="sm" variant="outline"
                                                            onClick={() => setVoteDialog({proposal, vote: 'against'})}>
                                                        <XCircle className="h-3 w-3 mr-1"/>Vote Against
                                                    </Button>
                                                    <Button size="sm" variant="ghost"
                                                            onClick={() => setVoteDialog({proposal, vote: 'abstain'})}>
                                                        Abstain
                                                    </Button>
                                                </>
                                            )}
                                            {isManager && (
                                                <Button size="sm" variant="outline"
                                                        onClick={() => handleToggleStatus(proposal)}>
                                                    {proposal.status === 'open' ? 'Close Voting' : 'Reopen'}
                                                </Button>
                                            )}
                                        </div>
                                    </CardContent>
                                </Card>
                            );
                        })
                    )}
                </TabsContent>
            </Tabs>

            {/* Create Proposal Dialog */}
            <Dialog open={showCreate} onOpenChange={setShowCreate}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Create Proposal</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleCreate} className="space-y-4">
                        <div className="space-y-1">
                            <Label htmlFor="p-title">Title *</Label>
                            <Input id="p-title" required value={form.title}
                                   onChange={e => setForm(f => ( {...f, title: e.target.value} ))}
                                   placeholder="Proposal title"/>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="p-desc">Description</Label>
                            <Textarea id="p-desc" value={form.description}
                                      onChange={e => setForm(f => ( {...f, description: e.target.value} ))} rows={3}
                                      placeholder="Describe the proposal..."/>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label>Proposal Type</Label>
                                <Select value={form.proposal_type}
                                        onValueChange={v => setForm(f => ( {...f, proposal_type: v} ))}>
                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="general">General</SelectItem>
                                        <SelectItem value="special_levy">Special Levy</SelectItem>
                                        <SelectItem value="by_law">By-Law Change</SelectItem>
                                        <SelectItem value="capital_works">Capital Works</SelectItem>
                                        <SelectItem value="maintenance">Maintenance</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <Label>Voting Type</Label>
                                <Select value={form.voting_type}
                                        onValueChange={v => setForm(f => ( {...f, voting_type: v} ))}>
                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="simple_majority">Simple Majority</SelectItem>
                                        <SelectItem value="special_resolution">Special Resolution</SelectItem>
                                        <SelectItem value="unanimous">Unanimous</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label htmlFor="p-fund">Fund Source</Label>
                                <Input id="p-fund" value={form.fund_source}
                                       onChange={e => setForm(f => ( {...f, fund_source: e.target.value} ))}
                                       placeholder="Admin / Sinking"/>
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="p-cost">Estimated Cost ($)</Label>
                                <Input id="p-cost" type="number" min="0" value={form.estimated_cost}
                                       onChange={e => setForm(f => ( {...f, estimated_cost: e.target.value} ))}
                                       placeholder="0"/>
                            </div>
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>Cancel</Button>
                            <Button type="submit" disabled={creating}>
                                {creating && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                                Create Proposal
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            {/* Vote Confirmation Dialog */}
            <Dialog open={!!voteDialog} onOpenChange={() => setVoteDialog(null)}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle>Confirm Vote</DialogTitle>
                    </DialogHeader>
                    {voteDialog && (
                        <div className="space-y-3">
                            <p className="text-sm">{voteDialog.proposal.title}</p>
                            <p className="text-sm">
                                You are voting <span
                                className="font-semibold capitalize text-purple-700">{voteDialog.vote}</span> this
                                proposal.
                            </p>
                            <p className="text-xs text-muted-foreground">Your vote is recorded on behalf of your
                                lot.</p>
                        </div>
                    )}
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setVoteDialog(null)}>Cancel</Button>
                        <Button onClick={handleVote} disabled={voting}>
                            {voting && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                            Confirm Vote
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
