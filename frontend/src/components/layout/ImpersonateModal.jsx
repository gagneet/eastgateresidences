"use client";

import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { ArrowRight, Building, Loader2, Search, ShieldAlert, User, UserSearch } from 'lucide-react';
import { ScrollArea } from '../ui/scroll-area';
import { Badge } from '../ui/badge';
import { Avatar, AvatarFallback, AvatarImage } from '../ui/avatar';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../ui/select';
import { cn, getInitials, roleDisplayNames } from '../../lib/utils';
/**
 * @generated FunctionHeader
 * Function: ImpersonateModal
 * Path: frontend/src/components/layout/ImpersonateModal.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ImpersonateModal = ({isOpen, onClose}) => {
    const {api, impersonate, availableBuildings, selectedBuilding} = useAuth();
    const [searchTerm, setSearchTerm] = useState('');
    const [loading, setLoading] = useState(false);
    const [results, setResults] = useState([]);
    const [selectedUser, setSelectedUser] = useState(null);
    const [impersonating, setImpersonating] = useState(false);
    const [selectedBuildingId, setSelectedBuildingId] = useState('');

    // Pre-select the current building when modal opens.
    // building_id (plan number, e.g. "13195") is what the backend membership
    // check and JWT claim use — not the Postgres scheme UUID in `id`.
    useEffect(() => {
        if (isOpen && selectedBuilding?.building_id) {
            setSelectedBuildingId(selectedBuilding.building_id);
        }
    }, [isOpen, selectedBuilding]);

    // Auto-update building selector when a user is selected
    useEffect(() => {
        if (selectedUser && selectedBuilding?.building_id) {
            setSelectedBuildingId(selectedBuilding.building_id);
        }
    }, [selectedUser, selectedBuilding]);

    useEffect(() => {
        if (!isOpen) {
            setSearchTerm('');
            setResults([]);
            setSelectedUser(null);
            setSelectedBuildingId(selectedBuilding?.building_id || '');
            return;
        }

        const timer = setTimeout(() => {
            if (searchTerm.trim().length >= 1) {
                handleSearch();
            } else {
                setResults([]);
            }
        }, 300);

        return () => clearTimeout(timer);
    }, [searchTerm, isOpen]);
    /**
     * @generated FunctionHeader
     * Function: handleSearch
     * Path: frontend/src/components/layout/ImpersonateModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSearch = async () => {
        setLoading(true);
        try {
            // Search for users including by unit_number
            // The backend /users endpoint supports role filter but not search query directly in standard way
            // We'll fetch all and filter client-side for better UX in this specific popup
            const response = await api.get('/users?status=active');
            const allUsers = response.data;

            const term = searchTerm.toLowerCase();
            const filtered = allUsers.filter(u =>
                    u.role !== 'super_admin' && (
                        ( u.full_name || '' ).toLowerCase().includes(term) ||
                        ( u.unit_owner_name || '' ).toLowerCase().includes(term) ||
                        ( u.email || '' ).toLowerCase().includes(term) ||
                        ( u.unit_number && u.unit_number.toString().toLowerCase().includes(term) )
                    )
            ).sort((a, b) => {
                // Sort by unit number primarily if available
                if (a.unit_number && b.unit_number) return a.unit_number.localeCompare(b.unit_number);
                return ( a.full_name || '' ).localeCompare(b.full_name || '');
            });

            setResults(filtered);
        } catch (error) {
            console.error('Search failed:', error);
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleImpersonate
     * Path: frontend/src/components/layout/ImpersonateModal.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleImpersonate = async () => {
        if (!selectedUser || !selectedBuildingId) return;

        setImpersonating(true);
        // Always pass building_id so the backend can verify the target user is a
        // member of that building and scope the JWT token correctly.
        const success = await impersonate(selectedUser.id, selectedBuildingId);
        setImpersonating(false);

        if (success) {
            onClose();
        }
    };

    return (
        <Dialog open={isOpen} onOpenChange={onClose}>
            <DialogContent className="sm:max-w-[500px] p-0 overflow-hidden gap-0">
                <div className="bg-primary/5 p-6 border-b">
                    <DialogHeader>
                        <div className="flex items-center gap-2 text-primary mb-1">
                            <UserSearch className="h-5 w-5"/>
                            <DialogTitle className="text-xl">User Impersonation</DialogTitle>
                        </div>
                        <DialogDescription>
                            Search for a Unit or User to view the portal from their perspective.
                            <span className="block mt-1 text-amber-600 font-medium flex items-center gap-1">
                <ShieldAlert className="h-3.5 w-3.5"/>
                Administrative PII masking will be applied.
              </span>
                        </DialogDescription>
                    </DialogHeader>
                </div>

                <div className="p-6 space-y-4">
                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                        <Input
                            placeholder="Search by Unit Number, Name or Email..."
                            className="pl-10 h-12 text-base shadow-sm border-primary/20 focus-visible:ring-primary"
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                            autoFocus
                        />
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between px-1">
                            <Label className="text-xs font-bold uppercase text-muted-foreground tracking-wider">
                                {results.length > 0 ? `${results.length} Matching Users` : 'Search Results'}
                            </Label>
                            {loading && <Loader2 className="h-3 w-3 animate-spin text-primary"/>}
                        </div>

                        <ScrollArea className="h-[300px] rounded-md border bg-muted/20">
                            {results.length > 0 ? (
                                <div className="p-2 space-y-1">
                                    {results.map((u) => (
                                        <button
                                            key={u.id}
                                            onClick={() => setSelectedUser(u)}
                                            className={cn(
                                                "w-full flex items-center gap-3 p-3 rounded-lg text-left transition-all",
                                                selectedUser?.id === u.id
                                                    ? "bg-primary text-primary-foreground shadow-md"
                                                    : "hover:bg-primary/10 bg-white"
                                            )}
                                        >
                                            <Avatar className="h-10 w-10 border-2 border-background shadow-sm">
                                                <AvatarImage src={u.profile_image}/>
                                                <AvatarFallback className={cn(
                                                    selectedUser?.id === u.id ? "bg-primary-foreground text-primary" : "bg-muted"
                                                )}>
                                                    {getInitials(u.full_name)}
                                                </AvatarFallback>
                                            </Avatar>

                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center justify-between gap-2">
                                                    {/* Show strata roll name (source of truth) as primary display name */}
                                                    <span className="font-bold text-sm truncate">
                            {u.unit_owner_name || u.full_name}
                          </span>
                                                    {u.unit_number && (
                                                        <Badge
                                                            variant={selectedUser?.id === u.id ? "secondary" : "outline"}
                                                            className="text-[10px] font-black shrink-0">
                                                            UNIT {u.unit_number}
                                                        </Badge>
                                                    )}
                                                </div>
                                                {/* If portal name differs from strata roll, show it as sub-label */}
                                                {u.unit_owner_name && u.unit_owner_name !== u.full_name && (
                                                    <div className={cn(
                                                        "text-[10px] truncate mt-0.5 italic",
                                                        selectedUser?.id === u.id ? "text-primary-foreground/70" : "text-amber-600"
                                                    )}>
                                                        Portal: {u.full_name}
                                                    </div>
                                                )}
                                                <div className="flex items-center justify-between mt-0.5">
                            <span className={cn(
                                "text-xs truncate",
                                selectedUser?.id === u.id ? "text-primary-foreground/80" : "text-muted-foreground"
                            )}>
                                {u.email}
                            </span>
                                                    <span className={cn(
                                                        "text-[10px] font-medium uppercase tracking-tighter",
                                                        selectedUser?.id === u.id ? "text-primary-foreground/90" : "text-primary"
                                                    )}>
                                {roleDisplayNames[ u.role ] || u.role}
                            </span>
                                                </div>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            ) : searchTerm && !loading ? (
                                <div
                                    className="h-full flex flex-col items-center justify-center text-center p-8 opacity-60">
                                    <Search className="h-10 w-10 mb-2"/>
                                    <p className="text-sm font-medium">No matches found for "{searchTerm}"</p>
                                    <p className="text-xs">Try searching for a different unit or name</p>
                                </div>
                            ) : (
                                <div
                                    className="h-full flex flex-col items-center justify-center text-center p-8 opacity-40">
                                    <Building className="h-10 w-10 mb-2"/>
                                    <p className="text-sm font-medium">Enter a unit number or resident name</p>
                                </div>
                            )}
                        </ScrollArea>
                    </div>
                </div>

                {/* Building selector — always shown so admin can confirm building context */}
                {availableBuildings && availableBuildings.length > 0 && (
                    <div className="space-y-1">
                        <Label className="text-xs font-bold uppercase text-muted-foreground tracking-wider">
                            Building Context
                        </Label>
                        {availableBuildings.length === 1 ? (
                            <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-muted/40 text-sm">
                                <Building className="h-4 w-4 text-muted-foreground"/>
                                <span className="font-medium">{availableBuildings[ 0 ].name}</span>
                                <Badge variant="outline" className="ml-auto font-mono text-xs">
                                    {availableBuildings[ 0 ].building_id}
                                </Badge>
                            </div>
                        ) : (
                            <Select value={selectedBuildingId} onValueChange={setSelectedBuildingId}>
                                <SelectTrigger className="h-10">
                                    <SelectValue placeholder="Select building context..."/>
                                </SelectTrigger>
                                <SelectContent>
                                    {availableBuildings.map(b => (
                                        <SelectItem key={b.id} value={b.building_id}>
                                            <span>{b.name}</span>
                                            <span
                                                className="ml-2 text-muted-foreground text-xs font-mono">({b.building_id})</span>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        )}
                        <p className="text-[10px] text-muted-foreground px-1">
                            The impersonated session will be scoped to this building. The target user must be a member.
                        </p>
                    </div>
                )}

                <DialogFooter className="p-6 bg-muted/30 border-t sm:justify-between items-center">
                    <div className="hidden sm:block">
                        {selectedUser && (
                            <div
                                className="flex items-center gap-2 text-xs text-muted-foreground animate-in fade-in slide-in-from-left-2">
                                <User className="h-3 w-3"/>
                                Selected: <span className="font-bold text-foreground">{selectedUser.full_name}</span>
                                {selectedBuildingId && (
                                    <Badge variant="outline" className="font-mono text-[10px]">
                                        {selectedBuildingId}
                                    </Badge>
                                )}
                            </div>
                        )}
                    </div>
                    <div className="flex gap-2 w-full sm:w-auto">
                        <Button variant="ghost" onClick={onClose} disabled={impersonating}>
                            Cancel
                        </Button>
                        <Button
                            disabled={!selectedUser || !selectedBuildingId || impersonating}
                            onClick={handleImpersonate}
                            className="flex-1 sm:flex-none min-w-[120px]"
                        >
                            {impersonating ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                    Switching...
                                </>
                            ) : (
                                <>
                                    Impersonate
                                    <ArrowRight className="ml-2 h-4 w-4"/>
                                </>
                            )}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

export default ImpersonateModal;
