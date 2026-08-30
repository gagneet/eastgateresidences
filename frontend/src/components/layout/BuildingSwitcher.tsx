"use client";

import React, {useEffect, useState} from 'react';
import {useAuth} from '../../contexts/AuthContext';
import {Building2, ChevronDown, Loader2} from 'lucide-react';
import {motion} from 'framer-motion';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import {Button} from '../ui/button';
import {Tooltip, TooltipContent, TooltipTrigger} from '../ui/tooltip';

interface BuildingOption {
    id: string;
    name: string;
    address?: string;
    building_id?: string;
}
/**
 * @generated FunctionHeader
 * Function: BuildingSwitcher
 * Path: frontend/src/components/layout/BuildingSwitcher.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BuildingSwitcher: React.FC = () => {
    const {api, selectedBuilding, availableBuildings, switchBuilding, user} = useAuth();
    const [buildings, setBuildings] = useState<BuildingOption[]>(availableBuildings as BuildingOption[] || []);
    const [switching, setSwitching] = useState(false);

    useEffect(() => {
        if (!api || !user) return;
        api.get('/buildings/me')
            .then((res: any) => {
                const data: BuildingOption[] = res.data || [];
                if (data.length > 0) setBuildings(data);
            })
            .catch(() => {/* silently ignore */
            });
    }, [api, user]);

    const currentBuilding = selectedBuilding as BuildingOption | null;
    const currentName = currentBuilding?.name || buildings[0]?.name || 'East Gate Residences';
    const currentAddress = currentBuilding?.address || buildings[0]?.address || '';
    /**
     * @generated FunctionHeader
     * Function: handleSwitch
     * Path: frontend/src/components/layout/BuildingSwitcher.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSwitch = async (buildingId: string) => {
        if (switching) return;
        setSwitching(true);
        try {
            await switchBuilding(buildingId);
        } finally {
            setSwitching(false);
        }
    };

    // Single building — no switcher needed, just show name
    if (buildings.length <= 1) {
        return (
            <Tooltip>
                <TooltipTrigger asChild>
                    <div
                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-primary/10 border border-primary/20 focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
                        tabIndex={0}
                        aria-label={`Current building: ${currentName}${currentAddress ? `. ${currentAddress}` : ''}`}
                    >
                        <Building2 className="h-4 w-4 text-primary shrink-0"/>
                        <div className="min-w-0">
                            <p className="text-xs font-semibold text-primary truncate">{currentName}</p>
                            {currentAddress && (
                                <p className="text-[10px] text-muted-foreground truncate">{currentAddress}</p>
                            )}
                        </div>
                    </div>
                </TooltipTrigger>
                <TooltipContent side="right">
                    <p className="font-semibold">{currentName}</p>
                    {currentAddress && <p className="text-[10px] opacity-80">{currentAddress}</p>}
                </TooltipContent>
            </Tooltip>
        );
    }

    // Multiple buildings — show dropdown
    return (
        <DropdownMenu>
            <Tooltip>
                <TooltipTrigger asChild>
                    <DropdownMenuTrigger asChild>
                        <Button
                            variant="outline"
                            size="sm"
                            className="w-full justify-between gap-1 text-left h-auto py-2"
                            disabled={switching}
                            aria-label={`Switch building. Current: ${currentName}${currentAddress ? `. ${currentAddress}` : ''}`}
                            asChild
                        >
                            <motion.button
                                className="flex items-center justify-between w-full"
                                whileTap={{scale: 0.98}}
                            >
                                <div className="flex items-center gap-2 min-w-0">
                                    {switching ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary"/>
                                    ) : (
                                        <Building2 className="h-4 w-4 shrink-0 text-primary"/>
                                    )}
                                    <div className="min-w-0">
                                        <p className="text-xs font-semibold truncate">{currentName}</p>
                                        {currentAddress && (
                                            <p className="text-[10px] text-muted-foreground truncate">{currentAddress}</p>
                                        )}
                                    </div>
                                </div>
                                <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground"/>
                            </motion.button>
                        </Button>
                    </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent side="right">
                    <p className="font-semibold">{currentName}</p>
                    {currentAddress && <p className="text-[10px] opacity-80">{currentAddress}</p>}
                </TooltipContent>
            </Tooltip>
            <DropdownMenuContent align="start" className="w-64">
                <DropdownMenuLabel className="text-xs text-muted-foreground">Switch Building</DropdownMenuLabel>
                <DropdownMenuSeparator/>
                {buildings.map((b) => (
                    <DropdownMenuItem
                        key={b.id}
                        onClick={() => handleSwitch(b.id)}
                        className="flex items-start gap-2 cursor-pointer"
                    >
                        <Building2 className="h-4 w-4 mt-0.5 shrink-0 text-primary"/>
                        <div className="min-w-0">
                            <p className="text-sm font-medium truncate">{b.name}</p>
                            {b.address && (
                                <p className="text-xs text-muted-foreground truncate">{b.address}</p>
                            )}
                        </div>
                    </DropdownMenuItem>
                ))}
            </DropdownMenuContent>
        </DropdownMenu>
    );
};

export default BuildingSwitcher;
