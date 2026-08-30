import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Badge } from '../../components/ui/badge';
import { Avatar, AvatarFallback } from '../../components/ui/avatar';
import { Textarea } from '../../components/ui/textarea';
import { ScrollArea } from '../../components/ui/scroll-area';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '../../components/ui/dropdown-menu';
import {
    Archive,
    Building2,
    Crown,
    Home,
    Loader2,
    MessageSquare,
    MoreVertical,
    Plus,
    Search,
    Send,
    Settings,
    Trash2,
    UserMinus,
    UserPlus,
    Users,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn, formatDate, getInitials } from '../../lib/utils';
import { Popover, PopoverContent, PopoverTrigger, } from "../../components/ui/popover";
/**
 * @generated FunctionHeader
 * Function: ChatPage
 * Path: frontend/src/pages/dashboard/ChatPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ChatPage = () => {
    const {api, user, hasPermission} = useAuth();
    const [groups, setGroups] = useState([]);
    const [selectedGroup, setSelectedGroup] = useState(null);
    const [messages, setMessages] = useState([]);
    const [messageInput, setMessageInput] = useState('');
    const [loading, setLoading] = useState(true);
    const [sendingMessage, setSendingMessage] = useState(false);
    const [createDialogOpen, setCreateDialogOpen] = useState(false);
    const [settingsDialogOpen, setSettingsDialogOpen] = useState(false);
    const [addMemberDialogOpen, setAddMemberDialogOpen] = useState(false);
    const [allUsers, setAllUsers] = useState([]);
    const [searchTerm, setSearchTerm] = useState('');
    const [addMemberSearch, setAddMemberSearch] = useState('');
    const textareaRef = React.useRef(null);

    // Auto-expand textarea
    React.useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
        }
    }, [messageInput]);

    // Create group form
    const [newGroup, setNewGroup] = useState({
        name: '',
        description: '',
        selectedMembers: [],
    });

    // Fetch groups
    const fetchGroups = useCallback(async () => {
        try {
            const response = await api.get('/chat-groups');
            setGroups(response.data);

            // Auto-select first group if none selected
            if (!selectedGroup && response.data.length > 0) {
                setSelectedGroup(response.data[ 0 ]);
            }
        } catch (error) {
            console.error('Failed to fetch groups:', error);
            toast.error('Failed to load chat groups');
        } finally {
            setLoading(false);
        }
    }, [api, selectedGroup]);

    // Fetch messages for selected group
    const fetchMessages = useCallback(async () => {
        if (!selectedGroup) return;

        try {
            const response = await api.get(`/chat-groups/${selectedGroup.id}/messages?limit=100`);
            setMessages(response.data);
        } catch (error) {
            console.error('Failed to fetch messages:', error);
            toast.error('Failed to load messages');
        }
    }, [api, selectedGroup]);

    // Fetch all users for group creation
    const fetchUsers = useCallback(async () => {
        try {
            const response = await api.get('/chat/users');
            setAllUsers(response.data);
        } catch (error) {
            console.error('Failed to fetch users:', error);
            toast.error('Failed to load users list');
        }
    }, [api]);

    useEffect(() => {
        fetchGroups();
        fetchUsers();
    }, [fetchGroups, fetchUsers]);

    useEffect(() => {
        if (selectedGroup) {
            fetchMessages();
        }
    }, [selectedGroup, fetchMessages]);

    // Poll for new messages every 5 seconds
    useEffect(() => {
        const interval = setInterval(() => {
            if (selectedGroup) {
                fetchMessages();
            }
        }, 5000);

        return () => clearInterval(interval);
    }, [selectedGroup, fetchMessages]);
    // Send message
    /**
     * @generated FunctionHeader
     * Function: handleSendMessage
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSendMessage = async (e) => {
        e.preventDefault();
        if (!messageInput.trim() || !selectedGroup) return;

        setSendingMessage(true);
        try {
            await api.post(`/chat-groups/${selectedGroup.id}/messages`, {
                content: messageInput.trim(),
            });
            setMessageInput('');
            await fetchMessages();
        } catch (error) {
            console.error('Failed to send message:', error);
            toast.error('Failed to send message');
        } finally {
            setSendingMessage(false);
        }
    };
    // Delete message
    /**
     * @generated FunctionHeader
     * Function: handleDeleteMessage
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteMessage = async (messageId) => {
        if (!window.confirm('Are you sure you want to delete this message?')) return;

        try {
            await api.delete(`/chat-groups/${selectedGroup.id}/messages/${messageId}`);
            toast.success('Message deleted');
            await fetchMessages();
        } catch (error) {
            console.error('Failed to delete message:', error);
            toast.error(error.response?.data?.detail || 'Failed to delete message');
        }
    };
    // Create group
    /**
     * @generated FunctionHeader
     * Function: handleCreateGroup
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateGroup = async () => {
        if (!newGroup.name.trim()) {
            toast.error('Please enter a group name');
            return;
        }

        try {
            await api.post('/chat-groups', {
                name: newGroup.name,
                description: newGroup.description,
                type: 'custom',
                initial_members: newGroup.selectedMembers,
            });

            toast.success('Group created successfully');
            setCreateDialogOpen(false);
            setNewGroup({name: '', description: '', selectedMembers: []});
            await fetchGroups();
        } catch (error) {
            console.error('Failed to create group:', error);
            toast.error('Failed to create group');
        }
    };
    // Remove member from group
    /**
     * @generated FunctionHeader
     * Function: handleRemoveMember
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRemoveMember = async (userId) => {
        if (!window.confirm('Remove this member from the group?')) return;

        try {
            await api.delete(`/chat-groups/${selectedGroup.id}/members/${userId}`);
            toast.success('Member removed');
            await fetchGroups();

            // If user removed themselves, deselect group
            if (userId === user.id) {
                setSelectedGroup(null);
            }
        } catch (error) {
            console.error('Failed to remove member:', error);
            toast.error('Failed to remove member');
        }
    };
    // Add member to group
    /**
     * @generated FunctionHeader
     * Function: handleAddMember
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddMember = async (userId) => {
        try {
            await api.post(`/chat-groups/${selectedGroup.id}/members`, {user_id: userId});
            toast.success('Member added');
            await fetchGroups();
            setAddMemberDialogOpen(false);
            setAddMemberSearch('');
        } catch (error) {
            console.error('Failed to add member:', error);
            toast.error(error.response?.data?.detail || 'Failed to add member');
        }
    };
    // Archive group (EC Members — end of year)
    /**
     * @generated FunctionHeader
     * Function: handleArchiveGroup
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleArchiveGroup = async () => {
        if (!window.confirm(`Archive "${selectedGroup.name}"? A new group will be created for the current year.`)) return;
        try {
            await api.put(`/chat-groups/${selectedGroup.id}/archive`);
            toast.success('Group archived. A fresh EC Members group has been created.');
            setSelectedGroup(null);
            await fetchGroups();
            setSettingsDialogOpen(false);
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to archive group');
        }
    };
    // Delete group
    /**
     * @generated FunctionHeader
     * Function: handleDeleteGroup
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteGroup = async () => {
        if (!window.confirm(`Delete group "${selectedGroup.name}"? This cannot be undone.`)) return;

        try {
            await api.delete(`/chat-groups/${selectedGroup.id}`);
            toast.success('Group deleted');
            setSelectedGroup(null);
            await fetchGroups();
            setSettingsDialogOpen(false);
        } catch (error) {
            console.error('Failed to delete group:', error);
            toast.error(error.response?.data?.detail || 'Failed to delete group');
        }
    };
    // Check if user can delete message
    /**
     * @generated FunctionHeader
     * Function: canDeleteMessage
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const canDeleteMessage = (message) => {
        if (!selectedGroup) return false;

        const isAdmin = user.role === 'super_admin';
        const isStrataAdmin = user.role === 'strata_admin';
        const isMessageOwner = message.sender_id === user.id;
        const whoCanDelete = selectedGroup.settings?.who_can_delete || [];

        if (whoCanDelete.includes('admin') && isAdmin) return true;
        if (whoCanDelete.includes('strata_admin') && isStrataAdmin) return true;
        if (whoCanDelete.includes('message_owner') && isMessageOwner) return true;

        return false;
    };
    // Check if user is group admin
    /**
     * @generated FunctionHeader
     * Function: isGroupAdmin
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const isGroupAdmin = (groupId) => {
        const group = groups.find(g => g.id === groupId);
        if (!group) return false;

        const member = group.members?.find(m => m.user_id === user.id);
        return member?.role === 'admin';
    };
    // Get group icon
    /**
     * @generated FunctionHeader
     * Function: getGroupIcon
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getGroupIcon = (group) => {
        if (group.type === 'general') return <Home className="h-4 w-4"/>;
        if (group.type === 'role_based') return <Crown className="h-4 w-4"/>;
        if (group.type === 'unit_based') return <Building2 className="h-4 w-4"/>;
        return <Users className="h-4 w-4"/>;
    };
    // Get role color
    /**
     * @generated FunctionHeader
     * Function: getRoleBadgeColor
     * Path: frontend/src/pages/dashboard/ChatPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getRoleBadgeColor = (role) => {
        const colors = {
            super_admin: 'bg-purple-100 text-purple-800',
            chairman: 'bg-blue-100 text-blue-800',
            ec_member: 'bg-green-100 text-green-800',
            strata_manager: 'bg-cyan-100 text-cyan-800',
            admin_staff: 'bg-teal-100 text-teal-800',
            owner: 'bg-yellow-100 text-yellow-800',
            tenant: 'bg-orange-100 text-orange-800',
            service_provider: 'bg-amber-100 text-amber-800',
            guest: 'bg-slate-100 text-slate-700',
        };
        return colors[ role ] || 'bg-gray-100 text-gray-800';
    };

    // Filtered users for member selection
    const filteredUsers = allUsers.filter(u =>
        u.full_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        u.email.toLowerCase().includes(searchTerm.toLowerCase())
    );

    if (loading) {
        return (
            <div className="flex items-center justify-center h-96">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="chat-page">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Community Chat</h1>
                    <p className="text-muted-foreground">Connect with your neighbors</p>
                </div>
                {hasPermission('can_chat') && (
                    <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
                        <DialogTrigger asChild>
                            <Button>
                                <Plus className="mr-2 h-4 w-4"/>
                                Create Group
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-2xl">
                            <DialogHeader>
                                <DialogTitle>Create New Group</DialogTitle>
                                <DialogDescription>
                                    Create a private group and invite members
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-4">
                                <div>
                                    <Label htmlFor="group-name">Group Name *</Label>
                                    <Input
                                        id="group-name"
                                        placeholder="e.g., Building A Residents"
                                        value={newGroup.name}
                                        onChange={(e) => setNewGroup({...newGroup, name: e.target.value})}
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="group-description">Description</Label>
                                    <Textarea
                                        id="group-description"
                                        placeholder="What's this group about?"
                                        value={newGroup.description}
                                        onChange={(e) => setNewGroup({...newGroup, description: e.target.value})}
                                        rows={3}
                                    />
                                </div>
                                <div>
                                    <Label>Invite Members (Optional)</Label>
                                    <Input
                                        placeholder="Search users..."
                                        value={searchTerm}
                                        onChange={(e) => setSearchTerm(e.target.value)}
                                        className="mb-2"
                                    />
                                    <ScrollArea className="h-48 border rounded-md p-2">
                                        {filteredUsers.map((u) => (
                                            <div
                                                key={u.id}
                                                className="flex items-center gap-2 p-2 hover:bg-muted rounded-md cursor-pointer"
                                                onClick={() => {
                                                    setNewGroup({
                                                        ...newGroup,
                                                        selectedMembers: newGroup.selectedMembers.includes(u.id)
                                                            ? newGroup.selectedMembers.filter(id => id !== u.id)
                                                            : [...newGroup.selectedMembers, u.id]
                                                    });
                                                }}
                                            >
                                                <input
                                                    type="checkbox"
                                                    checked={newGroup.selectedMembers.includes(u.id)}
                                                    onChange={() => {
                                                    }}
                                                    className="rounded"
                                                />
                                                <Avatar className="h-8 w-8">
                                                    <AvatarFallback>{getInitials(u.full_name)}</AvatarFallback>
                                                </Avatar>
                                                <div className="flex-1">
                                                    <p className="text-sm font-medium">{u.full_name}</p>
                                                    <p className="text-xs text-muted-foreground">{u.email}</p>
                                                </div>
                                                <Badge variant="outline" className="text-xs">
                                                    {u.role}
                                                </Badge>
                                            </div>
                                        ))}
                                    </ScrollArea>
                                    {newGroup.selectedMembers.length > 0 && (
                                        <p className="text-sm text-muted-foreground mt-2">
                                            {newGroup.selectedMembers.length} member(s) selected
                                        </p>
                                    )}
                                </div>
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={() => setCreateDialogOpen(false)}>
                                    Cancel
                                </Button>
                                <Button onClick={handleCreateGroup}>Create Group</Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                )}
            </div>

            <div className="flex flex-row h-[calc(100vh-200px)] lg:h-auto lg:grid lg:grid-cols-4 gap-2 lg:gap-6">
                {/* Groups Sidebar */}
                <Card className="w-16 lg:w-auto lg:col-span-1 flex flex-col overflow-hidden">
                    <CardHeader className="pb-3 px-2 lg:px-6">
                        <CardTitle className="text-lg flex items-center justify-center lg:justify-start gap-2">
                            <MessageSquare className="h-5 w-5"/>
                            <span className="hidden lg:inline">Groups</span>
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="p-0 flex-1 overflow-hidden">
                        <ScrollArea className="h-full">
                            {groups.length === 0 ? (
                                <div className="p-4 text-center text-muted-foreground">
                                    <MessageSquare className="h-12 w-12 mx-auto mb-2 opacity-20"/>
                                    <p className="text-sm hidden lg:block">No groups yet</p>
                                </div>
                            ) : (
                                <div className="space-y-1 p-1 lg:p-2">
                                    {groups.map((group) => {
                                        const buttonContent = (
                                            <button
                                                onClick={() => setSelectedGroup(group)}
                                                className={`w-full text-left p-2 lg:p-3 rounded-lg transition-colors flex items-center lg:items-start justify-center lg:justify-start gap-2 ${
                                                    selectedGroup?.id === group.id
                                                        ? 'bg-primary text-primary-foreground'
                                                        : 'hover:bg-muted'
                                                }`}
                                            >
                                                <div className={cn(
                                                    "flex-shrink-0",
                                                    selectedGroup?.id === group.id ? 'text-primary-foreground' : 'text-primary'
                                                )}>
                                                    {getGroupIcon(group)}
                                                </div>
                                                <div className="flex-1 min-w-0 hidden lg:block">
                                                    <div className="flex items-center gap-2">
                                                        <p className="font-medium text-sm truncate">{group.name}</p>
                                                        {group.is_system && (
                                                            <Badge variant="secondary" className="text-xs">
                                                                System
                                                            </Badge>
                                                        )}
                                                    </div>
                                                    {group.last_message && (
                                                        <p className={`text-xs truncate ${
                                                            selectedGroup?.id === group.id
                                                                ? 'text-primary-foreground/70'
                                                                : 'text-muted-foreground'
                                                        }`}>
                                                            {group.last_message.sender_name}: {group.last_message.content}
                                                        </p>
                                                    )}
                                                    <p className={`text-xs ${
                                                        selectedGroup?.id === group.id
                                                            ? 'text-primary-foreground/60'
                                                            : 'text-muted-foreground'
                                                    }`}>
                                                        {group.member_count} members
                                                    </p>
                                                </div>
                                            </button>
                                        );

                                        return (
                                            <div key={group.id}>
                                                <div className="lg:hidden">
                                                    <Popover>
                                                        <PopoverTrigger asChild>
                                                            {buttonContent}
                                                        </PopoverTrigger>
                                                        <PopoverContent side="right" className="w-auto p-2">
                                                            <p className="text-sm font-semibold">{group.name}</p>
                                                            <p className="text-xs text-muted-foreground">{group.member_count} members</p>
                                                        </PopoverContent>
                                                    </Popover>
                                                </div>
                                                <div className="hidden lg:block">
                                                    {buttonContent}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </ScrollArea>
                    </CardContent>
                </Card>

                {/* Chat View */}
                <Card className="flex-1 lg:col-span-3 flex flex-col overflow-hidden">
                    {selectedGroup ? (
                        <>
                            {/* Chat Header */}
                            <CardHeader className="pb-3 border-b">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-full bg-primary/10 text-primary">
                                            {getGroupIcon(selectedGroup)}
                                        </div>
                                        <div>
                                            <CardTitle className="text-lg">{selectedGroup.name}</CardTitle>
                                            {selectedGroup.description && (
                                                <p className="text-sm text-muted-foreground">
                                                    {selectedGroup.description}
                                                </p>
                                            )}
                                            <p className="text-xs text-muted-foreground mt-1">
                                                {selectedGroup.member_count} members
                                            </p>
                                        </div>
                                    </div>
                                    <DropdownMenu>
                                        <DropdownMenuTrigger asChild>
                                            <Button variant="ghost" size="icon" aria-label="Group actions">
                                                <MoreVertical className="h-4 w-4"/>
                                            </Button>
                                        </DropdownMenuTrigger>
                                        <DropdownMenuContent align="end">
                                            <DropdownMenuLabel>Group Actions</DropdownMenuLabel>
                                            <DropdownMenuSeparator/>
                                            <DropdownMenuItem onClick={() => setSettingsDialogOpen(true)}>
                                                <Settings className="mr-2 h-4 w-4"/>
                                                Group Settings
                                            </DropdownMenuItem>
                                            {!selectedGroup.is_system && (
                                                <DropdownMenuItem onClick={() => handleRemoveMember(user.id)}>
                                                    <UserMinus className="mr-2 h-4 w-4"/>
                                                    Leave Group
                                                </DropdownMenuItem>
                                            )}
                                        </DropdownMenuContent>
                                    </DropdownMenu>
                                </div>
                            </CardHeader>

                            {/* Messages */}
                            <CardContent className="p-0 flex-1 flex flex-col overflow-hidden">
                                <ScrollArea className="flex-1 p-4">
                                    {messages.length === 0 ? (
                                        <div className="text-center text-muted-foreground py-12">
                                            <MessageSquare className="h-12 w-12 mx-auto mb-2 opacity-20"/>
                                            <p>No messages yet. Start the conversation!</p>
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            {messages.map((message) => (
                                                <div
                                                    key={message.id}
                                                    className={`flex gap-3 ${
                                                        message.sender_id === user.id ? 'flex-row-reverse' : ''
                                                    }`}
                                                >
                                                    <Avatar className="h-8 w-8">
                                                        <AvatarFallback>
                                                            {getInitials(message.sender_name)}
                                                        </AvatarFallback>
                                                    </Avatar>
                                                    <div
                                                        className={`flex-1 ${message.sender_id === user.id ? 'text-right' : ''}`}>
                                                        <div className="flex items-center gap-2 mb-1">
                                                            {message.sender_id !== user.id && (
                                                                <>
                                  <span className="text-sm font-medium">
                                    {message.sender_name}
                                  </span>
                                                                    {message.sender_role && (
                                                                        <Badge
                                                                            variant="outline"
                                                                            className={`text-xs ${getRoleBadgeColor(message.sender_role)}`}
                                                                        >
                                                                            {message.sender_role}
                                                                        </Badge>
                                                                    )}
                                                                </>
                                                            )}
                                                            <span className="text-xs text-muted-foreground">
                                {formatDate(message.created_at)}
                              </span>
                                                        </div>
                                                        <div
                                                            className={`inline-block px-4 py-2 rounded-lg max-w-[80%] ${
                                                                message.sender_id === user.id
                                                                    ? 'bg-primary text-primary-foreground'
                                                                    : 'bg-muted'
                                                            }`}
                                                        >
                                                            <p className="text-sm whitespace-pre-wrap break-words">
                                                                {message.content}
                                                            </p>
                                                        </div>
                                                        {canDeleteMessage(message) && (
                                                            <button
                                                                onClick={() => handleDeleteMessage(message.id)}
                                                                className="text-xs text-destructive hover:underline mt-1"
                                                            >
                                                                Delete
                                                            </button>
                                                        )}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </ScrollArea>

                                {/* Message Input */}
                                <div className="p-4 border-t">
                                    <form onSubmit={handleSendMessage} className="flex items-end gap-2">
                                        <Textarea
                                            ref={textareaRef}
                                            placeholder="Type your message..."
                                            value={messageInput}
                                            onChange={(e) => setMessageInput(e.target.value)}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' && !e.shiftKey) {
                                                    e.preventDefault();
                                                    handleSendMessage(e);
                                                }
                                            }}
                                            disabled={sendingMessage}
                                            className="min-h-[40px] max-h-[120px] resize-none py-2"
                                        />
                                        <Button type="submit" disabled={sendingMessage || !messageInput.trim()}
                                                size="icon" className="shrink-0" aria-label="Send message">
                                            {sendingMessage ? (
                                                <Loader2 className="h-4 w-4 animate-spin"/>
                                            ) : (
                                                <Send className="h-4 w-4"/>
                                            )}
                                        </Button>
                                    </form>
                                </div>
                            </CardContent>

                            {/* Group Settings Dialog */}
                            <Dialog open={settingsDialogOpen} onOpenChange={setSettingsDialogOpen}>
                                <DialogContent className="max-w-2xl">
                                    <DialogHeader>
                                        <DialogTitle>Group Settings: {selectedGroup.name}</DialogTitle>
                                    </DialogHeader>
                                    <div className="space-y-4">
                                        {/* Members List */}
                                        <div>
                                            <div className="flex items-center justify-between mb-2">
                                                <Label>Members ({selectedGroup.members?.length || 0})</Label>
                                                {!selectedGroup.is_system &&
                                                    ( isGroupAdmin(selectedGroup.id) || user.role === 'super_admin' ) && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={() => setAddMemberDialogOpen(true)}
                                                        >
                                                            <UserPlus className="mr-2 h-4 w-4"/>
                                                            Add Member
                                                        </Button>
                                                    )}
                                            </div>
                                            <ScrollArea className="h-64 border rounded-md p-2">
                                                {selectedGroup.members?.map((member) => (
                                                    <div
                                                        key={member.user_id}
                                                        className="flex items-center gap-2 p-2 rounded-md hover:bg-muted"
                                                    >
                                                        <Avatar className="h-8 w-8">
                                                            <AvatarFallback>{getInitials(member.full_name)}</AvatarFallback>
                                                        </Avatar>
                                                        <div className="flex-1">
                                                            <p className="text-sm font-medium">{member.full_name}</p>
                                                            <p className="text-xs text-muted-foreground">
                                                                {member.role === 'admin' ? 'Group Admin' : 'Member'}
                                                            </p>
                                                        </div>
                                                        {!selectedGroup.is_system &&
                                                            ( isGroupAdmin(selectedGroup.id) || user.role === 'super_admin' ) &&
                                                            member.user_id !== user.id && (
                                                                <Button
                                                                    variant="ghost"
                                                                    size="sm"
                                                                    onClick={() => handleRemoveMember(member.user_id)}
                                                                >
                                                                    <UserMinus className="h-4 w-4"/>
                                                                </Button>
                                                            )}
                                                    </div>
                                                ))}
                                            </ScrollArea>
                                        </div>

                                        {/* Archive EC Members group */}
                                        {selectedGroup.is_system &&
                                            selectedGroup.type === 'role_based' &&
                                            ( user.role === 'strata_admin' || user.role === 'super_admin' ) && (
                                                <div className="pt-4 border-t">
                                                    <Button
                                                        variant="outline"
                                                        onClick={handleArchiveGroup}
                                                        className="w-full text-amber-600 border-amber-200 hover:bg-amber-50"
                                                    >
                                                        <Archive className="mr-2 h-4 w-4"/>
                                                        Archive Group (End of Year)
                                                    </Button>
                                                    <p className="text-xs text-muted-foreground mt-1 text-center">
                                                        Archives this group and creates a fresh EC Members group.
                                                    </p>
                                                </div>
                                            )}

                                        {/* Delete Group */}
                                        {!selectedGroup.is_system &&
                                            ( isGroupAdmin(selectedGroup.id) || user.role === 'super_admin' ) && (
                                                <div className="pt-4 border-t">
                                                    <Button
                                                        variant="destructive"
                                                        onClick={handleDeleteGroup}
                                                        className="w-full"
                                                    >
                                                        <Trash2 className="mr-2 h-4 w-4"/>
                                                        Delete Group
                                                    </Button>
                                                </div>
                                            )}
                                    </div>
                                </DialogContent>
                            </Dialog>

                            {/* Add Member Dialog */}
                            <Dialog open={addMemberDialogOpen} onOpenChange={setAddMemberDialogOpen}>
                                <DialogContent className="max-w-md">
                                    <DialogHeader>
                                        <DialogTitle>Add Member to {selectedGroup?.name}</DialogTitle>
                                        <DialogDescription>
                                            Select a user to add to this group
                                        </DialogDescription>
                                    </DialogHeader>
                                    <div className="space-y-4">
                                        {/* Search */}
                                        <div className="relative">
                                            <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground"/>
                                            <Input
                                                placeholder="Search users..."
                                                value={addMemberSearch}
                                                onChange={(e) => setAddMemberSearch(e.target.value)}
                                                className="pl-9"
                                            />
                                        </div>

                                        {/* User List */}
                                        <ScrollArea className="h-64 border rounded-md p-2">
                                            {allUsers
                                                .filter((u) => {
                                                    // Exclude users already in the group
                                                    const isMember = selectedGroup?.members?.some(
                                                        (m) => m.user_id === u.id
                                                    );
                                                    if (isMember) return false;

                                                    // Filter by search term
                                                    if (addMemberSearch) {
                                                        const searchLower = addMemberSearch.toLowerCase();
                                                        return (
                                                            u.full_name.toLowerCase().includes(searchLower) ||
                                                            u.unit_number?.toLowerCase().includes(searchLower)
                                                        );
                                                    }
                                                    return true;
                                                })
                                                .map((u) => (
                                                    <div
                                                        key={u.id}
                                                        className="flex items-center gap-2 p-2 rounded-md hover:bg-muted cursor-pointer"
                                                        onClick={() => handleAddMember(u.id)}
                                                    >
                                                        <Avatar className="h-8 w-8">
                                                            <AvatarFallback>{getInitials(u.full_name)}</AvatarFallback>
                                                        </Avatar>
                                                        <div className="flex-1">
                                                            <p className="text-sm font-medium">{u.full_name}</p>
                                                            <p className="text-xs text-muted-foreground">
                                                                {u.unit_number || 'No unit'} • {u.role}
                                                            </p>
                                                        </div>
                                                        <UserPlus className="h-4 w-4 text-muted-foreground"/>
                                                    </div>
                                                ))}
                                            {allUsers.filter((u) => {
                                                const isMember = selectedGroup?.members?.some(
                                                    (m) => m.user_id === u.id
                                                );
                                                return !isMember;
                                            }).length === 0 && (
                                                <div className="text-center text-muted-foreground py-8">
                                                    <Users className="h-12 w-12 mx-auto mb-2 opacity-20"/>
                                                    <p>All users are already members</p>
                                                </div>
                                            )}
                                        </ScrollArea>
                                    </div>
                                </DialogContent>
                            </Dialog>
                        </>
                    ) : (
                        <CardContent className="py-12">
                            <div className="text-center text-muted-foreground">
                                <MessageSquare className="h-16 w-16 mx-auto mb-4 opacity-20"/>
                                <p className="text-lg font-medium mb-2">Select a group to start chatting</p>
                                <p className="text-sm">Choose a group from the sidebar to view messages</p>
                            </div>
                        </CardContent>
                    )}
                </Card>
            </div>
        </div>
    );
};

export default ChatPage;
