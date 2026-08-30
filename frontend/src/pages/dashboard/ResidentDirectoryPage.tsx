"use client";

// @featuretrace:resident-directory-chat — Resident directory listing and click-to-chat initiation.
// Layer: frontend
// Data flow: ResidentDirectoryPage -> GET /directory + POST /conversations -> db.memberships/db.users/db.conversations (building-scoped).
// Related: backend/server.py, backend/routers/communication.py, docs/architecture/mindmap/featuretrace/resident-directory-chat_flow.md

import React, {useEffect, useState} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {Card, CardContent} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Badge} from '../../components/ui/badge';
import {Label} from '../../components/ui/label';
import {Switch} from '../../components/ui/switch';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import {Avatar, AvatarFallback} from '../../components/ui/avatar';
import {Home, Search, Settings, Users, X} from 'lucide-react';
import {getInitials} from '../../lib/utils';
import {toast} from 'sonner';
/**
 * @generated FunctionHeader
 * Function: ResidentDirectoryPage
 * Path: frontend/src/pages/dashboard/ResidentDirectoryPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ResidentDirectoryPage: React.FC = () => {
    const {api, user, selectedBuilding} = useAuth();
    const router = useRouter();
    const [directory, setDirectory] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [activeFilter, setActiveFilter] = useState<string | null>(null);
    const [settingsOpen, setSettingsOpen] = useState(false);
    const [creatingChat, setCreatingChat] = useState<string | null>(null);
    const [settings, setSettings] = useState({
        directory_visible: true,
        email_visible: false,
        phone_visible: false,
        move_in_date: ''
    });
    const selectedBuildingKey = selectedBuilding?.building_id || selectedBuilding?.id || selectedBuilding?.name || '';

    const fetchDirectory = React.useCallback(async () => {
        try {
            setLoading(true);
            const response = await api.get('/directory');
            setDirectory(response.data || []);
        } catch (error) {
            console.error('Failed to fetch directory:', error);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchDirectory();
    }, [fetchDirectory, selectedBuildingKey]);
    /**
     * @generated FunctionHeader
     * Function: handleUpdateSettings
     * Path: frontend/src/pages/dashboard/ResidentDirectoryPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateSettings = async () => {
        try {
            await api.put('/directory/settings', null, {params: settings});
            toast.success('Directory settings updated');
            setSettingsOpen(false);
            fetchDirectory();
        } catch (error) {
            toast.error('Failed to update settings');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleStartChat
     * Path: frontend/src/pages/dashboard/ResidentDirectoryPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleStartChat = async (resident: any) => {
        const residentChatId = resident.chat_user_id || resident.id;
        if (residentChatId === user?.id) {
            toast.info('Cannot start a chat with yourself');
            return;
        }

        if (!residentChatId) {
            toast.error('This resident is not available for chat.');
            return;
        }

        setCreatingChat(residentChatId);
        try {
            // Re-read the directory immediately before POST /conversations.
            // Directory cards can be stale after building switches or identity cleanup.
            const latestDirectoryResponse = await api.get('/directory');
            const latestDirectory = latestDirectoryResponse.data || [];
            setDirectory(latestDirectory);

            const latestResident = latestDirectory.find((entry: any) =>
                (entry.chat_user_id || entry.id) === residentChatId
            );
            if (!latestResident) {
                toast.error('This directory entry is no longer available. The directory has been refreshed.');
                return;
            }

            const latestChatId = latestResident.chat_user_id || latestResident.id;
            const response = await api.post('/conversations', {
                is_group: false,
                member_ids: [latestChatId]
            });

            toast.success(`Opening chat with ${latestResident.full_name}`);
            router.push(`/community/messages?conversationId=${response.data.id}`);
        } catch (error: any) {
            console.error('Failed to create conversation:', error);
            toast.error(error?.response?.data?.detail || 'Failed to start chat. Please try again.');
        } finally {
            setCreatingChat(null);
        }
    };

    const searchFiltered = directory.filter(resident =>
        resident.full_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        resident.unit_number?.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const apartments = searchFiltered.filter(r => r.unit_type === 'apartment');
    const townhouses = searchFiltered.filter(r => r.unit_type === 'townhouse');

    const filteredDirectory = activeFilter === 'apartments'
        ? searchFiltered.filter(r => r.unit_number?.startsWith('UA'))
        : activeFilter === 'townhouses'
            ? searchFiltered.filter(r => r.unit_number?.startsWith('TH'))
            : searchFiltered;

    return (
        <div className="space-y-6" data-testid="directory-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Resident Directory</h1>
                    <p className="text-muted-foreground">Connect with your neighbors
                        at {selectedBuilding?.name || 'your building'}</p>
                </div>
                <div className="flex gap-2">
                    <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
                        <DialogTrigger asChild>
                            <Button variant="outline" data-testid="directory-settings-btn">
                                <Settings className="mr-2 h-4 w-4"/>
                                My Settings
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>Directory Settings</DialogTitle>
                                <DialogDescription>Control how you appear in the resident directory</DialogDescription>
                            </DialogHeader>
                            <div className="space-y-6 py-4">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <Label htmlFor="visible" className="font-medium">Appear in Directory</Label>
                                        <p className="text-sm text-muted-foreground">Show your name and unit in the
                                            directory</p>
                                    </div>
                                    <Switch
                                        id="visible"
                                        checked={settings.directory_visible}
                                        onCheckedChange={(v) => setSettings(prev => ({...prev, directory_visible: v}))}
                                    />
                                </div>
                                <div className="flex items-center justify-between">
                                    <div>
                                        <Label htmlFor="email" className="font-medium">Show Email</Label>
                                        <p className="text-sm text-muted-foreground">Allow neighbors to see your
                                            email</p>
                                    </div>
                                    <Switch
                                        id="email"
                                        checked={settings.email_visible}
                                        onCheckedChange={(v) => setSettings(prev => ({...prev, email_visible: v}))}
                                    />
                                </div>
                                <div className="flex items-center justify-between">
                                    <div>
                                        <Label htmlFor="phone" className="font-medium">Show Phone</Label>
                                        <p className="text-sm text-muted-foreground">Allow neighbors to see your
                                            phone</p>
                                    </div>
                                    <Switch
                                        id="phone"
                                        checked={settings.phone_visible}
                                        onCheckedChange={(v) => setSettings(prev => ({...prev, phone_visible: v}))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="move_in">Move-in Date</Label>
                                    <Input
                                        id="move_in"
                                        type="date"
                                        value={settings.move_in_date}
                                        onChange={(e) => setSettings(prev => ({...prev, move_in_date: e.target.value}))}
                                    />
                                </div>
                                <Button onClick={handleUpdateSettings} className="w-full">Save Settings</Button>
                            </div>
                        </DialogContent>
                    </Dialog>
                </div>
            </div>

            <div className="relative max-w-md">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                <Input
                    placeholder="Search by name or unit number..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="pl-10 pr-10"
                    data-testid="directory-search"
                />
                {searchTerm && (
                    <button
                        type="button"
                        onClick={() => setSearchTerm('')}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                        aria-label="Clear search"
                    >
                        <X className="h-4 w-4"/>
                    </button>
                )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <Card
                    className={`card-dashboard cursor-pointer transition-all hover:shadow-md ${activeFilter === null ? 'ring-2 ring-indigo-500' : ''}`}
                    onClick={() => setActiveFilter(null)}
                    role="button"
                    aria-label="Show all residents"
                >
                    <CardContent className="p-6">
                        <div className="flex items-center gap-4">
                            <div className="p-3 rounded-full bg-blue-100">
                                <Users className="h-6 w-6 text-blue-600"/>
                            </div>
                            <div>
                                <p className="text-2xl font-bold">{directory.length}</p>
                                <p className="text-sm text-muted-foreground">All Residents</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card
                    className={`card-dashboard cursor-pointer transition-all hover:shadow-md ${activeFilter === 'apartments' ? 'ring-2 ring-indigo-500' : ''}`}
                    onClick={() => setActiveFilter(prev => prev === 'apartments' ? null : 'apartments')}
                    role="button"
                    aria-label="Filter apartments"
                >
                    <CardContent className="p-6">
                        <div className="flex items-center gap-4">
                            <div className="p-3 rounded-full bg-green-100">
                                <Home className="h-6 w-6 text-green-600"/>
                            </div>
                            <div>
                                <p className="text-2xl font-bold">{apartments.length}</p>
                                <p className="text-sm text-muted-foreground">Apartments</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
                <Card
                    className={`card-dashboard cursor-pointer transition-all hover:shadow-md ${activeFilter === 'townhouses' ? 'ring-2 ring-indigo-500' : ''}`}
                    onClick={() => setActiveFilter(prev => prev === 'townhouses' ? null : 'townhouses')}
                    role="button"
                    aria-label="Filter townhouses"
                >
                    <CardContent className="p-6">
                        <div className="flex items-center gap-4">
                            <div className="p-3 rounded-full bg-purple-100">
                                <Home className="h-6 w-6 text-purple-600"/>
                            </div>
                            <div>
                                <p className="text-2xl font-bold">{townhouses.length}</p>
                                <p className="text-sm text-muted-foreground">Townhouses</p>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                    {[1, 2, 3, 4].map((i) => (
                        <Card key={i} className="card-dashboard h-32 animate-pulse bg-muted"/>
                    ))}
                </div>
            ) : filteredDirectory.length === 0 ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <Users className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Residents Found</h3>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                    {filteredDirectory.map((resident) => (
                        <Card
                            key={resident.chat_user_id || resident.id}
                            className="card-dashboard hover:shadow-lg transition-all cursor-pointer group"
                            onClick={() => handleStartChat(resident)}
                            role="button"
                            tabIndex={0}
                            aria-label={`Start chat with ${resident.full_name}`}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    handleStartChat(resident);
                                }
                            }}
                        >
                            <CardContent className="p-6 text-center">
                                <Avatar className="h-16 w-16 mx-auto mb-4">
                                    <AvatarFallback className="bg-primary/10 text-primary">
                                        {getInitials(resident.full_name)}
                                    </AvatarFallback>
                                </Avatar>
                                <h3 className="font-semibold">{resident.full_name}</h3>
                                <Badge variant="secondary" className="mt-2">{resident.unit_number}</Badge>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}
        </div>
    );
};

export default ResidentDirectoryPage;
