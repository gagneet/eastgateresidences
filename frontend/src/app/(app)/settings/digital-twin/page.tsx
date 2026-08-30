// @ts-nocheck
"use client";

import {useEffect, useState} from "react";
import {Activity, Building2, Edit2, Map, Plus, Trash2, Users2, Wrench} from "lucide-react";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Tooltip, TooltipContent, TooltipTrigger} from "@/components/ui/tooltip";
import {Input} from "@/components/ui/input";
import {Badge} from "@/components/ui/badge";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "@/components/ui/select";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from "@/components/ui/dialog";
import {useAuth} from "@/contexts/AuthContext";
import {toast} from "sonner";
import {BenefitGroup, BuildingAsset, Facility, Zone} from "@/types/intelligence";
import {formatMoneyFromDollars} from '@/lib/currency';

type ObjectType = "asset" | "facility" | "zone" | "benefit_group";
type DialogMode = "add" | "edit";

const OBJECT_TYPES: { value: ObjectType; label: string }[] = [
    {value: "asset", label: "Building Asset"},
    {value: "facility", label: "Facility"},
    {value: "zone", label: "Zone"},
    {value: "benefit_group", label: "Benefit Group"},
];

const ALLOCATION_TYPES = [
    {value: "unit_entitlement", label: "Unit Entitlement"},
    {value: "equal_split", label: "Equal Split"},
    {value: "usage_based", label: "Usage Based"},
    {value: "fixed_percentage", label: "Fixed Percentage"},
];

