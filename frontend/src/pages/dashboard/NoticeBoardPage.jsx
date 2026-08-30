import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger
} from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { Label } from '../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Checkbox } from '../../components/ui/checkbox';
import {
    AlertCircle,
    Bell,
    Calendar,
    CheckCircle,
    History,
    Loader2,
    MessageSquare,
    Pin,
    Plus,
    Trash2
} from 'lucide-react';
import { formatDate, priorityConfig } from '../../lib/utils';
import { toast } from 'sonner';

const ALL_ROLES = [
    {id: 'owner', label: 'Owners'},
    {id: 'tenant', label: 'Tenants'},
    {id: 'ec_member', label: 'EC Members'},
    {id: 'strata_manager', label: 'Strata Manager'},
    {id: 'service_provider', label: 'Service Providers'},
    {id: 'guest', label: 'Guests'}
];
/**
 * @generated FunctionHeader
 * Function: NoticeBoardPage
 * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const NoticeBoardPage = () => {
    const {api, hasPermission, user} = useAuth();
    const [notices, setNotices] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedNotice, setSelectedNotice] = useState(null);
    const [comments, setComments] = useState([]);
    const [newComment, setNewComment] = useState('');
    const [showCreateDialog, setShowCreateDialog] = useState(false);
    const [showExpiryDialog, setShowExpiryDialog] = useState(false);
    const [showHistoryDialog, setShowHistoryDialog] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [broadcastToAll, setBroadcastToAll] = useState(false);
    const [formData, setFormData] = useState({
        title: '',
        content: '',
        category: 'general',
        priority: 'normal',
        is_pinned: false,
        requires_acknowledgment: false,
        expires_at: '',
        target_roles: []
    });

    const canManageNotices = hasPermission('can_post_announcements');
    const canBroadcast = ['super_admin', 'strata_manager'].includes(user?.role || '');

    useEffect(() => {
        fetchNotices();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: fetchNotices
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchNotices = async () => {
        try {
            const response = await api.get('/notices');
            setNotices(response.data);
        } catch (error) {
            console.error('Failed to fetch notices:', error);
            toast.error('Failed to load notices');
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreateNotice
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateNotice = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const payload = {
                ...formData,
                target_roles: formData.target_roles.length > 0 ? formData.target_roles : null
            };
            if (broadcastToAll && canBroadcast) {
                await api.post('/notices/broadcast', payload);
                toast.success('Notice broadcast successfully');
            } else {
                await api.post('/notices', payload);
                toast.success('Notice created successfully');
            }
            setShowCreateDialog(false);
            setBroadcastToAll(false);
            setFormData({
                title: '',
                content: '',
                category: 'general',
                priority: 'normal',
                is_pinned: false,
                requires_acknowledgment: false,
                expires_at: '',
                target_roles: []
            });
            fetchNotices();
        } catch (error) {
            console.error('Failed to create notice:', error);
            toast.error('Failed to create notice');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleExpiryUpdate
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExpiryUpdate = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.patch(`/notices/${selectedNotice.id}/expiry`, {
                expires_at: formData.expires_at
            });
            toast.success('Expiry date updated');
            setShowExpiryDialog(false);
            fetchNotices();
        } catch (error) {
            toast.error('Failed to update expiry date');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id) => {
        if (!window.confirm('Are you sure you want to delete this notice?')) return;
        try {
            await api.delete(`/notices/${id}`);
            toast.success('Notice deleted');
            fetchNotices();
        } catch (error) {
            toast.error('Failed to delete notice');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleTogglePin
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleTogglePin = async (noticeId) => {
        try {
            await api.put(`/notices/${noticeId}/pin`);
            toast.success('Notice pin status updated');
            fetchNotices();
        } catch (error) {
            console.error('Failed to toggle pin:', error);
            toast.error('Failed to update pin status');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAcknowledge
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAcknowledge = async (noticeId) => {
        try {
            await api.post(`/notices/${noticeId}/acknowledge`);
            toast.success('Notice acknowledged');
            fetchNotices();
        } catch (error) {
            console.error('Failed to acknowledge notice:', error);
            toast.error('Failed to acknowledge notice');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: loadComments
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const loadComments = async (noticeId) => {
        try {
            const response = await api.get(`/notices/${noticeId}/comments`);
            setComments(response.data);
        } catch (error) {
            console.error('Failed to load comments:', error);
            toast.error('Failed to load comments');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAddComment
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddComment = async (noticeId) => {
        if (!newComment.trim()) return;

        try {
            await api.post(`/notices/${noticeId}/comments`, {content: newComment});
            setNewComment('');
            loadComments(noticeId);
            toast.success('Comment added');
            fetchNotices(); // Refresh to update comment count
        } catch (error) {
            console.error('Failed to add comment:', error);
            toast.error('Failed to add comment');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openNoticeDialog
     * Path: frontend/src/pages/dashboard/NoticeBoardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openNoticeDialog = (notice) => {
        setSelectedNotice(notice);
        loadComments(notice.id);
    };

    const categoryColors = {
        general: 'bg-blue-100 text-blue-800',
        maintenance: 'bg-orange-100 text-orange-800',
        financial: 'bg-green-100 text-green-800',
        legal: 'bg-purple-100 text-purple-800',
        meeting: 'bg-pink-100 text-pink-800'
    };

    return (
        <div className="space-y-6" data-testid="notice-board-page">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Notice Board</h1>
                    <p className="text-muted-foreground">Official notices and community discussions</p>
                </div>
                {canManageNotices && (
                    <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
                        <DialogTrigger asChild>
                            <Button>
                                <Plus className="h-4 w-4 mr-2"/>
                                Post Notice
                            </Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-2xl">
                            <DialogHeader>
                                <DialogTitle>Create New Notice</DialogTitle>
                            </DialogHeader>
                            <form onSubmit={handleCreateNotice} className="space-y-4">
                                <div>
                                    <Label htmlFor="title">Title</Label>
                                    <Input
                                        id="title"
                                        value={formData.title}
                                        onChange={(e) => setFormData({...formData, title: e.target.value})}
                                        required
                                    />
                                </div>
                                <div>
                                    <Label htmlFor="content">Content</Label>
                                    <Textarea
                                        id="content"
                                        value={formData.content}
                                        onChange={(e) => setFormData({...formData, content: e.target.value})}
                                        rows={6}
                                        required
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <Label htmlFor="category">Category</Label>
                                        <Select
                                            value={formData.category}
                                            onValueChange={(value) => setFormData({...formData, category: value})}
                                        >
                                            <SelectTrigger id="category">
                                                <SelectValue/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="general">General</SelectItem>
                                                <SelectItem value="maintenance">Maintenance</SelectItem>
                                                <SelectItem value="financial">Financial</SelectItem>
                                                <SelectItem value="legal">Legal</SelectItem>
                                                <SelectItem value="meeting">Meeting</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div>
                                        <Label htmlFor="priority">Priority</Label>
                                        <Select
                                            value={formData.priority}
                                            onValueChange={(value) => setFormData({...formData, priority: value})}
                                        >
                                            <SelectTrigger id="priority">
                                                <SelectValue/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="low">Low</SelectItem>
                                                <SelectItem value="normal">Normal</SelectItem>
                                                <SelectItem value="high">High</SelectItem>
                                                <SelectItem value="urgent">Urgent</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="flex items-center space-x-2 py-2">
                                        <Checkbox
                                            id="is_pinned"
                                            checked={formData.is_pinned}
                                            onCheckedChange={v => setFormData({...formData, is_pinned: v})}
                                        />
                                        <Label htmlFor="is_pinned">Pin to top</Label>
                                    </div>
                                    <div className="flex items-center space-x-2 py-2">
                                        <Checkbox
                                            id="requires_acknowledgment"
                                            checked={formData.requires_acknowledgment}
                                            onCheckedChange={v => setFormData({
                                                ...formData,
                                                requires_acknowledgment: v
                                            })}
                                        />
                                        <Label htmlFor="requires_acknowledgment">Require acknowledgment</Label>
                                    </div>
                                </div>

                                <div>
                                    <Label htmlFor="expires_at">Expiration Date (optional)</Label>
                                    <Input
                                        id="expires_at"
                                        type="datetime-local"
                                        value={formData.expires_at}
                                        onChange={(e) => setFormData({...formData, expires_at: e.target.value})}
                                    />
                                </div>

                                <div className="space-y-2 border-t pt-4">
                                    <Label>Target Roles (Leave empty for all residents)</Label>
                                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                        {ALL_ROLES.map(role => (
                                            <div key={role.id} className="flex items-center space-x-2">
                                                <Checkbox
                                                    id={`role-${role.id}`}
                                                    checked={formData.target_roles.includes(role.id)}
                                                    onCheckedChange={(checked) => {
                                                        const roles = checked
                                                            ? [...formData.target_roles, role.id]
                                                            : formData.target_roles.filter(r => r !== role.id);
                                                        setFormData({...formData, target_roles: roles});
                                                    }}
                                                />
                                                <label htmlFor={`role-${role.id}`}
                                                       className="text-sm">{role.label}</label>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                {canBroadcast && (
                                    <div className="flex items-center space-x-2 rounded-md border p-3">
                                        <Checkbox
                                            id="broadcast_to_all"
                                            checked={broadcastToAll}
                                            onCheckedChange={v => setBroadcastToAll(!!v)}
                                        />
                                        <Label htmlFor="broadcast_to_all">Broadcast to all accessible buildings</Label>
                                    </div>
                                )}

                                <div className="flex justify-end space-x-2 pt-4">
                                    <Button type="button" variant="outline" onClick={() => setShowCreateDialog(false)}>
                                        Cancel
                                    </Button>
                                    <Button type="submit" disabled={submitting}>
                                        {submitting ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> : null}
                                        {broadcastToAll && canBroadcast ? 'Broadcast Notice' : 'Create Notice'}
                                    </Button>
                                </div>
                            </form>
                        </DialogContent>
                    </Dialog>
                )}
            </div>

            {loading ? (
                <div className="space-y-4">
                    {[1, 2, 3].map((i) => (
                        <Card key={i} className="card-dashboard">
                            <CardContent className="p-6">
                                <div className="skeleton h-6 w-1/4 mb-4"/>
                                <div className="skeleton h-4 w-full mb-2"/>
                                <div className="skeleton h-4 w-2/3"/>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : notices.length === 0 ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <Bell className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Notices</h3>
                        <p className="text-muted-foreground">
                            There are no active notices at this time.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-4">
                    {notices.map((notice) => {
                        const priority = priorityConfig[ notice.priority ] || priorityConfig.normal;
                        const categoryColor = categoryColors[ notice.category ] || categoryColors.general;

                        return (
                            <Card
                                key={notice.id}
                                className={`card-dashboard cursor-pointer hover:shadow-lg transition-shadow ${notice.is_pinned ? 'border-2 border-primary' : ''}`}
                                onClick={() => openNoticeDialog(notice)}
                                data-testid={`notice-${notice.id}`}
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-start justify-between gap-4 mb-4">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            {notice.is_pinned && (
                                                <Badge variant="default" className="bg-primary">
                                                    <Pin className="h-3 w-3 mr-1"/>
                                                    Pinned
                                                </Badge>
                                            )}
                                            <Badge className={priority.class}>
                                                {priority.label}
                                            </Badge>
                                            <Badge className={categoryColor}>
                                                {notice.category}
                                            </Badge>
                                            {notice.requires_acknowledgment && (
                                                <Badge variant="outline" className="text-amber-600 border-amber-600">
                                                    <AlertCircle className="h-3 w-3 mr-1"/>
                                                    Acknowledgment Required
                                                </Badge>
                                            )}
                                        </div>
                                        <span className="text-sm text-muted-foreground whitespace-nowrap">
                      {formatDate(notice.created_at)}
                    </span>
                                    </div>
                                    <h3 className="text-lg font-semibold mb-2">{notice.title}</h3>
                                    <p className="text-muted-foreground line-clamp-2 mb-4">{notice.content}</p>
                                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                                        <span>Posted by {notice.created_by_name}</span>
                                        <span className="flex items-center gap-1">
                      <MessageSquare className="h-4 w-4"/>
                                            {notice.comment_count} comments
                    </span>
                                        {notice.requires_acknowledgment && (
                                            <span className="flex items-center gap-1">
                        <CheckCircle className="h-4 w-4"/>
                                                {notice.acknowledgment_count} acknowledged
                      </span>
                                        )}
                                    </div>
                                    {canManageNotices && (
                                        <div className="mt-4 pt-4 border-t flex items-center justify-between">
                                            <div className="flex gap-2">
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleTogglePin(notice.id);
                                                    }}
                                                >
                                                    <Pin className="h-4 w-4 mr-1"/>
                                                    {notice.is_pinned ? 'Unpin' : 'Pin'}
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        setSelectedNotice(notice);
                                                        setFormData({...formData, expires_at: notice.expires_at || ''});
                                                        setShowExpiryDialog(true);
                                                    }}
                                                >
                                                    <Calendar className="h-4 w-4 mr-1"/>
                                                    Expiry
                                                </Button>
                                            </div>
                                            <div className="flex gap-1">
                                                <Button
                                                    size="icon"
                                                    variant="ghost"
                                                    className="h-8 w-8 text-purple-600"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        setSelectedNotice(notice);
                                                        setShowHistoryDialog(true);
                                                    }}
                                                >
                                                    <History className="h-4 w-4"/>
                                                </Button>
                                                <Button
                                                    size="icon"
                                                    variant="ghost"
                                                    className="h-8 w-8 text-red-600"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleDelete(notice.id);
                                                    }}
                                                >
                                                    <Trash2 className="h-4 w-4"/>
                                                </Button>
                                            </div>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}

            {/* Notice Detail Dialog */}
            <Dialog open={!!selectedNotice} onOpenChange={(open) => !open && setSelectedNotice(null)}>
                <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
                    {selectedNotice && (
                        <>
                            <DialogHeader>
                                <div className="space-y-3">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        {selectedNotice.is_pinned && (
                                            <Badge variant="default" className="bg-primary">
                                                <Pin className="h-3 w-3 mr-1"/>
                                                Pinned
                                            </Badge>
                                        )}
                                        <Badge className={priorityConfig[ selectedNotice.priority ]?.class}>
                                            {priorityConfig[ selectedNotice.priority ]?.label}
                                        </Badge>
                                        <Badge className={categoryColors[ selectedNotice.category ]}>
                                            {selectedNotice.category}
                                        </Badge>
                                    </div>
                                    <DialogTitle className="text-2xl">{selectedNotice.title}</DialogTitle>
                                    <p className="text-sm text-muted-foreground">
                                        Posted
                                        by {selectedNotice.created_by_name} on {formatDate(selectedNotice.created_at)}
                                    </p>
                                </div>
                            </DialogHeader>

                            <div className="space-y-6">
                                <div className="prose max-w-none">
                                    <p className="whitespace-pre-wrap">{selectedNotice.content}</p>
                                </div>

                                {selectedNotice.requires_acknowledgment && (
                                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                                        <div className="flex items-start gap-3">
                                            <AlertCircle className="h-5 w-5 text-amber-600 mt-0.5"/>
                                            <div className="flex-1">
                                                <h4 className="font-semibold text-amber-900 mb-2">
                                                    Acknowledgment Required
                                                </h4>
                                                <p className="text-sm text-amber-800 mb-3">
                                                    This notice requires your acknowledgment. Please confirm that you
                                                    have read and understood this notice.
                                                </p>
                                                <Button
                                                    size="sm"
                                                    onClick={() => handleAcknowledge(selectedNotice.id)}
                                                    className="bg-amber-600 hover:bg-amber-700"
                                                >
                                                    <CheckCircle className="h-4 w-4 mr-2"/>
                                                    Acknowledge
                                                </Button>
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {/* Comments Section */}
                                <div className="border-t pt-6">
                                    <h3 className="text-lg font-semibold mb-4">
                                        Discussion ({comments.length})
                                    </h3>

                                    {/* Add Comment */}
                                    <div className="mb-6">
                                        <Textarea
                                            placeholder="Add a comment..."
                                            value={newComment}
                                            onChange={(e) => setNewComment(e.target.value)}
                                            rows={3}
                                        />
                                        <div className="mt-2 flex justify-end">
                                            <Button
                                                size="sm"
                                                onClick={() => handleAddComment(selectedNotice.id)}
                                                disabled={!newComment.trim()}
                                            >
                                                Post Comment
                                            </Button>
                                        </div>
                                    </div>

                                    {/* Comments List */}
                                    <div className="space-y-4">
                                        {comments.map((comment) => (
                                            <Card key={comment.id} className="bg-muted/50">
                                                <CardContent className="p-4">
                                                    <div className="flex items-start justify-between mb-2">
                                                        <span className="font-medium">{comment.author_name}</span>
                                                        <span className="text-xs text-muted-foreground">
                              {formatDate(comment.created_at)}
                            </span>
                                                    </div>
                                                    <p className="text-sm whitespace-pre-wrap">{comment.content}</p>
                                                </CardContent>
                                            </Card>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </>
                    )}
                </DialogContent>
            </Dialog>

            {/* Edit Expiry Dialog */}
            <Dialog open={showExpiryDialog} onOpenChange={setShowExpiryDialog}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Update Expiry Date</DialogTitle>
                        <DialogDescription>Only the expiry date can be modified for an active
                            notice.</DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleExpiryUpdate} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="edit_notice_expires_at">New Expiry Date</Label>
                            <Input
                                id="edit_notice_expires_at"
                                type="datetime-local"
                                value={formData.expires_at}
                                onChange={e => setFormData({...formData, expires_at: e.target.value})}
                                required
                            />
                            <p className="text-xs text-muted-foreground">Set to a past date to remove the notice
                                immediately.</p>
                        </div>
                        <DialogFooter className="gap-2">
                            <Button type="button" variant="outline"
                                    onClick={() => setShowExpiryDialog(false)}>Cancel</Button>
                            <Button type="submit" disabled={submitting}>
                                {submitting ? 'Updating...' : 'Update Expiry'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            {/* History Dialog */}
            <Dialog open={showHistoryDialog} onOpenChange={setShowHistoryDialog}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Notice History</DialogTitle>
                        <DialogDescription>Audit trail for "{selectedNotice?.title}"</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4 max-h-[60vh] overflow-y-auto">
                        {selectedNotice?.history?.map((entry, idx) => (
                            <div key={idx} className="border-l-2 border-primary pl-4 py-1">
                                <p className="text-sm font-semibold capitalize">{entry.action.replace('_', ' ')}</p>
                                <p className="text-xs text-muted-foreground">By {entry.user_name} on {formatDate(entry.timestamp)}</p>
                                {entry.old_expiry !== undefined && (
                                    <p className="text-[10px] mt-1 italic text-muted-foreground">
                                        Changed from {entry.old_expiry || 'None'} to {entry.new_expiry || 'None'}
                                    </p>
                                )}
                            </div>
                        ))}
                        {( !selectedNotice?.history || selectedNotice.history.length === 0 ) && (
                            <p className="text-sm text-muted-foreground text-center py-4">No history available</p>
                        )}
                    </div>
                    <DialogFooter>
                        <Button onClick={() => setShowHistoryDialog(false)}>Close</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default NoticeBoardPage;
