"use client";
import React from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import { formatCurrency } from '../../lib/utils';
import { AlertCircle } from 'lucide-react';
/**
 * Fund Balance Donut Chart Component
 * Visualizes Admin and Sinking fund balances using a donut chart
 */
const FundBalanceDonut = ({fundData, title = "Fund Balances", error}) => {
    if (error === 'not_found') {
        return (
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle>{title}</CardTitle>
                    <CardDescription>No data available</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                        <AlertCircle className="h-10 w-10 mb-2 opacity-20"/>
                        <p className="text-sm text-center">No fund data available yet</p>
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (error === 'error') {
        return null; // Hide this component on error
    }

    if (!fundData || ( !fundData.admin_fund && !fundData.sinking_fund )) {
        return null;
    }

    const adminBalance = Math.abs(fundData.admin_fund?.closing_balance || 0);
    const sinkingBalance = Math.abs(fundData.sinking_fund?.closing_balance || 0);

    const data = [
        {name: 'Admin Fund', value: adminBalance, color: '#3b82f6'},
        {name: 'Sinking Fund', value: sinkingBalance, color: '#10b981'}
    ].filter(item => item.value > 0);

    const total = adminBalance + sinkingBalance;

    if (total === 0) {
        return (
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle>{title}</CardTitle>
                    <CardDescription>No balance data available</CardDescription>
                </CardHeader>
            </Card>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: CustomTooltip
     * Path: frontend/src/components/widgets/FundBalanceDonut.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const CustomTooltip = ({active, payload}) => {
        if (active && payload && payload.length) {
            const data = payload[ 0 ].payload;
            return (
                <div className="bg-white border border-gray-200 rounded-lg shadow-lg p-3">
                    <p className="font-semibold">{data.name}</p>
                    <p className="text-sm text-muted-foreground">
                        {formatCurrency(data.value)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                        {( ( data.value / total ) * 100 ).toFixed(1)}% of total
                    </p>
                </div>
            );
        }
        return null;
    };
    /**
     * @generated FunctionHeader
     * Function: CustomLabel
     * Path: frontend/src/components/widgets/FundBalanceDonut.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const CustomLabel = ({cx, cy}) => {
        return (
            <text
                x={cx}
                y={cy}
                textAnchor="middle"
                dominantBaseline="central"
                className="fill-foreground"
            >
                <tspan x={cx} dy="-0.5em" fontSize="14" fontWeight="600">
                    Total
                </tspan>
                <tspan x={cx} dy="1.5em" fontSize="20" fontWeight="bold">
                    {formatCurrency(total)}
                </tspan>
            </text>
        );
    };

    return (
        <Card className="card-dashboard">
            <CardHeader>
                <CardTitle>{title}</CardTitle>
                <CardDescription>Distribution across funds</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                            <Pie
                                data={data}
                                cx="50%"
                                cy="50%"
                                innerRadius={60}
                                outerRadius={100}
                                paddingAngle={2}
                                dataKey="value"
                                label={CustomLabel}
                            >
                                {data.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.color}/>
                                ))}
                            </Pie>
                            <Tooltip content={<CustomTooltip/>}/>
                            <Legend
                                verticalAlign="bottom"
                                height={36}
                                formatter={(value, entry) => (
                                    <span className="text-sm">
                    {value}: <span className="font-semibold">{formatCurrency(entry.payload.value)}</span>
                  </span>
                                )}
                            />
                        </PieChart>
                    </ResponsiveContainer>
                </div>

                {/* Fund Details */}
                <div className="mt-4 space-y-2">
                    <div className="flex items-center justify-between p-2 rounded bg-blue-50">
                        <div className="flex items-center gap-2">
                            <div className="w-3 h-3 rounded-full bg-blue-500"></div>
                            <span className="text-sm font-medium">Admin Fund</span>
                        </div>
                        <span className="text-sm font-semibold">{formatCurrency(adminBalance)}</span>
                    </div>
                    <div className="flex items-center justify-between p-2 rounded bg-green-50">
                        <div className="flex items-center gap-2">
                            <div className="w-3 h-3 rounded-full bg-green-500"></div>
                            <span className="text-sm font-medium">Sinking Fund</span>
                        </div>
                        <span className="text-sm font-semibold">{formatCurrency(sinkingBalance)}</span>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};

export default FundBalanceDonut;
