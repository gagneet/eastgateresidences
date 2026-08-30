import React, { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Building2, ChevronRight, Loader2, LogOut } from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: SelectBuildingPage
 * Path: frontend/src/pages/auth/SelectBuildingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SelectBuildingPage = () => {
    const router = useRouter();
    const {user, availableBuildings, selectedBuilding, switchBuilding, loading, logout} = useAuth();

    useEffect(() => {
        // If not loading and no user, go to login
        if (!loading && !user) {
            router.push('/login');
        }

        // If only one building, the AuthContext should have auto-selected it,
        // but as a safety, if we're here and there's only one, just go to dashboard.
        // EXCEPT: if we landed here because a switch-building call 404'd (stale id
        // post-cutover), the lone building is the same one that just failed — auto-
        // redirecting back to /dashboard would create an infinite redirect loop.
        // Honour the sessionStorage flag set by AuthContext and clear it.
        if (!loading && availableBuildings.length === 1) {
            let cameFromStaleRedirect = false;
            try {
                cameFromStaleRedirect = sessionStorage.getItem('staleBuildingRedirect') === '1';
                if (cameFromStaleRedirect) sessionStorage.removeItem('staleBuildingRedirect');
            } catch { /* ignore */
            }
            if (!cameFromStaleRedirect) router.push('/dashboard');
        }
    }, [user, loading, availableBuildings, router]);

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-muted/30">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: handleSelect
     * Path: frontend/src/pages/auth/SelectBuildingPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSelect = async (buildingId) => {
        await switchBuilding(buildingId, { redirectTo: false });
        router.push('/dashboard');
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12">
            <div className="w-full max-w-2xl">
                <div className="text-center mb-8">
                    <div className="inline-flex items-center justify-center h-16 w-16 rounded-full bg-primary/10 mb-4">
                        <Building2 className="h-8 w-8 text-primary"/>
                    </div>
                    <h1 className="text-3xl font-bold tracking-tight">Select your building</h1>
                    <p className="text-muted-foreground mt-2 text-lg">
                        Welcome back, {user?.full_name}. Please choose which property you'd like to manage today.
                    </p>
                </div>

                {availableBuildings.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center">
                            <p className="text-base font-semibold mb-2">No buildings available</p>
                            <p className="text-sm text-muted-foreground">
                                Your previously selected building is no longer accessible, and no other
                                buildings are assigned to your account. Contact your strata administrator,
                                or sign out and use a different account.
                            </p>
                        </CardContent>
                    </Card>
                ) : (
                    <div role="list" className="grid gap-4 sm:grid-cols-1">
                        {availableBuildings.map((building) => {
                            const isSelected = selectedBuilding?.id === building.id;
                            return (
                                <Card
                                    key={building.id}
                                    role="listitem"
                                    tabIndex={0}
                                    aria-label={`Select ${building.name}, plan ${building.building_id}`}
                                    aria-current={isSelected ? 'true' : undefined}
                                    data-testid={`select-building-${building.id}`}
                                    className={`hover:border-primary/50 transition-all cursor-pointer group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ${isSelected ? 'border-primary bg-primary/5' : ''}`}
                                    onClick={() => handleSelect(building.id)}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter' || e.key === ' ') {
                                            e.preventDefault();
                                            handleSelect(building.id);
                                        }
                                    }}
                                >
                                    <CardContent className="p-6 flex items-center justify-between gap-4">
                                        <div className="flex min-w-0 items-center gap-4">
                                            <div
                                                className="h-12 w-12 shrink-0 rounded-lg bg-muted flex items-center justify-center group-hover:bg-primary/10 transition-colors">
                                                <Building2
                                                    className="h-6 w-6 text-muted-foreground group-hover:text-primary"/>
                                            </div>
                                            <div>
                                                <h3 className="font-bold text-lg leading-none mb-1">{building.name}</h3>
                                                <p className="text-sm text-muted-foreground">{building.address}</p>
                                                <div className="flex items-center gap-2 mt-2">
                                                    <span
                                                        className="text-[10px] uppercase tracking-wider font-bold text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                                        Plan {building.building_id}
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                        <ChevronRight
                                            aria-hidden="true"
                                            className="h-5 w-5 text-muted-foreground group-hover:text-primary group-hover:translate-x-1 transition-all"/>
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>
                )}

                <div className="mt-8 pt-6 border-t border-border flex flex-col items-center gap-4">
                    <p className="text-sm text-muted-foreground">
                        Logging in as <span className="font-medium text-foreground">{user?.email}</span>
                    </p>
                    <Button variant="ghost" onClick={logout}
                            className="text-destructive hover:text-destructive hover:bg-destructive/10">
                        <LogOut className="mr-2 h-4 w-4"/>
                        Sign out and use different account
                    </Button>
                </div>
            </div>
        </div>
    );
};

export default SelectBuildingPage;
