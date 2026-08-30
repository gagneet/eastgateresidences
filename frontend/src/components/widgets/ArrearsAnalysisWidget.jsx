"use client";
import React from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { AlertTriangle, ArrowRight, Download, TrendingUp } from 'lucide-react';
import { formatCurrency } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * ArrearsAnalysisWidget - Displays arrears breakdown with visual indicators
 * Shows units in arrears, amounts, aging analysis, and top debtors
 *
 * @param {Object} data - Arrears data object
 * @param {Function} onViewDetails - Handler for view details button
 * @param {Function} onExport - Optional export handler
 */
const ArrearsAnalysisWidget = ({
                                   data = {
                                       total_units: 87,
                                       units_in_arrears: 0,
                                       total_arrears: 0,
                                       by_aging: {
                                           current: 0,
                                           '30_days': 0,
                                           '60_days': 0,
                                           '90_plus_days': 0
                                       },
                                       top_arrears: []
                                   },
                                   onViewDetails = null,
                                   onExport = null,
                                   showTopArrears = true
                               }) => {
    const arrearsPercentage = data.total_units > 0
        ? ( ( data.units_in_arrears / data.total_units ) * 100 ).toFixed(1)
        : 0;
    /**
     * @generated FunctionHeader
     * Function: getStatusColor
     * Path: frontend/src/components/widgets/ArrearsAnalysisWidget.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusColor = () => {
        if (arrearsPercentage < 5) return 'text-green-600 bg-green-50 border-green-200';
        if (arrearsPercentage < 15) return 'text-orange-600 bg-orange-50 border-orange-200';
        return 'text-red-600 bg-red-50 border-red-200';
    };
    /**
     * @generated FunctionHeader
     * Function: getStatusLabel
     * Path: frontend/src/components/widgets/ArrearsAnalysisWidget.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusLabel = () => {
        if (arrearsPercentage < 5) return 'Excellent';
        if (arrearsPercentage < 15) return 'Needs Attention';
        return 'Critical';
    };

    // Aging breakdown data
    const agingData = [
        {
            label: 'Current',
            amount: data.by_aging?.current || 0,
            color: 'bg-green-500',
            percentage: data.total_arrears > 0 ? ( ( data.by_aging?.current || 0 ) / data.total_arrears * 100 ).toFixed(0) : 0
        },
        {
            label: '30 Days',
            amount: data.by_aging?.[ '30_days' ] || 0,
            color: 'bg-yellow-500',
            percentage: data.total_arrears > 0 ? ( ( data.by_aging?.[ '30_days' ] || 0 ) / data.total_arrears * 100 ).toFixed(0) : 0
        },
        {
            label: '60 Days',
            amount: data.by_aging?.[ '60_days' ] || 0,
            color: 'bg-orange-500',
            percentage: data.total_arrears > 0 ? ( ( data.by_aging?.[ '60_days' ] || 0 ) / data.total_arrears * 100 ).toFixed(0) : 0
        },
        {
            label: '90+ Days',
            amount: data.by_aging?.[ '90_plus_days' ] || 0,
            color: 'bg-red-500',
            percentage: data.total_arrears > 0 ? ( ( data.by_aging?.[ '90_plus_days' ] || 0 ) / data.total_arrears * 100 ).toFixed(0) : 0
        }
    ];

    return (
        <Card className="card-dashboard">
            <CardHeader>
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <AlertTriangle className="h-5 w-5 text-orange-600"/>
                        <div>
                            <CardTitle>Arrears Analysis</CardTitle>
                            <CardDescription>Outstanding levies breakdown</CardDescription>
                        </div>
                    </div>
                    {onExport && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={onExport}
                            className="gap-2"
                        >
                            <Download className="h-4 w-4"/>
                            Export
                        </Button>
                    )}
                </div>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* Overall Status */}
                <div className={`p-4 rounded-lg border-2 ${getStatusColor()}`}>
                    <div className="flex items-center justify-between mb-2">
                        <div>
                            <p className="text-sm font-medium">Overall Arrears Status</p>
                            <p className="text-2xl font-bold mt-1">{arrearsPercentage}%</p>
                            <p className="text-xs mt-1">
                                {data.units_in_arrears} of {data.total_units} units
                            </p>
                        </div>
                        <Badge className={getStatusColor()}>
                            {getStatusLabel()}
                        </Badge>
                    </div>
                    <div className="mt-3 pt-3 border-t border-current/20">
                        <div className="flex justify-between items-baseline">
                            <span className="text-sm">Total Outstanding:</span>
                            <span className="text-xl font-bold">
                {formatCurrency(data.total_arrears)}
              </span>
                        </div>
                    </div>
                </div>

                {/* Aging Breakdown */}
                <div>
                    <h4 className="font-semibold mb-3 text-sm">Aging Breakdown</h4>
                    <div className="space-y-3">
                        {agingData.map((item, index) => (
                            <div key={index} className="space-y-1">
                                <div className="flex items-center justify-between text-sm">
                                    <span className="text-muted-foreground">{item.label}</span>
                                    <div className="flex items-center gap-2">
                                        <span className="font-semibold">{formatCurrency(item.amount)}</span>
                                        <span className="text-xs text-muted-foreground">({item.percentage}%)</span>
                                    </div>
                                </div>
                                <div className="h-2 bg-muted rounded-full overflow-hidden">
                                    <div
                                        className={`h-full ${item.color} transition-all duration-500`}
                                        style={{width: `${item.percentage}%`}}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Top Arrears List */}
                {showTopArrears && data.top_arrears && data.top_arrears.length > 0 && (
                    <div>
                        <h4 className="font-semibold mb-3 text-sm flex items-center gap-2">
                            Top Arrears
                            <TrendingUp className="h-4 w-4 text-red-600"/>
                        </h4>
                        <div className="space-y-2">
                            {data.top_arrears.slice(0, 5).map((arrear, index) => (
                                <div
                                    key={index}
                                    className="flex items-center justify-between p-3 rounded-lg border hover:bg-accent/50 transition-colors"
                                >
                                    <div>
                                        <p className="font-semibold text-sm">
                                            Unit {arrear.unit_number || arrear.unit}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {arrear.owner_name || arrear.owner}
                                        </p>
                                    </div>
                                    <div className="text-right">
                                        <p className="font-bold text-red-600">
                                            {formatCurrency(arrear.amount)}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {arrear.months || arrear.days_overdue} {arrear.months ? 'month(s)' : 'days'}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Action Buttons */}
                <div className="flex gap-2">
                    {onViewDetails && (
                        <Button
                            variant="default"
                            className="flex-1 gap-2"
                            onClick={onViewDetails}
                        >
                            View All Details
                            <ArrowRight className="h-4 w-4"/>
                        </Button>
                    )}
                    <Button
                        variant="outline"
                        className="flex-1"
                        onClick={() => {
                            // Send reminders functionality
                            toast.info('Reminder functionality will be available soon', {
                                description: 'This feature is currently under development'
                            });
                        }}
                    >
                        Send Reminders
                    </Button>
                </div>

                {/* Quick Stats */}
                <div className="grid grid-cols-2 gap-4 p-4 bg-muted rounded-lg">
                    <div>
                        <p className="text-xs text-muted-foreground mb-1">Average Per Unit</p>
                        <p className="text-lg font-bold">
                            {data.units_in_arrears > 0
                                ? formatCurrency(data.total_arrears / data.units_in_arrears)
                                : '$0.00'
                            }
                        </p>
                    </div>
                    <div>
                        <p className="text-xs text-muted-foreground mb-1">Recovery Target</p>
                        <p className="text-lg font-bold text-green-600">
                            {formatCurrency(data.total_arrears * 0.8)} {/* 80% recovery target */}
                        </p>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};

export default ArrearsAnalysisWidget;