const EDITABLE_ROLES = ["super_admin", "strata_manager", "ec_member"];
/**
 * @generated FunctionHeader
 * Function: healthColor
 * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function healthColor(score: number) {
    if (score >= 80) return "text-emerald-600 bg-emerald-50";
    if (score >= 50) return "text-amber-600 bg-amber-50";
    return "text-rose-600 bg-rose-50";
}
/**
 * @generated FunctionHeader
 * Function: riskColor
 * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function riskColor(score: number) {
    if (score <= 20) return "text-emerald-600 bg-emerald-50";
    if (score <= 50) return "text-amber-600 bg-amber-50";
    return "text-rose-600 bg-rose-50";
}
/** Round to at most 4 decimal places, stripping trailing zeros */
function fmtScore(n: number | undefined | null, fallback = 0): number {
    return parseFloat((n ?? fallback).toFixed(4));
}
/**
 * @generated FunctionHeader
 * Function: DigitalTwinSettingsPage
 * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function DigitalTwinSettingsPage() {
    const {token, user, api}: any = useAuth();
    const [activeTab, setActiveTab] = useState("assets");
    const [zones, setZones] = useState<Zone[]>([]);
    const [facilities, setFacilities] = useState<Facility[]>([]);
    const [assets, setAssets] = useState<BuildingAsset[]>([]);
    const [benefitGroups, setBenefitGroups] = useState<BenefitGroup[]>([]);
    const [loading, setLoading] = useState(true);

    // Dialog state
    const [dialogOpen, setDialogOpen] = useState(false);
    const [dialogMode, setDialogMode] = useState<DialogMode>("add");
    const [editItemId, setEditItemId] = useState<string>("");
    const [objectType, setObjectType] = useState<ObjectType>("asset");
    const [saving, setSaving] = useState(false);
    const [formData, setFormData] = useState<Record<string, string>>({});
    // Read-only computed fields shown in edit mode
    const [editReadOnly, setEditReadOnly] = useState<Record<string, any>>({});

    const effectiveRole =
        user?.temp_elevation?.role ?? (user?.is_elevated ? "ec_member" : user?.role);
    const canEdit = !!effectiveRole && EDITABLE_ROLES.includes(effectiveRole);

    useEffect(() => {
        if (token) fetchData();
    }, [token]);
    /**
     * @generated FunctionHeader
     * Function: fetchData
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchData = async () => {
        setLoading(true);
        try {
            const [zonesRes, facilitiesRes, assetsRes, bgRes] = await Promise.all([
                api.get("/digital-twin/zones"),
                api.get("/digital-twin/facilities"),
                api.get("/digital-twin/assets"),
                api.get("/digital-twin/benefit-groups"),
            ]);
            setZones(zonesRes.data);
            setFacilities(facilitiesRes.data);
            setAssets(assetsRes.data);
            setBenefitGroups(bgRes.data);
        } catch (err) {
            toast.error("Failed to load digital twin configuration.");
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openAddDialog
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openAddDialog = () => {
        const tabTypeMap: Record<string, ObjectType> = {
            assets: "asset", facilities: "facility", zones: "zone", groups: "benefit_group",
        };
        setDialogMode("add");
        setEditItemId("");
        setObjectType(tabTypeMap[activeTab] || "asset");
        setFormData({});
        setEditReadOnly({});
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openEditDialog
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditDialog = (type: ObjectType, item: any) => {
        setDialogMode("edit");
        setEditItemId(item.id);
        setObjectType(type);

        // Populate editable fields
        const editable: Record<string, string> = {name: item.name || ""};
        if (type === "zone") {
            editable.description = item.description || "";
        } else if (type === "benefit_group") {
            editable.description = item.description || "";
            editable.allocation_type = item.allocation_rule?.allocation_type || "unit_entitlement";
        } else if (type === "facility") {
            editable.category = item.category || "";
            editable.zone_id = item.zone_id || "";
            editable.benefit_group_id = item.benefit_group_id || "";
            editable.notes = item.notes || "";
        } else if (type === "asset") {
            editable.category = item.category || "";
            editable.facility_id = item.facility_id || "";
            editable.installation_date = item.installation_date || "";
            editable.expected_lifespan_years = String(item.expected_lifespan_years ?? 20);
            editable.replacement_cost_estimate = String(item.replacement_cost_estimate ?? 0);
            editable.maintenance_frequency_months = String(item.maintenance_frequency_months ?? 12);
            editable.notes = item.notes || "";
        }

        // Capture read-only computed fields
        const ro: Record<string, any> = {};
        if (type === "asset" || type === "facility") {
            ro.health_score = item.health_score ?? 100;
        }
        if (type === "asset") {
            ro.risk_score = item.risk_score ?? 0;
            ro.last_service_date = item.last_service_date || null;
        }
        if (type === "benefit_group") {
            ro.lot_count = (item.lot_numbers || []).length;
        }

        setFormData(editable);
        setEditReadOnly(ro);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: buildPayload
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const buildPayload = (): Record<string, any> => {
        if (objectType === "asset") {
            const p: Record<string, any> = {
                name: formData.name,
                category: formData.category || "general",
                facility_id: formData.facility_id || undefined,
                expected_lifespan_years: parseInt(formData.expected_lifespan_years || "20"),
                replacement_cost_estimate: parseFloat(formData.replacement_cost_estimate || "0"),
                maintenance_frequency_months: parseInt(formData.maintenance_frequency_months || "12"),
                installation_date: formData.installation_date || undefined,
                notes: formData.notes || undefined,
            };
            if (dialogMode === "add") {
                p.risk_score = 0;
                p.health_score = 100;
            }
            return p;
        }
        if (objectType === "facility") {
            return {
                name: formData.name,
                category: formData.category || "general",
                zone_id: formData.zone_id || undefined,
                benefit_group_id: formData.benefit_group_id || undefined,
                notes: formData.notes || undefined,
                ...(dialogMode === "add" ? {health_score: 100} : {}),
            };
        }
        if (objectType === "zone") {
            return {
                name: formData.name,
                description: formData.description || undefined,
            };
        }
        // benefit_group
        return {
            name: formData.name,
            description: formData.description || undefined,
            ...(dialogMode === "add" ? {lot_numbers: []} : {}),
            allocation_rule: {
                allocation_type: formData.allocation_type || "unit_entitlement",
            },
        };
    };
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        if (!formData.name?.trim()) {
            toast.error("Name is required.");
            return;
        }
        setSaving(true);
        const endpointMap: Record<ObjectType, string> = {
            asset: "/digital-twin/assets",
            facility: "/digital-twin/facilities",
            zone: "/digital-twin/zones",
            benefit_group: "/digital-twin/benefit-groups",
        };
        const typeLabel = OBJECT_TYPES.find(t => t.value === objectType)?.label;
        try {
            const payload = buildPayload();
            if (dialogMode === "add") {
                await api.post(endpointMap[objectType], payload);
                toast.success(`${typeLabel} added successfully`);
            } else {
                await api.put(`${endpointMap[objectType]}/${editItemId}`, payload);
                toast.success(`${typeLabel} updated successfully`);
            }
            setDialogOpen(false);
            fetchData();
        } catch (err: any) {
            const detail = err?.response?.data?.detail;
            toast.error(typeof detail === "string" ? detail : "Failed to save. Check required fields.");
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (e: any, type: ObjectType, id: string, name: string) => {
        e.stopPropagation();
        if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
        const endpointMap: Record<ObjectType, string> = {
            asset: `/digital-twin/assets/${id}`,
            facility: `/digital-twin/facilities/${id}`,
            zone: `/digital-twin/zones/${id}`,
            benefit_group: `/digital-twin/benefit-groups/${id}`,
        };
        try {
            await api.delete(endpointMap[type]);
            toast.success(`"${name}" deleted`);
            fetchData();
        } catch {
            toast.error("Delete failed. The item may have dependent records.");
        }
    };
    /**
     * @generated FunctionHeader
     * Function: field
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const field = (key: string) => formData[key] || "";
    /**
     * @generated FunctionHeader
     * Function: setField
     * Path: frontend/src/app/(app)/settings/digital-twin/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const setField = (key: string, value: string) => setFormData(f => ({...f, [key]: value}));

    const rowClass = canEdit
        ? "cursor-pointer hover:bg-slate-50 transition-colors"
        : "";

    if (loading) return <div className="p-8 text-center text-slate-500">Loading Digital Twin Configuration...</div>;

    return (
        <div className="space-y-8">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Digital Twin Architecture</h1>
                    <p className="text-muted-foreground">Model your building's physical structure, service zones, and
                        assets.</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={fetchData}>Refresh</Button>
                    {canEdit && (
                        <Button className="flex items-center gap-2" onClick={openAddDialog}>
                            <Plus className="h-4 w-4"/>
                            Add Object
                        </Button>
                    )}
                </div>
            </div>

            {canEdit && (
                <p className="text-xs text-slate-400">Click any row to view and edit its details.</p>
            )}

            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                <TabsList>
                    <TabsTrigger value="assets" className="flex items-center gap-2">
                        <Wrench className="h-4 w-4"/> Assets ({assets.length})
                    </TabsTrigger>
                    <TabsTrigger value="facilities" className="flex items-center gap-2">
                        <Building2 className="h-4 w-4"/> Facilities ({facilities.length})
                    </TabsTrigger>
                    <TabsTrigger value="zones" className="flex items-center gap-2">
                        <Map className="h-4 w-4"/> Zones ({zones.length})
                    </TabsTrigger>
                    <TabsTrigger value="groups" className="flex items-center gap-2">
                        <Users2 className="h-4 w-4"/> Benefit Groups ({benefitGroups.length})
                    </TabsTrigger>
                </TabsList>

                {/* Assets */}
                <TabsContent value="assets" className="pt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Asset Register</CardTitle>
                            <CardDescription>Individual components and their lifecycle data.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {assets.length === 0 ? (
                                <p className="text-center text-slate-400 py-8">No assets yet. Click "Add Object" to
                                    create one.</p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Asset Name</TableHead>
                                            <TableHead>Category</TableHead>
                                            <TableHead>Lifespan</TableHead>
                                            <TableHead>Replacement Cost</TableHead>
                                            <TableHead>Health</TableHead>
                                            <TableHead>Risk</TableHead>
                                            {canEdit && <TableHead className="text-right">Actions</TableHead>}
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {assets.map((asset) => (
                                            <TableRow
                                                key={asset.id}
                                                className={rowClass}
                                                onClick={() => canEdit && openEditDialog("asset", asset)}
                                            >
                                                <TableCell className="font-medium">{asset.name}</TableCell>
                                                <TableCell
                                                    className="capitalize">{asset.category?.replace(/_/g, " ")}</TableCell>
                                                <TableCell>{asset.expected_lifespan_years}y</TableCell>
                                                <TableCell>{formatMoneyFromDollars(asset.replacement_cost_estimate)}</TableCell>
                                                <TableCell>
                                                    <Badge variant="outline"
                                                           className={`text-xs font-bold ${healthColor(asset.health_score ?? 100)}`}>
                                                        <Activity className="h-3 w-3 mr-1"/>
                                                        {fmtScore(asset.health_score, 100)}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge variant="outline"
                                                           className={`text-xs font-bold ${riskColor(asset.risk_score ?? 0)}`}>
                                                        {fmtScore(asset.risk_score, 0)}
                                                    </Badge>
                                                </TableCell>
                                                {canEdit && (
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            openEditDialog("asset", asset);
                                                                        }}
                                                                        aria-label={`Edit ${asset.name}`}
                                                                    >
                                                                        <Edit2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Edit Asset</TooltipContent>
                                                            </Tooltip>
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="text-rose-500"
                                                                        onClick={(e) =>
                                                                            handleDelete(e, "asset", asset.id, asset.name)
                                                                        }
                                                                        aria-label={`Delete ${asset.name}`}
                                                                    >
                                                                        <Trash2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Delete Asset</TooltipContent>
                                                            </Tooltip>
                                                        </div>
                                                    </TableCell>
                                                )}
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* Facilities */}
                <TabsContent value="facilities" className="pt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Building Facilities</CardTitle>
                            <CardDescription>Functional systems aggregating multiple assets.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {facilities.length === 0 ? (
                                <p className="text-center text-slate-400 py-8">No facilities yet.</p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Facility Name</TableHead>
                                            <TableHead>Category</TableHead>
                                            <TableHead>Zone</TableHead>
                                            <TableHead>Benefit Group</TableHead>
                                            <TableHead>Health</TableHead>
                                            {canEdit && <TableHead className="text-right">Actions</TableHead>}
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {facilities.map((fac) => (
                                            <TableRow
                                                key={fac.id}
                                                className={rowClass}
                                                onClick={() => canEdit && openEditDialog("facility", fac)}
                                            >
                                                <TableCell className="font-medium">{fac.name}</TableCell>
                                                <TableCell
                                                    className="capitalize">{fac.category?.replace(/_/g, " ")}</TableCell>
                                                <TableCell>{zones.find(z => z.id === fac.zone_id)?.name || "—"}</TableCell>
                                                <TableCell>{benefitGroups.find(bg => bg.id === fac.benefit_group_id)?.name || "—"}</TableCell>
                                                <TableCell>
                                                    <Badge variant="outline"
                                                           className={`text-xs font-bold ${healthColor(fac.health_score ?? 100)}`}>
                                                        {fmtScore(fac.health_score, 100)}
                                                    </Badge>
                                                </TableCell>
                                                {canEdit && (
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            openEditDialog("facility", fac);
                                                                        }}
                                                                        aria-label={`Edit ${fac.name}`}
                                                                    >
                                                                        <Edit2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Edit Facility</TooltipContent>
                                                            </Tooltip>
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="text-rose-500"
                                                                        onClick={(e) =>
                                                                            handleDelete(e, "facility", fac.id, fac.name)
                                                                        }
                                                                        aria-label={`Delete ${fac.name}`}
                                                                    >
                                                                        <Trash2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Delete Facility</TooltipContent>
                                                            </Tooltip>
                                                        </div>
                                                    </TableCell>
                                                )}
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* Zones */}
                <TabsContent value="zones" className="pt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Physical Zones</CardTitle>
                            <CardDescription>Areas used for maintenance filtering and cost center
                                mapping.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {zones.length === 0 ? (
                                <p className="text-center text-slate-400 py-8">No zones yet.</p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Zone Name</TableHead>
                                            <TableHead>Description</TableHead>
                                            <TableHead>Facilities</TableHead>
                                            {canEdit && <TableHead className="text-right">Actions</TableHead>}
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {zones.map((zone) => (
                                            <TableRow
                                                key={zone.id}
                                                className={rowClass}
                                                onClick={() => canEdit && openEditDialog("zone", zone)}
                                            >
                                                <TableCell className="font-medium">{zone.name}</TableCell>
                                                <TableCell
                                                    className="text-muted-foreground">{zone.description || "—"}</TableCell>
                                                <TableCell className="text-slate-500 text-sm">
                                                    {facilities.filter(f => f.zone_id === zone.id).length} facilities
                                                </TableCell>
                                                {canEdit && (
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            openEditDialog("zone", zone);
                                                                        }}
                                                                        aria-label={`Edit ${zone.name}`}
                                                                    >
                                                                        <Edit2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Edit Zone</TooltipContent>
                                                            </Tooltip>
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="text-rose-500"
                                                                        onClick={(e) =>
                                                                            handleDelete(e, "zone", zone.id, zone.name)
                                                                        }
                                                                        aria-label={`Delete ${zone.name}`}
                                                                    >
                                                                        <Trash2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Delete Zone</TooltipContent>
                                                            </Tooltip>
                                                        </div>
                                                    </TableCell>
                                                )}
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* Benefit Groups */}
                <TabsContent value="groups" className="pt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Benefit Groups</CardTitle>
                            <CardDescription>Logical groupings for fair cost allocation.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {benefitGroups.length === 0 ? (
                                <p className="text-center text-slate-400 py-8">No benefit groups yet.</p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Group Name</TableHead>
                                            <TableHead>Description</TableHead>
                                            <TableHead>Allocation Method</TableHead>
                                            <TableHead>Lots</TableHead>
                                            {canEdit && <TableHead className="text-right">Actions</TableHead>}
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {benefitGroups.map((bg) => (
                                            <TableRow
                                                key={bg.id}
                                                className={rowClass}
                                                onClick={() => canEdit && openEditDialog("benefit_group", bg)}
                                            >
                                                <TableCell className="font-medium">{bg.name}</TableCell>
                                                <TableCell
                                                    className="text-muted-foreground">{bg.description || "—"}</TableCell>
                                                <TableCell
                                                    className="capitalize">{bg.allocation_rule?.allocation_type?.replace(/_/g, " ")}</TableCell>
                                                <TableCell className="text-slate-500 text-sm">
                                                    {(bg.lot_numbers || []).length === 0 ? "All lots" : `${bg.lot_numbers.length} lots`}
                                                </TableCell>
                                                {canEdit && (
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            openEditDialog("benefit_group", bg);
                                                                        }}
                                                                        aria-label={`Edit ${bg.name}`}
                                                                    >
                                                                        <Edit2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Edit Benefit Group</TooltipContent>
                                                            </Tooltip>
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="text-rose-500"
                                                                        onClick={(e) =>
                                                                            handleDelete(e, "benefit_group", bg.id, bg.name)
                                                                        }
                                                                        aria-label={`Delete ${bg.name}`}
                                                                    >
                                                                        <Trash2 className="h-4 w-4"/>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Delete Benefit Group</TooltipContent>
                                                            </Tooltip>
                                                        </div>
                                                    </TableCell>
                                                )}
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* Add / Edit Dialog */}
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>
                            {dialogMode === "edit"
                                ? `Edit ${OBJECT_TYPES.find(t => t.value === objectType)?.label}`
                                : "Add Digital Twin Object"}
                        </DialogTitle>
                        <DialogDescription>
                            {dialogMode === "edit"
                                ? "Update the details below and click Save."
                                : "Select the type of object and fill in the required details."}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        {/* Read-only computed badges (edit mode only) */}
                        {dialogMode === "edit" && Object.keys(editReadOnly).length > 0 && (
                            <div className="flex flex-wrap gap-2 p-3 bg-slate-50 rounded-lg border border-slate-200">
                                {editReadOnly.health_score !== undefined && (
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-xs text-slate-500 font-medium">Health:</span>
                                        <Badge variant="outline"
                                               className={`text-xs font-bold ${healthColor(editReadOnly.health_score)}`}>
                                            {fmtScore(editReadOnly.health_score, 100)}
                                        </Badge>
                                    </div>
                                )}
                                {editReadOnly.risk_score !== undefined && (
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-xs text-slate-500 font-medium">Risk:</span>
                                        <Badge variant="outline"
                                               className={`text-xs font-bold ${riskColor(editReadOnly.risk_score)}`}>
                                            {fmtScore(editReadOnly.risk_score, 0)}
                                        </Badge>
                                    </div>
                                )}
                                {editReadOnly.last_service_date && (
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-xs text-slate-500 font-medium">Last Service:</span>
                                        <span
                                            className="text-xs font-medium">{editReadOnly.last_service_date.split("T")[0]}</span>
                                    </div>
                                )}
                                {editReadOnly.lot_count !== undefined && (
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-xs text-slate-500 font-medium">Lots:</span>
                                        <span
                                            className="text-xs font-medium">{editReadOnly.lot_count === 0 ? "All lots" : editReadOnly.lot_count}</span>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Object type selector (add mode only) */}
                        {dialogMode === "add" && (
                            <div className="space-y-1.5">
                                <p className="text-xs font-black uppercase text-slate-500">Object Type</p>
                                <Select value={objectType} onValueChange={(v) => {
                                    setObjectType(v as ObjectType);
                                    setFormData({});
                                }}>
                                    <SelectTrigger>
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {OBJECT_TYPES.map(t => (
                                            <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}

                        {/* Common: Name */}
                        <div className="space-y-1.5">
                            <p className="text-xs font-black uppercase text-slate-500">Name *</p>
                            <Input
                                value={field("name")}
                                onChange={e => setField("name", e.target.value)}
                                placeholder={`${OBJECT_TYPES.find(t => t.value === objectType)?.label} name`}
                            />
                        </div>

                        {/* Zone / Benefit Group: description */}
                        {(objectType === "zone" || objectType === "benefit_group") && (
                            <div className="space-y-1.5">
                                <p className="text-xs font-black uppercase text-slate-500">Description</p>
                                <Input value={field("description")}
                                       onChange={e => setField("description", e.target.value)}
                                       placeholder="Optional description"/>
                            </div>
                        )}

                        {/* Benefit Group: allocation type */}
                        {objectType === "benefit_group" && (
                            <div className="space-y-1.5">
                                <p className="text-xs font-black uppercase text-slate-500">Allocation Method</p>
                                <Select value={field("allocation_type") || "unit_entitlement"}
                                        onValueChange={v => setField("allocation_type", v)}>
                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        {ALLOCATION_TYPES.map(a => <SelectItem key={a.value}
                                                                               value={a.value}>{a.label}</SelectItem>)}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}

                        {/* Facility: category + zone + benefit group + notes */}
                        {objectType === "facility" && (
                            <>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Category *</p>
                                    <Input value={field("category")}
                                           onChange={e => setField("category", e.target.value)}
                                           placeholder="e.g. Vertical Transport, Electrical, HVAC"/>
                                </div>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Zone</p>
                                    <Select value={field("zone_id") || "__none__"}
                                            onValueChange={v => setField("zone_id", v === "__none__" ? "" : v)}>
                                        <SelectTrigger><SelectValue
                                            placeholder="Select zone (optional)"/></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="__none__">None</SelectItem>
                                            {zones.map(z => <SelectItem key={z.id} value={z.id}>{z.name}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Benefit Group</p>
                                    <Select value={field("benefit_group_id") || "__none__"}
                                            onValueChange={v => setField("benefit_group_id", v === "__none__" ? "" : v)}>
                                        <SelectTrigger><SelectValue
                                            placeholder="Select benefit group (optional)"/></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="__none__">None</SelectItem>
                                            {benefitGroups.map(bg => <SelectItem key={bg.id}
                                                                                 value={bg.id}>{bg.name}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Notes</p>
                                    <Input value={field("notes")} onChange={e => setField("notes", e.target.value)}
                                           placeholder="Optional notes"/>
                                </div>
                            </>
                        )}

                        {/* Asset: category, facility, dates, cost, notes */}
                        {objectType === "asset" && (
                            <>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Category *</p>
                                    <Input value={field("category")}
                                           onChange={e => setField("category", e.target.value)}
                                           placeholder="e.g. lift, fire_safety, hvac"/>
                                </div>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Facility</p>
                                    <Select value={field("facility_id") || "__none__"}
                                            onValueChange={v => setField("facility_id", v === "__none__" ? "" : v)}>
                                        <SelectTrigger><SelectValue
                                            placeholder="Select facility (optional)"/></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="__none__">None</SelectItem>
                                            {facilities.map(f => <SelectItem key={f.id}
                                                                             value={f.id}>{f.name}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1.5">
                                        <p className="text-xs font-black uppercase text-slate-500">Installation Date</p>
                                        <Input type="date" value={field("installation_date")}
                                               onChange={e => setField("installation_date", e.target.value)}/>
                                    </div>
                                    <div className="space-y-1.5">
                                        <p className="text-xs font-black uppercase text-slate-500">Lifespan (years)
                                            *</p>
                                        <Input type="number" min="1" value={field("expected_lifespan_years") || "20"}
                                               onChange={e => setField("expected_lifespan_years", e.target.value)}/>
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1.5">
                                        <p className="text-xs font-black uppercase text-slate-500">Replacement Cost
                                            ($)</p>
                                        <Input type="number" min="0" value={field("replacement_cost_estimate")}
                                               onChange={e => setField("replacement_cost_estimate", e.target.value)}
                                               placeholder="0"/>
                                    </div>
                                    <div className="space-y-1.5">
                                        <p className="text-xs font-black uppercase text-slate-500">Service Interval
                                            (months)</p>
                                        <Input type="number" min="1"
                                               value={field("maintenance_frequency_months") || "12"}
                                               onChange={e => setField("maintenance_frequency_months", e.target.value)}/>
                                    </div>
                                </div>
                                <div className="space-y-1.5">
                                    <p className="text-xs font-black uppercase text-slate-500">Notes</p>
                                    <Input value={field("notes")} onChange={e => setField("notes", e.target.value)}
                                           placeholder="Optional notes"/>
                                </div>
                            </>
                        )}
                    </div>

                    <div className="flex justify-end gap-3 pt-2">
                        <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
                        <Button onClick={handleSave} disabled={saving || !field("name")}>
                            {saving ? "Saving..." : dialogMode === "edit" ? "Save Changes" : "Add Object"}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
