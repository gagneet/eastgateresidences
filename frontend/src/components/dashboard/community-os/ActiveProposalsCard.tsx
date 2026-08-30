"use client";
import React from "react";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Loader2, Vote} from "lucide-react";
import Link from "next/link";

interface Proposal {
    id: string;
    proposal_number?: string;
    title: string;
    proposal_type?: string;
    status?: string;
    votes_for?: number;
    votes_against?: number;
    votes_abstain?: number;
    voting_closes_at?: string;
}

interface ActiveProposalsCardProps {
    proposals?: Proposal[];
    loading?: boolean;
    isOwner?: boolean;
}
/**
 * @generated FunctionHeader
 * Function: timeRemaining
 * Path: frontend/src/components/community-os/ActiveProposalsCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function timeRemaining(closesAt?: string): string | null {
    if (!closesAt) return null;
    const diff = new Date(closesAt).getTime() - Date.now();
    if (diff <= 0) return "Closed";
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    if (days > 0) return `${days}d ${hours}h left`;
    return `${hours}h left`;
}
/**
 * @generated FunctionHeader
 * Function: ActiveProposalsCard
 * Path: frontend/src/components/community-os/ActiveProposalsCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ActiveProposalsCard({
                                                proposals = [],
                                                loading = false,
                                                isOwner = false,
                                            }: ActiveProposalsCardProps) {
    if (loading) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm font-medium">Active Proposals</CardTitle>
                </CardHeader>
                <CardContent className="flex items-center justify-center py-10">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader className="pb-2 flex flex-row items-center justify-between">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                    <Vote className="h-4 w-4 text-purple-500"/>
                    Active Proposals
                </CardTitle>
                <Link href="/governance/proposals">
                    <Button variant="ghost" size="sm" className="text-xs h-7">View all</Button>
                </Link>
            </CardHeader>
            <CardContent className="space-y-3">
                {proposals.length === 0 && (
                    <p className="text-sm text-muted-foreground">No active proposals.</p>
                )}
                {proposals.map((p) => {
                    const total = (p.votes_for ?? 0) + (p.votes_against ?? 0) + (p.votes_abstain ?? 0);
                    const forPct = total > 0 ? ((p.votes_for ?? 0) / total) * 100 : 0;
                    const againstPct = total > 0 ? ((p.votes_against ?? 0) / total) * 100 : 0;
                    const remaining = timeRemaining(p.voting_closes_at);

                    return (
                        <div key={p.id} className="space-y-1.5 border rounded-md p-3">
                            <div className="flex items-start justify-between gap-2">
                                <div className="space-y-0.5 flex-1 min-w-0">
                                    <p className="text-sm font-medium truncate">{p.title}</p>
                                    <div className="flex items-center gap-1.5 flex-wrap">
                                        {p.proposal_type && (
                                            <Badge variant="outline" className="text-xs py-0">
                                                {p.proposal_type.replace(/_/g, " ")}
                                            </Badge>
                                        )}
                                        {remaining && (
                                            <Badge variant="secondary" className="text-xs py-0">
                                                {remaining}
                                            </Badge>
                                        )}
                                    </div>
                                </div>
                            </div>

                            {total > 0 && (
                                <div className="space-y-0.5">
                                    <div className="flex h-2 w-full rounded-full overflow-hidden bg-muted">
                                        <div className="bg-green-500 h-full" style={{width: `${forPct}%`}}/>
                                        <div className="bg-red-400 h-full" style={{width: `${againstPct}%`}}/>
                                    </div>
                                    <div className="flex justify-between text-xs text-muted-foreground">
                                        <span>{p.votes_for ?? 0} for</span>
                                        <span>{p.votes_against ?? 0} against</span>
                                    </div>
                                </div>
                            )}

                            <Link href={`/governance/proposals`}>
                                <Button size="sm" variant={isOwner ? "default" : "outline"}
                                        className="h-7 text-xs w-full mt-1">
                                    {isOwner ? "Vote now" : "View"}
                                </Button>
                            </Link>
                        </div>
                    );
                })}
            </CardContent>
        </Card>
    );
}
