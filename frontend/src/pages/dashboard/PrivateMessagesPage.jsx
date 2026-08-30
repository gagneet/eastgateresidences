import React, { useEffect, useRef, useState } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Avatar, AvatarFallback } from '../../components/ui/avatar';
import { ScrollArea } from '../../components/ui/scroll-area';
import { Badge } from '../../components/ui/badge';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Label } from '../../components/ui/label';
import { Loader2, MessageSquare, Plus, Send, Users } from 'lucide-react';
import { formatDateTime, getInitials } from '../../lib/utils';
import { toast } from 'sonner';
import { Textarea } from '../../components/ui/textarea';
import { Popover, PopoverContent, PopoverTrigger, } from "../../components/ui/popover";
/**
 * @generated FunctionHeader
 * Function: PrivateMessagesPage
 * Path: frontend/src/pages/dashboard/PrivateMessagesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PrivateMessagesPage = () => {
    const {user, api, hasPermission} = useAuth();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const [conversations, setConversations] = useState([]);
    const [selectedConv, setSelectedConv] = useState(null);
    const [messages, setMessages] = useState([]);
    const [newMessage, setNewMessage] = useState('');
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [sending, setSending] = useState(false);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [newConvData, setNewConvData] = useState({
        name: '',
        is_group: false,
        member_ids: []
    });
    const messagesEndRef = useRef(null);
    const textareaRef = useRef(null);

    // Auto-expand textarea
    useEffect(() => {
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
            textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
        }
    }, [newMessage]);

    const fetchConversations = React.useCallback(async () => {
        try {
            const response = await api.get('/conversations');
            setConversations(response.data);
        } catch (error) {
            console.error('Failed to fetch conversations:', error);
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchUsers = React.useCallback(async () => {
        if (hasPermission('can_manage_users')) {
            try {
                const response = await api.get('/users');
                setUsers(response.data.filter(u => u.id !== user?.id));
            } catch (error) {
                console.error('Failed to fetch users:', error);
            }
        }
    }, [api, hasPermission, user?.id]);

    const fetchMessages = React.useCallback(async (convId) => {
        try {
            const response = await api.get(`/conversations/${convId}/messages`);
            setMessages(response.data);
        } catch (error) {
            console.error('Failed to fetch messages:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchConversations();
        fetchUsers();
    }, [fetchConversations, fetchUsers]);

    // Auto-select conversation if passed via query param
    useEffect(() => {
        const convId = searchParams.get('conversationId');
        if (convId && conversations.length > 0 && !selectedConv) {
            const conversation = conversations.find(c => c.id === convId);
            if (conversation) {
                setSelectedConv(conversation);
            }
        }
    }, [searchParams, conversations, selectedConv]);

    useEffect(() => {
        if (selectedConv) {
            fetchMessages(selectedConv.id);
            const interval = setInterval(() => fetchMessages(selectedConv.id), 5000);
            return () => clearInterval(interval);
        }
    }, [selectedConv, fetchMessages]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({behavior: 'smooth'});
    }, [messages]);
    /**
     * @generated FunctionHeader
     * Function: handleSend
     * Path: frontend/src/pages/dashboard/PrivateMessagesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSend = async (e) => {
        e.preventDefault();
        if (!newMessage.trim() || !selectedConv || sending) return;

        setSending(true);
        try {
            await api.post(`/conversations/${selectedConv.id}/messages`, {
                content: newMessage.trim(),
                conversation_id: selectedConv.id
            });
            setNewMessage('');
            await fetchMessages(selectedConv.id);
            await fetchConversations();
        } catch (error) {
            toast.error('Failed to send message');
        } finally {
            setSending(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreateConversation
     * Path: frontend/src/pages/dashboard/PrivateMessagesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateConversation = async (e) => {
        e.preventDefault();
        if (newConvData.member_ids.length === 0) {
            toast.error('Please select at least one member');
            return;
        }

        try {
            const response = await api.post('/conversations', newConvData);
            setConversations(prev => [response.data, ...prev]);
            setSelectedConv(response.data);
            setDialogOpen(false);
            setNewConvData({name: '', is_group: false, member_ids: []});
            toast.success('Conversation created');
        } catch (error) {
            toast.error('Failed to create conversation');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getConversationName
     * Path: frontend/src/pages/dashboard/PrivateMessagesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getConversationName = (conv) => {
        if (conv.name) return conv.name;
        const otherMembers = conv.members?.filter(m => m.id !== user?.id);
        return otherMembers?.map(m => m.full_name).join(', ') || 'New Conversation';
    };

    return (
        <div className="h-[calc(100vh-8rem)] flex flex-row gap-2 lg:gap-4" data-testid="private-messages-page">
            {/* Conversations List */}
            <Card className="card-dashboard w-16 lg:w-80 flex flex-col shrink-0 overflow-hidden">
                <CardHeader className="pb-3 px-2 lg:px-6 border-b border-border">
                    <div className="flex items-center justify-center lg:justify-between">
                        <CardTitle className="text-lg hidden lg:block">Messages</CardTitle>
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogTrigger asChild>
                                <Button size="sm" variant="outline" data-testid="new-conversation-btn"
                                        aria-label="New Conversation">
                                    <Plus className="h-4 w-4"/>
                                </Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>New Conversation</DialogTitle>
                                    <DialogDescription>
                                        Start a new direct message or group chat
                                    </DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleCreateConversation} className="space-y-4">
                                    <div className="flex items-center gap-2">
                                        <input
                                            type="checkbox"
                                            id="is_group"
                                            checked={newConvData.is_group}
                                            onChange={(e) => setNewConvData(prev => ( {
                                                ...prev,
                                                is_group: e.target.checked
                                            } ))}
                                            className="rounded"
                                        />
                                        <Label htmlFor="is_group">Create group chat</Label>
                                    </div>

                                    {newConvData.is_group && (
                                        <div className="space-y-2">
                                            <Label htmlFor="name">Group Name</Label>
                                            <Input
                                                id="name"
                                                value={newConvData.name}
                                                onChange={(e) => setNewConvData(prev => ( {
                                                    ...prev,
                                                    name: e.target.value
                                                } ))}
                                                placeholder="e.g., Building Updates"
                                            />
                                        </div>
                                    )}

                                    <div className="space-y-2">
                                        <Label>Select Members</Label>
                                        <div className="max-h-48 overflow-y-auto border rounded-lg p-2 space-y-2">
                                            {users.map((u) => (
                                                <label
                                                    key={u.id}
                                                    className="flex items-center gap-3 p-2 rounded hover:bg-muted cursor-pointer"
                                                >
                                                    <input
                                                        type="checkbox"
                                                        checked={newConvData.member_ids.includes(u.id)}
                                                        onChange={(e) => {
                                                            if (e.target.checked) {
                                                                setNewConvData(prev => ( {
                                                                    ...prev,
                                                                    member_ids: [...prev.member_ids, u.id]
                                                                } ));
                                                            } else {
                                                                setNewConvData(prev => ( {
                                                                    ...prev,
                                                                    member_ids: prev.member_ids.filter(id => id !== u.id)
                                                                } ));
                                                            }
                                                        }}
                                                        className="rounded"
                                                    />
                                                    <Avatar className="h-8 w-8">
                                                        <AvatarFallback className="text-xs bg-primary/10 text-primary">
                                                            {getInitials(u.full_name)}
                                                        </AvatarFallback>
                                                    </Avatar>
                                                    <div>
                                                        <p className="text-sm font-medium">{u.full_name}</p>
                                                        <p className="text-xs text-muted-foreground">{u.unit_number || 'No unit'}</p>
                                                    </div>
                                                </label>
                                            ))}
                                        </div>
                                    </div>

                                    <Button type="submit" className="w-full">
                                        Create Conversation
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    </div>
                </CardHeader>

                <ScrollArea className="flex-1">
                    {loading ? (
                        <div className="p-4 space-y-3">
                            {[1, 2, 3].map((i) => (
                                <div key={i} className="flex items-center gap-3 p-3">
                                    <div className="skeleton w-10 h-10 rounded-full"/>
                                    <div className="flex-1">
                                        <div className="skeleton h-4 w-24 mb-2"/>
                                        <div className="skeleton h-3 w-32"/>
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : conversations.length === 0 ? (
                        <div className="p-6 text-center">
                            <MessageSquare className="h-12 w-12 text-muted-foreground/50 mx-auto mb-3"/>
                            <p className="text-sm text-muted-foreground">No conversations yet</p>
                            <Button
                                variant="link"
                                className="mt-2"
                                onClick={() => setDialogOpen(true)}
                            >
                                Start a conversation
                            </Button>
                        </div>
                    ) : (
                        <div className="p-1 lg:p-2 space-y-1">
                            {conversations.map((conv) => {
                                const convName = getConversationName(conv);
                                const buttonContent = (
                                    <button
                                        onClick={() => setSelectedConv(conv)}
                                        className={`w-full flex items-center justify-center lg:justify-start gap-3 p-2 lg:p-3 rounded-lg text-left transition-colors ${
                                            selectedConv?.id === conv.id
                                                ? 'bg-primary/10'
                                                : 'hover:bg-muted'
                                        }`}
                                        data-testid={`conversation-${conv.id}`}
                                    >
                                        <Avatar className="h-10 w-10">
                                            {conv.is_group ? (
                                                <AvatarFallback className="bg-primary text-primary-foreground">
                                                    <Users className="h-5 w-5"/>
                                                </AvatarFallback>
                                            ) : (
                                                <AvatarFallback className="bg-primary/10 text-primary">
                                                    {getInitials(convName)}
                                                </AvatarFallback>
                                            )}
                                        </Avatar>
                                        <div className="flex-1 min-w-0 hidden lg:block">
                                            <div className="flex items-center gap-2">
                                                <p className="font-medium text-sm truncate">
                                                    {convName}
                                                </p>
                                                {conv.is_group && (
                                                    <Badge variant="secondary" className="text-xs">Group</Badge>
                                                )}
                                            </div>
                                            {conv.last_message && (
                                                <p className="text-xs text-muted-foreground truncate">
                                                    {conv.last_message.content}
                                                </p>
                                            )}
                                        </div>
                                    </button>
                                );

                                return (
                                    <div key={conv.id}>
                                        <div className="lg:hidden">
                                            <Popover>
                                                <PopoverTrigger asChild>
                                                    {buttonContent}
                                                </PopoverTrigger>
                                                <PopoverContent side="right" className="w-auto p-2">
                                                    <p className="text-sm font-semibold">{convName}</p>
                                                    {conv.is_group &&
                                                        <p className="text-xs text-muted-foreground">{conv.members?.length} members</p>}
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
            </Card>

            {/* Messages Area */}
            <Card className="card-dashboard flex-1 flex flex-col">
                {selectedConv ? (
                    <>
                        <CardHeader className="pb-3 border-b border-border">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <Avatar className="h-10 w-10">
                                        {selectedConv.is_group ? (
                                            <AvatarFallback className="bg-primary text-primary-foreground">
                                                <Users className="h-5 w-5"/>
                                            </AvatarFallback>
                                        ) : (
                                            <AvatarFallback className="bg-primary/10 text-primary">
                                                {getInitials(getConversationName(selectedConv))}
                                            </AvatarFallback>
                                        )}
                                    </Avatar>
                                    <div>
                                        <CardTitle className="text-lg">{getConversationName(selectedConv)}</CardTitle>
                                        <p className="text-xs text-muted-foreground">
                                            {selectedConv.members?.length} members
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </CardHeader>

                        <ScrollArea className="flex-1 p-4">
                            <div className="space-y-4">
                                {messages.map((message) => {
                                    const isOwn = message.sender_id === user?.id;
                                    return (
                                        <div
                                            key={message.id}
                                            className={`flex gap-3 ${isOwn ? 'flex-row-reverse' : ''}`}
                                        >
                                            {!isOwn && (
                                                <Avatar className="h-8 w-8 shrink-0">
                                                    <AvatarFallback className="bg-primary/10 text-primary text-xs">
                                                        {getInitials(message.sender_name)}
                                                    </AvatarFallback>
                                                </Avatar>
                                            )}
                                            <div className={`flex flex-col ${isOwn ? 'items-end' : 'items-start'}`}>
                                                {!isOwn && (
                                                    <span className="text-xs text-muted-foreground mb-1 px-2">
                            {message.sender_name}
                          </span>
                                                )}
                                                <div
                                                    className={`chat-bubble ${
                                                        isOwn ? 'chat-bubble-sent ml-auto' : 'chat-bubble-received'
                                                    }`}
                                                >
                                                    <p className="text-sm whitespace-pre-wrap break-words">
                                                        {message.content}
                                                    </p>
                                                </div>
                                                <span className="text-xs text-muted-foreground mt-1 px-2">
                          {formatDateTime(message.created_at)}
                        </span>
                                            </div>
                                        </div>
                                    );
                                })}
                                <div ref={messagesEndRef}/>
                            </div>
                        </ScrollArea>

                        <div className="p-4 border-t border-border">
                            <form onSubmit={handleSend} className="flex items-end gap-2">
                                <Textarea
                                    ref={textareaRef}
                                    value={newMessage}
                                    onChange={(e) => setNewMessage(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter' && !e.shiftKey) {
                                            e.preventDefault();
                                            handleSend(e);
                                        }
                                    }}
                                    placeholder="Type your message..."
                                    className="flex-1 min-h-[40px] max-h-[120px] resize-none py-2"
                                    disabled={sending}
                                    data-testid="private-message-input"
                                />
                                <Button
                                    type="submit"
                                    disabled={!newMessage.trim() || sending}
                                    size="icon"
                                    className="shrink-0"
                                    aria-label={sending ? "Sending message..." : "Send message"}
                                >
                                    {sending ? <Loader2 className="h-4 w-4 animate-spin"/> :
                                        <Send className="h-4 w-4"/>}
                                </Button>
                            </form>
                        </div>
                    </>
                ) : (
                    <div className="flex-1 flex items-center justify-center">
                        <div className="text-center">
                            <MessageSquare className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                            <h3 className="text-lg font-medium mb-2">Select a Conversation</h3>
                            <p className="text-muted-foreground mb-4">
                                Choose a conversation from the list or start a new one
                            </p>
                            <Button onClick={() => setDialogOpen(true)}>
                                <Plus className="mr-2 h-4 w-4"/>
                                New Conversation
                            </Button>
                        </div>
                    </div>
                )}
            </Card>
        </div>
    );
};

export default PrivateMessagesPage;
