"use client";
import React, {useEffect, useState} from 'react';
import {useAuth} from '@/contexts/AuthContext';
import {Badge} from '@/components/ui/badge';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {Building2, ExternalLink, FileText, Hash, Users} from 'lucide-react';
import {useRouter} from 'next/navigation';
/**
 * @generated FunctionHeader
 * Function: TechDocsPage
 * Path: frontend/src/app/(app)/admin/tech-docs/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TechDocsPage() {
    const {isAdmin, loading, selectedBuilding, api} = useAuth() as any;
    const router = useRouter();
    const [mounted, setMounted] = useState(false);
    const [unitCount, setUnitCount] = useState<number | null>(null);
    const [financialYear, setFinancialYear] = useState<string>('');

    useEffect(() => {
        setMounted(true);
    }, []);

    useEffect(() => {
        if (!mounted || loading) return;
        if (!isAdmin()) {
            router.replace('/dashboard');
            return;
        }
        // Fetch building stats for context banner
        if (!api) return;
        api.get('/settings').then((res: any) => {
            const d = res.data || {};
            if (d.current_financial_year) setFinancialYear(d.current_financial_year);
        }).catch(() => {
        });
        api.get('/units?limit=1').then((res: any) => {
            const total = res.headers?.['x-total-count'] || res.data?.total;
            if (total) setUnitCount(Number(total));
        }).catch(() => {
        });
    }, [mounted, api, isAdmin, router]);

    // mounted guard prevents SSR/client hydration mismatch — auth state is client-only
    if (!mounted || loading || !isAdmin()) return null;

    const buildingName = selectedBuilding?.name ?? 'All Buildings';
    const buildingId = selectedBuilding?.id ?? '—';

    return (
        <div className="flex flex-col h-full space-y-4 p-6">
            {/* Building context banner */}
            <Card className="border-primary/20 bg-primary/5 shrink-0">
                <CardHeader className="pb-2 pt-4">
                    <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                        <FileText className="h-4 w-4 text-primary"/>
                        Technical Documentation — Building Context
                    </CardTitle>
                </CardHeader>
                <CardContent className="flex flex-wrap items-center gap-6 pb-4">
                    <div className="flex items-center gap-2">
                        <Building2 className="h-4 w-4 text-muted-foreground"/>
                        <div>
                            <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Building</p>
                            <p className="font-bold text-sm">{buildingName}</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <Hash className="h-4 w-4 text-muted-foreground"/>
                        <div>
                            <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Building ID</p>
                            <Badge variant="outline" className="font-mono text-xs mt-0.5">{buildingId}</Badge>
                        </div>
                    </div>
                    {unitCount !== null && (
                        <div className="flex items-center gap-2">
                            <Users className="h-4 w-4 text-muted-foreground"/>
                            <div>
                                <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Units</p>
                                <p className="font-bold text-sm">{unitCount}</p>
                            </div>
                        </div>
                    )}
                    {financialYear && (
                        <div>
                            <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Financial Year</p>
                            <p className="font-bold text-sm">{financialYear}</p>
                        </div>
                    )}
                    <a
                        href="/tech-docs/index.html"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="ml-auto flex items-center gap-1 text-xs text-primary hover:underline"
                    >
                        Open in new tab <ExternalLink className="h-3 w-3"/>
                    </a>
                </CardContent>
            </Card>

            {/* Embedded docs */}
            <iframe
                src="/tech-docs/index.html"
                className="flex-1 w-full rounded-lg border min-h-[72vh] bg-white"
                title={`Technical Documentation — ${buildingName}`}
            />
        </div>
    );
}
