"use client";

import React from 'react';
import {useAuth} from '../../contexts/AuthContext';
import {useRouter} from 'next/navigation';
import {Loader2, ShieldAlert} from 'lucide-react';
import {Card, CardContent} from '../ui/card';
import {Button} from '../ui/button';

interface FeatureGuardProps {
    featureKey: string;
    children: React.ReactNode;
    fallback?: React.ReactNode;
}
/**
 * @generated FunctionHeader
 * Function: FeatureGuard
 * Path: frontend/src/components/layout/FeatureGuard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const FeatureGuard: React.FC<FeatureGuardProps> = ({
                                                              featureKey,
                                                              children,
                                                              fallback
                                                          }) => {
    const {hasFeatureAccess, loading, selectedBuilding} = useAuth();
    const router = useRouter();

    const hasAccess = hasFeatureAccess(featureKey);
    const buildingName = selectedBuilding?.name || 'your building';

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }

    if (!hasAccess) {
        if (fallback) return <>{fallback}</>;

        return (
            <div className="flex items-center justify-center min-h-[400px] p-6">
                <Card className="max-w-md w-full border-2 border-dashed border-destructive/50 bg-destructive/5">
                    <CardContent className="pt-6 text-center space-y-4">
                        <div
                            className="mx-auto w-12 h-12 rounded-full bg-destructive/10 flex items-center justify-center">
                            <ShieldAlert className="h-6 w-6 text-destructive"/>
                        </div>
                        <div className="space-y-2">
                            <h3 className="text-lg font-bold">Feature not available for {buildingName}</h3>
                            <p className="text-sm text-muted-foreground">
                                Please refer to your{' '}
                                <a
                                    href="https://my.civiumstrata.com.au"
                                    target="_blank"
                                    rel="noreferrer"
                                    className="font-semibold text-primary underline underline-offset-2"
                                >
                                    Strata Website
                                </a>
                                .
                            </p>
                        </div>
                        <Button
                            variant="outline"
                            onClick={() => router.push('/dashboard')}
                            className="w-full"
                        >
                            Return to Dashboard
                        </Button>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return <>{children}</>;
};

export default FeatureGuard;
