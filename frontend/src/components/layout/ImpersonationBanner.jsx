import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Button } from '../ui/button';
import { AlertCircle, LogOut } from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: ImpersonationBanner
 * Path: frontend/src/components/layout/ImpersonationBanner.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ImpersonationBanner = () => {
    const {isImpersonating, user, exitImpersonation, selectedBuilding} = useAuth();

    if (!isImpersonating) return null;

    return (
        <div
            className="bg-amber-500 text-white py-2 px-4 flex items-center justify-between sticky top-0 z-[100] shadow-md animate-in slide-in-from-top duration-300">
            <div className="flex items-center gap-2 overflow-hidden">
                <AlertCircle className="h-5 w-5 flex-shrink-0"/>
                <p className="text-sm font-bold truncate">
                    Currently impersonating: <span className="underline decoration-white/50">{user?.full_name}</span>
                    {user?.unit_number && ` (Unit ${user.unit_number})`}
                    {selectedBuilding && (
                        <>
                            {' '}at <span className="underline decoration-white/50">{selectedBuilding.name}</span>
                        </>
                    )}
                    — <span className="opacity-90 font-normal italic">PII is masked for security</span>
                </p>
            </div>
            <Button
                variant="secondary"
                size="sm"
                onClick={exitImpersonation}
                className="ml-4 bg-white text-amber-600 hover:bg-white/90 border-none h-8 font-bold whitespace-nowrap"
            >
                <LogOut className="mr-2 h-4 w-4"/>
                Exit Impersonation
            </Button>
        </div>
    );
};

export default ImpersonationBanner;
