// @featuretrace:asset-intelligence — Asset Intelligence: maintenance-risk scoring per building
//   asset, cross-referenced with the Digital Twin asset register.
// Layer: frontend
// Data flow: this page → GET /intelligence/maintenance-risks, GET /digital-twin/assets →
//            backend/routers/intelligence.py (building-scoped).
// Related: frontend/src/app/(app)/intelligence/building/page.tsx
//           frontend/src/app/(app)/settings/digital-twin/page.tsx
//           backend/routers/intelligence.py
"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../contexts/AuthContext";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Button } from "../../components/ui/button";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Progress } from "../../components/ui/progress";
import { Activity, AlertTriangle, ArrowLeft, Info, Shield, Wrench } from "lucide-react";
import { MetricHelp as HelpTip } from "../../components/shared/MetricHelp";
import {formatMoneyFromDollars} from '@/lib/currency';
import {PageHeader} from "../../components/shared/PageHeader";
/**
 * @generated FunctionHeader
 * Function: riskBadge
 * Path: frontend/src/pages/dashboard/IntelligenceAssetsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const riskBadge = (score) => {
    if (score >= 75) return "bg-rose-100 text-rose-700 border-rose-200";
    if (score >= 50) return "bg-amber-100 text-amber-700 border-amber-200";
    if (score >= 25) return "bg-blue-100 text-blue-700 border-blue-200";
    return "bg-emerald-100 text-emerald-700 border-emerald-200";
};
/**
 * @generated FunctionHeader
 * Function: healthColor
 * Path: frontend/src/pages/dashboard/IntelligenceAssetsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const healthColor = (score) => {
    if (score >= 75) return "text-emerald-600";
    if (score >= 50) return "text-blue-600";
    if (score >= 25) return "text-amber-600";
    return "text-rose-600";
};
/**
 * @generated FunctionHeader
 * Function: fmtScore
 * Path: frontend/src/pages/dashboard/IntelligenceAssetsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtScore = (n) => Number(n ?? 0).toFixed(2);
/**
 * @generated FunctionHeader
 * Function: IntelligenceAssetsPage
 * Path: frontend/src/pages/dashboard/IntelligenceAssetsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const IntelligenceAssetsPage = () => {
    const router = useRouter();
    const {api} = useAuth();
    const [risks, setRisks] = useState([]);
    const [assets, setAssets] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedAsset, setSelectedAsset] = useState(null);
    const [selectedRisk, setSelectedRisk] = useState(null);

    useEffect(() => {
        let mounted = true;
        /**
         * @generated FunctionHeader
         * Function: load
         * Path: frontend/src/pages/dashboard/IntelligenceAssetsPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const load = async () => {
            try {
                const [riskResult, assetsResult] = await Promise.allSettled([
                    api.get("/intelligence/maintenance-risks"),
                    api.get("/digital-twin/assets"),
                ]);

                if (!mounted) return;

                if (riskResult.status === "fulfilled") {
                    setRisks(riskResult.value.data || []);
                } else {
                    console.error("Failed to load maintenance risks", riskResult.reason);
                    setRisks([]);
                }

                if (assetsResult.status === "fulfilled") {
                    setAssets(assetsResult.value.data || []);
                } else {
                    console.error("Failed to load assets", assetsResult.reason);
                    setAssets([]);
                }
            } catch (err) {
                console.error("Unexpected failure loading asset intelligence", err);
            } finally {
                if (mounted) setLoading(false);
            }
        };
        load();
        return () => {
            mounted = false;
        };
    }, [api]);

    if (loading) return (
        <div>
            <div className="animate-pulse space-y-4">
                {[...Array(3)].map((_, i) => (
                    <div key={i} className="h-28 bg-muted rounded-lg"/>
                ))}
            </div>
        </div>
    );

    // Merge risk data into assets for enriched view
    const riskMap = Object.fromEntries(risks.map(r => [r.asset_id, r]));

    return (
        <div className="space-y-6">
            {/* Back navigation */}
            <Button
                variant="ghost"
                size="sm"
                className="w-fit -ml-2 text-muted-foreground"
                onClick={() => router.push("/intelligence/building")}
            >
                <ArrowLeft className="h-4 w-4 mr-1"/>
                Back to Intelligence Hub
            </Button>
            <PageHeader
                title="Asset Intelligence"
                icon={<AlertTriangle className="h-5 w-5"/>}
                description="Risk, health, and recurring repair signals by asset. Click any row for details."
            />

            {/* Info alert */}
            <Alert>
                <Info className="h-4 w-4"/>
                <AlertDescription className="text-xs leading-relaxed">
                    <strong>Data source:</strong> Asset health scores are computed by the Digital Twin engine using
                    install year,
                    expected life, last service date, and maintenance history. Risk scores incorporate repair frequency
                    anomalies
                    from the last 12 months. Assets flagged here have repair costs approaching replacement threshold or
                    abnormally high failure rates.
                </AlertDescription>
            </Alert>

            {/* Attention Needed Table */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4 text-rose-500"/>
                        Attention Needed
                    </CardTitle>
                    <CardDescription>Repair-to-replacement anomalies and recurring failures. Click a row for
                        details.</CardDescription>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Asset</TableHead>
                                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    Repairs (12m)
                    <HelpTip
                        text="Number of maintenance jobs raised for this asset in the last 12 months. High frequency suggests premature failure or an underlying fault."/>
                  </span>
                                </TableHead>
                                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    Repair Cost
                    <HelpTip
                        text="Total cost of repairs for this asset in the last 12 months. When repair cost approaches replacement cost, replacement is more economical."/>
                  </span>
                                </TableHead>
                                <TableHead>Recommendation</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {risks.length ? risks.map((risk) => (
                                <TableRow
                                    key={risk.asset_id}
                                    className="cursor-pointer hover:bg-rose-50 transition-colors"
                                    onClick={() => setSelectedRisk(risk)}
                                >
                                    <TableCell className="font-medium">{risk.asset_name}</TableCell>
                                    <TableCell className="text-right">{risk.repairs_last_12m}</TableCell>
                                    <TableCell className="text-right">{formatMoneyFromDollars(risk.repair_cost_last_12m)}</TableCell>
                                    <TableCell className="text-rose-600 text-sm">{risk.recommendation}</TableCell>
                                </TableRow>
                            )) : (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground py-6">
                                        No maintenance anomalies detected.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Asset Health Snapshot Table */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Activity className="h-4 w-4 text-blue-500"/>
                        Asset Health Snapshot
                    </CardTitle>
                    <CardDescription>Current risk and health scores from the intelligence engine. Click a row for
                        details.</CardDescription>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Asset</TableHead>
                                <TableHead>Zone</TableHead>
                                <TableHead>Category</TableHead>
                                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    Health
                    <HelpTip
                        text="Health Score (0–100): 100 = new/perfect condition. Calculated from age vs expected life, service recency, and repair frequency. Below 50 warrants inspection."/>
                  </span>
                                </TableHead>
                                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    Risk
                    <HelpTip
                        text="Risk Score (0–100): measures likelihood of failure or unexpected replacement. Above 75 = high priority. Combines age, repair anomalies, and health score."/>
                  </span>
                                </TableHead>
                                <TableHead>Status</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {assets.length ? assets.map((asset) => {
                                const risk = riskMap[ asset.id ];
                                return (
                                    <TableRow
                                        key={asset.id}
                                        className="cursor-pointer hover:bg-muted transition-colors"
                                        onClick={() => setSelectedAsset({...asset, riskData: risk})}
                                    >
                                        <TableCell className="font-medium">{asset.name}</TableCell>
                                        <TableCell
                                            className="text-muted-foreground text-sm">{asset.zone_id || "—"}</TableCell>
                                        <TableCell>{asset.category}</TableCell>
                                        <TableCell
                                            className={`text-right font-bold ${healthColor(asset.health_score ?? 0)}`}>
                                            {fmtScore(asset.health_score)}
                                        </TableCell>
                                        <TableCell className="text-right">
                      <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-bold ${riskBadge(asset.risk_score ?? 0)}`}>
                        {fmtScore(asset.risk_score)}
                      </span>
                                        </TableCell>
                                        <TableCell>
                                            {risk ? (
                                                <span
                                                    className="text-xs px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 font-medium">Alert</span>
                                            ) : (
                                                <span
                                                    className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">OK</span>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                );
                            }) : (
                                <TableRow>
                                    <TableCell colSpan={6} className="text-center text-muted-foreground py-6">
                                        No assets found. Add assets via Settings → Digital Twin.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Asset Detail Dialog */}
            <Dialog open={!!selectedAsset} onOpenChange={(open) => !open && setSelectedAsset(null)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Shield className="h-5 w-5 text-blue-500"/>
                            {selectedAsset?.name}
                        </DialogTitle>
                        <DialogDescription>{selectedAsset?.category} · {selectedAsset?.zone_id || "No zone"}</DialogDescription>
                    </DialogHeader>
                    {selectedAsset && (
                        <div className="space-y-4 pt-2">
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1">
                                    <p className="text-xs text-muted-foreground">Health Score</p>
                                    <div
                                        className={`text-2xl font-bold ${healthColor(selectedAsset.health_score ?? 0)}`}>
                                        {fmtScore(selectedAsset.health_score)}/100
                                    </div>
                                    <Progress value={selectedAsset.health_score ?? 0} className="h-2"/>
                                </div>
                                <div className="space-y-1">
                                    <p className="text-xs text-muted-foreground">Risk Score</p>
                                    <div
                                        className="text-2xl font-bold text-rose-600">{fmtScore(selectedAsset.risk_score)}/100
                                    </div>
                                    <Progress value={selectedAsset.risk_score ?? 0}
                                              className="h-2 [&>div]:bg-rose-500"/>
                                </div>
                            </div>

                            {selectedAsset.description && (
                                <div>
                                    <p className="text-xs text-muted-foreground mb-1">Description</p>
                                    <p className="text-sm">{selectedAsset.description}</p>
                                </div>
                            )}

                            <div className="grid grid-cols-2 gap-3 text-sm border rounded-lg p-3 bg-muted">
                                {selectedAsset.install_year && (
                                    <div>
                                        <p className="text-xs text-muted-foreground">Install Year</p>
                                        <p className="font-medium">{selectedAsset.install_year}</p>
                                    </div>
                                )}
                                {selectedAsset.expected_life_years && (
                                    <div>
                                        <p className="text-xs text-muted-foreground">Expected Life</p>
                                        <p className="font-medium">{selectedAsset.expected_life_years} years</p>
                                    </div>
                                )}
                                {selectedAsset.replacement_cost && (
                                    <div>
                                        <p className="text-xs text-muted-foreground">Replacement Cost</p>
                                        <p className="font-medium">{formatMoneyFromDollars(selectedAsset.replacement_cost)}</p>
                                    </div>
                                )}
                                {selectedAsset.last_serviced && (
                                    <div>
                                        <p className="text-xs text-muted-foreground">Last Serviced</p>
                                        <p className="font-medium">{selectedAsset.last_serviced?.split("T")[ 0 ]}</p>
                                    </div>
                                )}
                            </div>

                            {selectedAsset.riskData && (
                                <div className="border rounded-lg p-3 bg-rose-50 space-y-2">
                                    <p className="text-sm font-semibold text-rose-700 flex items-center gap-1">
                                        <AlertTriangle className="h-4 w-4"/> Maintenance Alert
                                    </p>
                                    <div className="grid grid-cols-2 gap-2 text-sm">
                                        <div>
                                            <p className="text-xs text-muted-foreground">Repairs (12m)</p>
                                            <p className="font-medium">{selectedAsset.riskData.repairs_last_12m}</p>
                                        </div>
                                        <div>
                                            <p className="text-xs text-muted-foreground">Repair Cost (12m)</p>
                                            <p className="font-medium">{formatMoneyFromDollars(selectedAsset.riskData.repair_cost_last_12m)}</p>
                                        </div>
                                    </div>
                                    <p className="text-xs text-rose-700 font-medium">{selectedAsset.riskData.recommendation}</p>
                                </div>
                            )}
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            {/* Maintenance Risk Detail Dialog */}
            <Dialog open={!!selectedRisk} onOpenChange={(open) => !open && setSelectedRisk(null)}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Wrench className="h-5 w-5 text-rose-500"/>
                            {selectedRisk?.asset_name}
                        </DialogTitle>
                        <DialogDescription>Maintenance anomaly details</DialogDescription>
                    </DialogHeader>
                    {selectedRisk && (
                        <div className="space-y-4 pt-2">
                            <div className="grid grid-cols-2 gap-3 text-sm border rounded-lg p-3 bg-muted">
                                <div>
                                    <p className="text-xs text-muted-foreground">Repairs (Last 12m)</p>
                                    <p className="text-xl font-bold text-rose-600">{selectedRisk.repairs_last_12m}</p>
                                </div>
                                <div>
                                    <p className="text-xs text-muted-foreground">Repair Cost (12m)</p>
                                    <p className="text-xl font-bold text-amber-600">{formatMoneyFromDollars(selectedRisk.repair_cost_last_12m)}</p>
                                </div>
                                {selectedRisk.replacement_cost_estimate && (
                                    <div>
                                        <p className="text-xs text-muted-foreground">Replacement Estimate</p>
                                        <p className="font-medium">{formatMoneyFromDollars(selectedRisk.replacement_cost_estimate)}</p>
                                    </div>
                                )}
                                {selectedRisk.risk_score !== undefined && (
                                    <div>
                                        <p className="text-xs text-muted-foreground">Risk Score</p>
                                        <p className="font-medium">{fmtScore(selectedRisk.risk_score)}</p>
                                    </div>
                                )}
                            </div>
                            <div className="rounded-lg border bg-rose-50 p-3">
                                <p className="text-xs text-muted-foreground mb-1">Recommendation</p>
                                <p className="text-sm font-semibold text-rose-700">{selectedRisk.recommendation}</p>
                            </div>
                            {selectedRisk.anomaly_type && (
                                <div>
                                    <p className="text-xs text-muted-foreground">Anomaly Type</p>
                                    <p className="text-sm">{selectedRisk.anomaly_type}</p>
                                </div>
                            )}
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default IntelligenceAssetsPage;
