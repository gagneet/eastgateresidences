import type {Metadata, ResolvingMetadata} from 'next';
import {Suspense} from 'react';
import BuildingPublicPage from '@/pages/public/BuildingPublicPage';

const BACKEND_URL =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    'http://localhost:8003';

type Props = {
    params: Promise<{ slug: string }>;
};
/**
 * @generated FunctionHeader
 * Function: generateMetadata
 * Path: frontend/src/app/(public)/building/[slug]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export async function generateMetadata(
    {params}: Props,
    _parent: ResolvingMetadata
): Promise<Metadata> {
    const {slug} = await params;

    try {
        const response = await fetch(
            `${BACKEND_URL}/api/intelligence/risk-index/public/${slug}`,
            {next: {revalidate: 3600}}
        );
        if (!response.ok) {
            return {
                title: 'Building Intelligence Report',
                description: 'View building risk, financial health, and maintenance forecast.',
            };
        }
        const report = await response.json();

        const buildingName: string = report.building_name ?? 'Building';
        const riskLabel: string = (report.risk_level ?? 'unknown').replace(/^\w/, (c: string) => c.toUpperCase());
        const title = `${buildingName} — Building Intelligence Report`;
        const description =
            `${buildingName} has a ${riskLabel} risk score of ${Math.round(report.risk_score ?? 0)}/100. ` +
            `${report.financial_health_summary ?? ''} ${report.maintenance_forecast_summary ?? ''}`.trim();

        return {
            title,
            description,
            keywords: [
                buildingName,
                'strata risk score',
                'building intelligence',
                'building risk index',
                'capital replacement',
                'sinking fund',
                'maintenance forecast',
            ],
            openGraph: {
                title,
                description,
                type: 'website',
                images: ['/images/east_gate_residences.jpg'],
            },
            twitter: {
                card: 'summary',
                title,
                description,
            },
            alternates: {
                canonical: `/building/${slug}`,
            },
        };
    } catch {
        return {
            title: 'Building Intelligence Report',
            description: 'View building risk, financial health, and maintenance forecast.',
        };
    }
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(public)/building/[slug]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default async function Page({params}: Props) {
    const {slug} = await params;
    return (
        <Suspense
            fallback={
                <div className="max-w-3xl mx-auto px-4 py-12 space-y-6 animate-pulse">
                    <div className="h-8 w-64 bg-gray-200 rounded"/>
                    <div className="h-4 w-48 bg-gray-200 rounded"/>
                </div>
            }
        >
            <BuildingPublicPage slug={slug}/>
        </Suspense>
    );
}
