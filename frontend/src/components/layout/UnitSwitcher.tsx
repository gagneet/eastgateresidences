// @featuretrace:multi-unit-ownership — sidebar dropdown that switches an owner's active unit.
// Layer: frontend
// Data flow: UnitSwitcher → AuthContext.switchUnit() → POST /auth/switch-unit → re-issued JWT
//            (unit_number claim) → every owner-facing read (building-scoped).
// Related: frontend/src/contexts/AuthContext.tsx
//           frontend/src/hooks/useActiveUnit.ts
//           backend/routers/auth.py (POST /auth/switch-unit, GET /auth/my-units)
// Toggle: multi_unit_ownership
// Tests: tests/frontend/unit/components/UnitSwitcher.test.tsx
"use client";

import React, {useState} from 'react';
import {useAuth} from '../../contexts/AuthContext';
import {Home, ChevronDown, Loader2} from 'lucide-react';
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
import {Badge} from '../ui/badge';
/**
 * @generated FunctionHeader
 * Function: UnitSwitcher
 * Path: frontend/src/components/layout/UnitSwitcher.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UnitSwitcher: React.FC = () => {
    const {user, selectedUnit, availableUnits, switchUnit, hasFeatureAccess} = useAuth() as any;
    const [switching, setSwitching] = useState(false);

    // Only render for owners with the feature enabled and multiple units
    if (!hasFeatureAccess('multi_unit_ownership')) return null;
    if (!user || !['owner', 'ec_member', 'strata_manager', 'super_admin'].includes(user.role)) return null;

    const units: string[] = availableUnits?.length ? availableUnits : (user?.owned_units ?? []);
    const activeUnit = selectedUnit ?? user?.unit_number ?? null;

    if (units.length <= 1) {
        if (!activeUnit) return null;
        return (
            <Tooltip>
                <TooltipTrigger asChild>
                    <div
                        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-secondary/10 border border-secondary/20 focus-visible:ring-2 focus-visible:ring-secondary focus-visible:outline-none"
                        tabIndex={0}
                        aria-label={`Your unit: ${activeUnit}`}
                    >
                        <Home className="h-4 w-4 text-secondary shrink-0" aria-hidden="true"/>
                        <div className="min-w-0">
                            <p className="text-[10px] text-muted-foreground">Your unit</p>
                            <p className="text-xs font-semibold text-secondary truncate">Unit {activeUnit}</p>
                        </div>
                    </div>
                </TooltipTrigger>
                <TooltipContent side="right">
                    <p className="font-semibold">Unit {activeUnit}</p>
                </TooltipContent>
            </Tooltip>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: handleSwitch
     * Path: frontend/src/components/layout/UnitSwitcher.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSwitch = async (unitNumber: string) => {
        if (switching || unitNumber === activeUnit) return;
        setSwitching(true);
        try {
            await switchUnit(unitNumber);
        } finally {
            setSwitching(false);
        }
    };

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
                            aria-label={`Switch unit. Current: Unit ${activeUnit}`}
                            asChild
                        >
                            <motion.button
                                className="flex items-center justify-between w-full"
                                whileTap={{scale: 0.98}}
                            >
                                <div className="flex items-center gap-2 min-w-0">
                                    {switching ? (
                                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-secondary"
                                                 aria-hidden="true"/>
                                    ) : (
                                        <Home className="h-4 w-4 shrink-0 text-secondary" aria-hidden="true"/>
                                    )}
                                    <div className="min-w-0">
                                        <p className="text-[10px] text-muted-foreground leading-none mb-0.5">Active
                                            unit</p>
                                        <p className="text-xs font-semibold truncate">
                                            Unit {activeUnit ?? '—'}
                                        </p>
                                    </div>
                                </div>
                                <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true"/>
                            </motion.button>
                        </Button>
                    </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent side="right">
                    <p>Switch active unit</p>
                    <p className="text-[10px] opacity-70">You own {units.length} units</p>
                </TooltipContent>
            </Tooltip>

            <DropdownMenuContent side="right" align="start" className="w-52" data-testid="unit-switcher-menu">
                <DropdownMenuLabel className="text-xs text-muted-foreground">Your units</DropdownMenuLabel>
                <DropdownMenuSeparator/>
                {units.map((unitNumber: string) => (
                    <DropdownMenuItem
                        key={unitNumber}
                        onClick={() => handleSwitch(unitNumber)}
                        className="flex items-center justify-between gap-2 cursor-pointer"
                        aria-current={unitNumber === activeUnit ? 'true' : undefined}
                        data-testid={`unit-option-${unitNumber}`}
                    >
                        <div className="flex items-center gap-2 min-w-0">
                            <Home className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true"/>
                            <span className="text-sm font-medium truncate">Unit {unitNumber}</span>
                        </div>
                        {unitNumber === activeUnit && (
                            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">Active</Badge>
                        )}
                    </DropdownMenuItem>
                ))}
            </DropdownMenuContent>
        </DropdownMenu>
    );
};

export default UnitSwitcher;
