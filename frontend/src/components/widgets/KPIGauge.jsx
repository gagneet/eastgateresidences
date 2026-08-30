"use client";
import React from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { AlertCircle, CheckCircle, TrendingDown, TrendingUp } from 'lucide-react';
/**
 * KPI Gauge Component
 * Displays a Key Performance Indicator with status colors (green/amber/red)
 */
const KPIGauge = ({
                      title,
                      value,
                      target,
                      unit = '%',
                      description,
                      icon: Icon,
                      thresholds = {good: 90, warning: 70}, // Default thresholds
                      inverse = false, // If true, lower values are better
                      onClick
                  }) => {
    // Calculate percentage if target is provided
    const percentage = target ? ( value / target ) * 100 : value;
    // Determine status based on thresholds
    /**
     * @generated FunctionHeader
     * Function: getStatus
     * Path: frontend/src/components/widgets/KPIGauge.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatus = () => {
        const effectiveValue = inverse ? ( 100 - percentage ) : percentage;

        if (effectiveValue >= thresholds.good) {
            return {
                label: 'Excellent',
                color: 'text-green-600',
                bgColor: 'bg-green-100',
                borderColor: 'border-green-300',
                icon: CheckCircle,
                iconColor: 'text-green-600'
            };
        } else if (effectiveValue >= thresholds.warning) {
            return {
                label: 'Good',
                color: 'text-yellow-600',
                bgColor: 'bg-yellow-100',
                borderColor: 'border-yellow-300',
                icon: TrendingUp,
                iconColor: 'text-yellow-600'
            };
        } else {
            return {
                label: 'Needs Attention',
                color: 'text-red-600',
                bgColor: 'bg-red-100',
                borderColor: 'border-red-300',
                icon: AlertCircle,
                iconColor: 'text-red-600'
            };
        }
    };

    const status = getStatus();
    const StatusIcon = status.icon;

    // Calculate gauge fill percentage (0-100)
    const fillPercentage = Math.min(Math.max(percentage, 0), 100);

    return (
        <Card
            className={`card-dashboard ${onClick ? 'cursor-pointer hover:shadow-lg transition-all group' : ''}`}
            onClick={onClick}
        >
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                        {Icon && <Icon className="h-5 w-5 text-muted-foreground"/>}
                        <CardTitle className="text-base">{title}</CardTitle>
                    </div>
                    <Badge variant="outline" className={`${status.bgColor} ${status.color} ${status.borderColor}`}>
                        <StatusIcon className={`h-3 w-3 mr-1 ${status.iconColor}`}/>
                        {status.label}
                    </Badge>
                </div>
                {description && (
                    <CardDescription className="text-xs mt-1">{description}</CardDescription>
                )}
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Main Value Display */}
                <div className="text-center">
                    <p className={`text-4xl font-bold ${status.color}`}>
                        {typeof value === 'number' ? value.toFixed(1) : value}
                        <span className="text-lg ml-1">{unit}</span>
                    </p>
                    {target && (
                        <p className="text-sm text-muted-foreground mt-1">
                            Target: {target}{unit}
                        </p>
                    )}
                </div>

                {/* Gauge Bar */}
                <div className="space-y-2">
                    <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden">
                        <div
                            className={`h-full transition-all duration-500 ${
                                fillPercentage >= thresholds.good ? 'bg-green-500' :
                                    fillPercentage >= thresholds.warning ? 'bg-yellow-500' :
                                        'bg-red-500'
                            }`}
                            style={{width: `${fillPercentage}%`}}
                        />
                    </div>

                    {/* Threshold Markers */}
                    <div className="flex justify-between text-xs text-muted-foreground">
                        <span>0</span>
                        <span className="text-yellow-600">{thresholds.warning}</span>
                        <span className="text-green-600">{thresholds.good}</span>
                        <span>100</span>
                    </div>
                </div>

                {/* Trend Indicator (if available) */}
                {percentage !== undefined && (
                    <div className="flex items-center justify-center gap-2 text-sm">
                        {percentage >= thresholds.good ? (
                            <TrendingUp className="h-4 w-4 text-green-600"/>
                        ) : (
                            <TrendingDown className="h-4 w-4 text-red-600"/>
                        )}
                        <span className={percentage >= thresholds.good ? 'text-green-600' : 'text-red-600'}>
              {fillPercentage.toFixed(1)}% of target
            </span>
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default KPIGauge;
