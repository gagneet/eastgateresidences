"use client";
import React from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '@/contexts/AuthContext';
import {Card, CardContent} from '@/components/ui/card';
import {ArrowRight, Building2, Loader2, MapPin} from 'lucide-react';
import {motion} from 'framer-motion';
/**
 * @generated FunctionHeader
 * Function: SelectBuildingPage
 * Path: frontend/src/app/(public)/select-building/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SelectBuildingPage() {
    const {availableBuildings, switchBuilding, loading} = useAuth();
    const router = useRouter();

    const handleSelectBuilding = async (buildingId: string) => {
        await switchBuilding(buildingId, { redirectTo: false });
        router.push('/dashboard');
    };

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-50">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
            <div className="max-w-2xl w-full space-y-8">
                <div className="text-center space-y-2">
                    <h1 className="text-3xl font-black text-slate-900 tracking-tight">Select Property</h1>
                    <p className="text-slate-500 font-medium">Which building would you like to manage today?</p>
                </div>

                <div className="grid gap-4">
                    {availableBuildings.map((building, index) => (
                        <motion.button
                            key={building.id}
                            initial={{opacity: 0, y: 10}}
                            animate={{opacity: 1, y: 0}}
                            transition={{delay: index * 0.1}}
                            onClick={() => handleSelectBuilding(building.id)}
                            className="w-full group"
                        >
                            <Card
                                className="hover:border-primary/50 hover:shadow-md transition-all duration-300 text-left">
                                <CardContent className="p-6 flex items-center justify-between">
                                    <div className="flex items-center gap-4">
                                        <div
                                            className="p-3 rounded-2xl bg-primary/10 text-primary group-hover:bg-primary group-hover:text-white transition-colors">
                                            <Building2 size={24}/>
                                        </div>
                                        <div>
                                            <h3 className="font-bold text-lg text-slate-900">{building.name}</h3>
                                            <p className="text-sm text-slate-500 flex items-center gap-1">
                                                <MapPin size={14} className="opacity-50"/>
                                                {building.address}
                                            </p>
                                        </div>
                                    </div>
                                    <ArrowRight
                                        className="text-slate-300 group-hover:text-primary group-hover:translate-x-1 transition-all"/>
                                </CardContent>
                            </Card>
                        </motion.button>
                    ))}

                    {availableBuildings.length === 0 && (
                        <Card className="border-dashed">
                            <CardContent className="p-12 text-center text-slate-500">
                                <Building2 size={48} className="mx-auto mb-4 opacity-20"/>
                                <p>No active building memberships found for your account.</p>
                                <p className="text-xs mt-2 italic">Please contact your administrator if this is an
                                    error.</p>
                            </CardContent>
                        </Card>
                    )}
                </div>
            </div>
        </div>
    );
}
