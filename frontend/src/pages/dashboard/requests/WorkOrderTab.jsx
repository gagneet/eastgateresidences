import React, { useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { formatDate } from '../../../lib/utils';
import { ChevronRight, Loader2 } from 'lucide-react';
import Link from 'next/link';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';

const statusConfig = {
    draft: {label: 'Draft'},
    sent: {label: 'Sent'},
    accepted: {label: 'Accepted'},
    completed: {label: 'Completed'},
    cancelled: {label: 'Cancelled'},
};
/**
 * @generated FunctionHeader
 * Function: WorkOrderTab
 * Path: frontend/src/pages/dashboard/requests/WorkOrderTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const WorkOrderTab = () => {
    const {api} = useAuth();
    const [workOrders, setWorkOrders] = useState([]);
    const [loading, setLoading] = useState(true);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: workOrders,
        statusConfig,
        tab: 'work-order',
    });

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchWorkOrders
         * Path: frontend/src/pages/dashboard/requests/WorkOrderTab.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchWorkOrders = async () => {
            try {
                const response = await api.get('/work-orders');
                setWorkOrders(response.data);
            } catch (error) {
                console.error('Failed to fetch work orders:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchWorkOrders();
    }, [api]);

    if (loading) return <div className="flex justify-center p-8"><Loader2
        className="h-6 w-6 animate-spin text-muted-foreground"/></div>;

    return (
        <div className="space-y-4">
            <div className="flex justify-end">
                <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
            </div>
            {filteredRecords.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground border-2 border-dashed rounded-lg">
                    {statusFilter === 'all' ? 'No work orders associated with your property' : 'No work orders found for this status'}
                </div>
            ) : (
                filteredRecords.map(wo => (
                    <Link key={wo.id} href={`/maintenance/work-order/${wo.id}`} className="block group">
                        <Card className="hover:border-primary transition-colors">
                            <CardContent className="p-4 flex items-center justify-between">
                                <div>
                                    <div className="flex items-center gap-2 mb-1">
                                        <h4 className="font-semibold text-sm group-hover:text-primary transition-colors">{wo.title}</h4>
                                        <Badge variant="outline" className="text-[10px] uppercase">{wo.status}</Badge>
                                        {wo.is_emergency && <Badge variant="destructive"
                                                                   className="text-[10px] uppercase">Emergency</Badge>}
                                    </div>
                                    <p className="text-xs text-muted-foreground line-clamp-1">{wo.description}</p>
                                    <div
                                        className="flex gap-3 mt-2 text-[10px] text-muted-foreground font-medium uppercase tracking-tight">
                                        <span>{formatDate(wo.created_at)}</span>
                                        <span>•</span>
                                        <span>{wo.vendor_name || 'Unassigned'}</span>
                                    </div>
                                </div>
                                <ChevronRight
                                    className="h-4 w-4 text-muted-foreground group-hover:translate-x-1 transition-transform"/>
                            </CardContent>
                        </Card>
                    </Link>
                ))
            )}
        </div>
    );
};

export default WorkOrderTab;
