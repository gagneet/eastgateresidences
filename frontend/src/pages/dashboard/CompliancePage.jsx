import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Calendar, CheckCircle2, FileCheck, History, Loader2 } from 'lucide-react';
import { formatDate } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: CompliancePage
 * Path: frontend/src/pages/dashboard/CompliancePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CompliancePage = () => {
    const {api, user} = useAuth();
    const isOwnerOnly = user?.role === 'owner' || user?.role === 'tenant';
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedItem, setSelectedItem] = useState(null);
    const [isUpdateDialogOpen, setIsUpdateDialogOpen] = useState(false);
    const [isHistoryDialogOpen, setIsHistoryDialogOpen] = useState(false);
    const [history, setHistory] = useState([]);
    const [loadingHistory, setLoadingHistory] = useState(false);
    const [updating, setUpdating] = useState(false);

    const [formData, setFormData] = useState({
        status: '',
        notes: '',
        due_date: ''
    });

    const fetchItems = useCallback(async () => {
        try {
            setLoading(true);
            const response = await api.get('/compliance-items');
            setItems(response.data || []);
        } catch (error) {
            console.error('Failed to fetch compliance items:', error);
            toast.error('Failed to load compliance checklist');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchItems();
    }, [fetchItems]);
    /**
     * @generated FunctionHeader
     * Function: handleUpdateClick
     * Path: frontend/src/pages/dashboard/CompliancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUpdateClick = (item) => {
        setSelectedItem(item);
        setFormData({
            status: item.status,
            notes: item.notes || '',
            due_date: item.due_date ? item.due_date.split('T')[ 0 ] : ''
        });
        setIsUpdateDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleHistoryClick
     * Path: frontend/src/pages/dashboard/CompliancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleHistoryClick = async (item) => {
        setSelectedItem(item);
        setIsHistoryDialogOpen(true);
        setLoadingHistory(true);
        try {
            const response = await api.get(`/compliance-items/${item.id}/history`);
            setHistory(response.data || []);
        } catch (error) {
            console.error('Failed to fetch history:', error);
            toast.error('Failed to load item history');
        } finally {
            setLoadingHistory(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveUpdate
     * Path: frontend/src/pages/dashboard/CompliancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveUpdate = async () => {
        if (!selectedItem) return;

        setUpdating(true);
        try {
            await api.put(`/compliance-items/${selectedItem.id}`, formData);
            toast.success('Compliance item updated');
            setIsUpdateDialogOpen(false);
            fetchItems();
        } catch (error) {
            console.error('Failed to update item:', error);
            toast.error('Failed to update compliance item');
        } finally {
            setUpdating(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getStatusBadge
     * Path: frontend/src/pages/dashboard/CompliancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusBadge = (status) => {
        switch (status) {
            case 'completed':
                return <Badge className="bg-green-100 text-green-800 border-green-200">Completed</Badge>;
            case 'in_progress':
                return <Badge className="bg-blue-100 text-blue-800 border-blue-200">In Progress</Badge>;
            case 'pending':
                return <Badge className="bg-orange-100 text-orange-800 border-orange-200">Pending</Badge>;
            case 'overdue':
                return <Badge variant="destructive">Overdue</Badge>;
            case 'not_applicable':
                return <Badge variant="secondary">N/A</Badge>;
            default:
                return <Badge variant="outline">{status}</Badge>;
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getPriorityBadge
     * Path: frontend/src/pages/dashboard/CompliancePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getPriorityBadge = (priority) => {
        switch (priority) {
            case 'critical':
                return <Badge variant="destructive" className="animate-pulse">Critical</Badge>;
            case 'high':
                return <Badge className="bg-red-100 text-red-800 border-red-200">High</Badge>;
            case 'medium':
                return <Badge className="bg-yellow-100 text-yellow-800 border-yellow-200">Medium</Badge>;
            default:
                return <Badge className="bg-gray-100 text-gray-800 border-gray-200">Low</Badge>;
        }
    };

    return (
        <div className="space-y-6" data-testid="compliance-page">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <FileCheck className="h-6 w-6 text-primary"/>
                        Compliance Checklist
                    </h1>
                    <p className="text-muted-foreground">
                        {isOwnerOnly ? 'View building regulatory and operational compliance status' : 'Monitor and manage building regulatory and operational compliance'}
                    </p>
                </div>
            </div>

            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle>Compliance Items</CardTitle>
                    <CardDescription>Track upcoming deadlines and maintenance certifications</CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-12">
                            <Loader2 className="h-8 w-8 animate-spin text-primary"/>
                        </div>
                    ) : items.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground">
                            <CheckCircle2 className="h-12 w-12 mx-auto mb-4 opacity-20"/>
                            <p>No compliance items found. All systems go!</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Item</TableHead>
                                        <TableHead>Category</TableHead>
                                        <TableHead>Priority</TableHead>
                                        <TableHead>Due Date</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {items.map((item) => (
                                        <TableRow key={item.id}>
                                            <TableCell>
                                                <div>
                                                    <div className="font-semibold">{item.title}</div>
                                                    <div
                                                        className="text-xs text-muted-foreground line-clamp-1">{item.description}</div>
                                                </div>
                                            </TableCell>
                                            <TableCell className="capitalize">{item.category}</TableCell>
                                            <TableCell>{getPriorityBadge(item.priority)}</TableCell>
                                            <TableCell>
                                                <div
                                                    className={`flex items-center gap-1 ${item.status === 'overdue' ? 'text-red-600 font-semibold' : ''}`}>
                                                    <Calendar className="h-3 w-3"/>
                                                    {formatDate(item.due_date)}
                                                </div>
                                            </TableCell>
                                            <TableCell>{getStatusBadge(item.status)}</TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-2">
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleHistoryClick(item)}
                                                        title="View History"
                                                    >
                                                        <History className="h-4 w-4"/>
                                                    </Button>
                                                    {!isOwnerOnly && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={() => handleUpdateClick(item)}
                                                        >
                                                            Update
                                                        </Button>
                                                    )}
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Update Dialog */}
            <Dialog open={isUpdateDialogOpen} onOpenChange={setIsUpdateDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Update Compliance Status</DialogTitle>
                        <DialogDescription>{selectedItem?.title}</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>Status</Label>
                            <Select
                                value={formData.status}
                                onValueChange={(val) => setFormData({...formData, status: val})}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select status"/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="pending">Pending</SelectItem>
                                    <SelectItem value="in_progress">In Progress</SelectItem>
                                    <SelectItem value="completed">Completed</SelectItem>
                                    <SelectItem value="overdue">Overdue</SelectItem>
                                    <SelectItem value="not_applicable">Not Applicable</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>Due Date</Label>
                            <Input
                                type="date"
                                value={formData.due_date}
                                onChange={(e) => setFormData({...formData, due_date: e.target.value})}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label>Notes</Label>
                            <Textarea
                                placeholder="Enter updates, certification numbers, or next steps..."
                                value={formData.notes}
                                onChange={(e) => setFormData({...formData, notes: e.target.value})}
                                rows={4}
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setIsUpdateDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleSaveUpdate} disabled={updating}>
                            {updating && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                            Save Changes
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* History Dialog */}
            <Dialog open={isHistoryDialogOpen} onOpenChange={setIsHistoryDialogOpen}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <History className="h-5 w-5"/>
                            Compliance History
                        </DialogTitle>
                        <DialogDescription>{selectedItem?.title}</DialogDescription>
                    </DialogHeader>
                    <div className="py-4 max-h-[60vh] overflow-y-auto">
                        {loadingHistory ? (
                            <div className="flex justify-center py-8">
                                <Loader2 className="h-6 w-6 animate-spin text-primary"/>
                            </div>
                        ) : history.length === 0 ? (
                            <div className="text-center py-8 text-muted-foreground">
                                No history found for this item.
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {history.map((log) => (
                                    <div key={log.id} className="border-l-2 border-primary/20 pl-4 py-1 relative">
                                        <div className="absolute w-2 h-2 rounded-full bg-primary -left-[5px] top-2"/>
                                        <div className="flex justify-between items-start">
                                            <p className="font-semibold text-sm">{log.user_name}</p>
                                            <span
                                                className="text-[10px] text-muted-foreground">{formatDate(log.created_at)}</span>
                                        </div>
                                        <div className="mt-1 text-sm">
                                            {log.details?.status && (
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span
                                                        className="text-xs text-muted-foreground">Status changed to:</span>
                                                    {getStatusBadge(log.details.status)}
                                                </div>
                                            )}
                                            {log.details?.notes && (
                                                <p className="text-muted-foreground italic text-xs mt-1">"{log.details.notes}"</p>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button onClick={() => setIsHistoryDialogOpen(false)}>Close</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default CompliancePage;
