"use client";
import React from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Download, Minus, TrendingDown, TrendingUp } from 'lucide-react';
import {
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis
} from 'recharts';
import { formatCurrency } from '../../lib/utils';
import { collectionPercentage } from '../../lib/finance/financeTransforms';
/**
 * LevyCollectionChart - Interactive chart showing levy collection trends
 * Displays collected vs levied amounts over time with trend indicators
 *
 * @param {Array} data - Array of {month, collected, levied} objects
 * @param {String} chartType - 'line' or 'bar'
 * @param {String} title - Chart title
 * @param {Function} onExport - Optional export handler
 */
const LevyCollectionChart = ({
                                 data = [],
                                 chartType = 'bar',
                                 title = 'Levy Collection Trend',
                                 description = 'Monthly collection performance',
                                 onExport = null,
                                 showExport = false
                             }) => {
    // Calculate collection rate and trend
    /**
     * @generated FunctionHeader
     * Function: calculateTrend
     * Path: frontend/src/components/widgets/LevyCollectionChart.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const calculateTrend = () => {
        if (!data || data.length < 2) return {rate: 0, direction: 'neutral'};

        const recent = data.slice(-3);
        const totalCollected = recent.reduce((sum, item) => sum + ( item.collected || 0 ), 0);
        const totalLevied = recent.reduce((sum, item) => sum + ( item.levied || 0 ), 0);
        const rate = collectionPercentage(totalCollected, totalLevied).toFixed(1);

        // Determine trend direction
        let direction = 'neutral';
        if (data.length >= 2) {
            const lastRate = collectionPercentage(
                data[ data.length - 1 ].collected,
                data[ data.length - 1 ].levied,
            );
            const prevRate = collectionPercentage(
                data[ data.length - 2 ].collected,
                data[ data.length - 2 ].levied,
            );
            if (lastRate > prevRate + 2) direction = 'up';
            else if (lastRate < prevRate - 2) direction = 'down';
        }

        return {rate, direction};
    };

    const trend = calculateTrend();
    /**
     * @generated FunctionHeader
     * Function: getTrendIcon
     * Path: frontend/src/components/widgets/LevyCollectionChart.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getTrendIcon = () => {
        switch (trend.direction) {
            case 'up':
                return <TrendingUp className="h-5 w-5 text-green-600"/>;
            case 'down':
                return <TrendingDown className="h-5 w-5 text-red-600"/>;
            default:
                return <Minus className="h-5 w-5 text-gray-600"/>;
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getTrendColor
     * Path: frontend/src/components/widgets/LevyCollectionChart.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getTrendColor = () => {
        switch (trend.direction) {
            case 'up':
                return 'text-green-600';
            case 'down':
                return 'text-red-600';
            default:
                return 'text-gray-600';
        }
    };
    // Custom tooltip
    /**
     * @generated FunctionHeader
     * Function: CustomTooltip
     * Path: frontend/src/components/widgets/LevyCollectionChart.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const CustomTooltip = ({active, payload, label}) => {
        if (active && payload && payload.length) {
            const collected = payload.find(p => p.dataKey === 'collected')?.value || 0;
            const levied = payload.find(p => p.dataKey === 'levied')?.value || 0;
            const rate = collectionPercentage(collected, levied).toFixed(1);

            return (
                <div className="bg-white p-3 rounded-lg shadow-lg border">
                    <p className="font-semibold mb-2">{label}</p>
                    <div className="space-y-1 text-sm">
                        <div className="flex justify-between gap-4">
                            <span className="text-gray-600">Levied:</span>
                            <span className="font-semibold text-gray-800">{formatCurrency(levied)}</span>
                        </div>
                        <div className="flex justify-between gap-4">
                            <span className="text-green-600">Collected:</span>
                            <span className="font-semibold text-green-700">{formatCurrency(collected)}</span>
                        </div>
                        <div className="flex justify-between gap-4 pt-2 border-t">
                            <span className="text-gray-600">Rate:</span>
                            <span
                                className={`font-semibold ${rate >= 90 ? 'text-green-600' : rate >= 75 ? 'text-orange-600' : 'text-red-600'}`}>
                {rate}%
              </span>
                        </div>
                    </div>
                </div>
            );
        }
        return null;
    };

    return (
        <Card className="card-dashboard">
            <CardHeader>
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            {title}
                        </CardTitle>
                        <CardDescription>{description}</CardDescription>
                    </div>
                    <div className="flex items-center gap-3">
                        {/* Trend Indicator */}
                        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-muted">
                            {getTrendIcon()}
                            <div className="text-right">
                                <p className="text-xs text-muted-foreground">Avg Rate</p>
                                <p className={`text-lg font-bold ${getTrendColor()}`}>
                                    {trend.rate}%
                                </p>
                            </div>
                        </div>

                        {/* Export Button */}
                        {showExport && onExport && (
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
                </div>
            </CardHeader>
            <CardContent>
                <div className="h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                        {chartType === 'line' ? (
                            <LineChart data={data}>
                                <CartesianGrid strokeDasharray="3 3"/>
                                <XAxis
                                    dataKey="month"
                                    tick={{fontSize: 12}}
                                />
                                <YAxis
                                    tick={{fontSize: 12}}
                                    tickFormatter={(value) => `$${( value / 1000 ).toFixed(0)}k`}
                                />
                                <Tooltip content={<CustomTooltip/>}/>
                                <Legend/>
                                <Line
                                    type="monotone"
                                    dataKey="levied"
                                    stroke="#94a3b8"
                                    strokeWidth={2}
                                    name="Levied"
                                    dot={{r: 4}}
                                />
                                <Line
                                    type="monotone"
                                    dataKey="collected"
                                    stroke="#22c55e"
                                    strokeWidth={2}
                                    name="Collected"
                                    dot={{r: 4}}
                                />
                            </LineChart>
                        ) : (
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3"/>
                                <XAxis
                                    dataKey="month"
                                    tick={{fontSize: 12}}
                                />
                                <YAxis
                                    tick={{fontSize: 12}}
                                    tickFormatter={(value) => `$${( value / 1000 ).toFixed(0)}k`}
                                />
                                <Tooltip content={<CustomTooltip/>}/>
                                <Legend/>
                                <Bar dataKey="levied" fill="#94a3b8" name="Levied" radius={[4, 4, 0, 0]}/>
                                <Bar dataKey="collected" fill="#22c55e" name="Collected" radius={[4, 4, 0, 0]}/>
                            </BarChart>
                        )}
                    </ResponsiveContainer>
                </div>

                {/* Summary Stats */}
                <div className="mt-4 grid grid-cols-3 gap-4 p-4 bg-muted rounded-lg">
                    <div className="text-center">
                        <p className="text-xs text-muted-foreground mb-1">Total Levied</p>
                        <p className="text-lg font-bold">
                            {formatCurrency(data.reduce((sum, item) => sum + ( item.levied || 0 ), 0))}
                        </p>
                    </div>
                    <div className="text-center border-x border-border">
                        <p className="text-xs text-muted-foreground mb-1">Total Collected</p>
                        <p className="text-lg font-bold text-green-600">
                            {formatCurrency(data.reduce((sum, item) => sum + ( item.collected || 0 ), 0))}
                        </p>
                    </div>
                    <div className="text-center">
                        <p className="text-xs text-muted-foreground mb-1">Outstanding</p>
                        <p className="text-lg font-bold text-red-600">
                            {formatCurrency(
                                data.reduce((sum, item) => sum + ( item.levied || 0 ), 0) -
                                data.reduce((sum, item) => sum + ( item.collected || 0 ), 0)
                            )}
                        </p>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};

export default LevyCollectionChart;
