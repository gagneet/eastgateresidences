"use client";

import {useParams, useRouter} from "next/navigation";
import {useEffect, useState} from "react";
import {Activity, AlertTriangle, ArrowLeft, Building2, CheckCircle} from "lucide-react";
import {useAuth} from "@/contexts/AuthContext";

interface BuildingDetail {
    building_id: string;
    name: string;
    lot_count: number;
    health_score: number | null;
    arrears_rate: number;
    open_work_orders: number;
    last_computed_at: string | null;
}
/**
 * @generated FunctionHeader
 * Function: BuildingDetailPage
 * Path: frontend/src/app/(app)/admin/buildings/[id]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingDetailPage() {
    const params = useParams();
    const router = useRouter();
    const {api} = useAuth() as { api: { get: (url: string) => Promise<{ data: { buildings: BuildingDetail[] } }> } };
    const buildingId = params?.id as string;

    const [building, setBuilding] = useState<BuildingDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!buildingId || !api) return;
        api
            .get("/portfolio/buildings")
            .then((res) => {
                const buildings: BuildingDetail[] = res.data?.buildings || [];
                const found = buildings.find((b) => b.building_id === buildingId);
                if (found) {
                    setBuilding(found);
                } else {
                    setError(`Building "${buildingId}" not found.`);
                }
            })
            .catch((err: { response?: { data?: { detail?: string } } }) => {
                setError(err?.response?.data?.detail || "Failed to load building data.");
            })
            .finally(() => setLoading(false));
    }, [buildingId, api]);

    const healthColor =
        building?.health_score == null
            ? "text-gray-500"
            : building.health_score >= 85
                ? "text-green-600"
                : building.health_score >= 70
                    ? "text-yellow-600"
                    : "text-red-600";

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <button
                onClick={() => router.push("/admin")}
                className="flex items-center gap-2 text-sm text-gray-600 hover:text-gray-900 mb-6"
            >
                <ArrowLeft className="w-4 h-4"/>
                Back to Portfolio
            </button>

            {loading && <p className="text-gray-500">Loading building details…</p>}
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">
                    {error}
                </div>
            )}
            {building && (
                <div className="space-y-6">
                    <div className="flex items-center gap-3">
                        <Building2 className="w-8 h-8 text-blue-600"/>
                        <div>
                            <h1 className="text-2xl font-bold text-gray-900">{building.name}</h1>
                            <p className="text-sm text-gray-500">Building ID: {building.building_id}</p>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="bg-white rounded-xl border p-4 shadow-sm">
                            <p className="text-xs text-gray-500 uppercase tracking-wide">Health Score</p>
                            <p className={`text-3xl font-bold mt-1 ${healthColor}`}>
                                {building.health_score ?? "—"}
                            </p>
                        </div>
                        <div className="bg-white rounded-xl border p-4 shadow-sm">
                            <p className="text-xs text-gray-500 uppercase tracking-wide">Lots</p>
                            <p className="text-3xl font-bold mt-1 text-gray-900">{building.lot_count}</p>
                        </div>
                        <div className="bg-white rounded-xl border p-4 shadow-sm">
                            <p className="text-xs text-gray-500 uppercase tracking-wide">Arrears Rate</p>
                            <p
                                className={`text-3xl font-bold mt-1 ${
                                    building.arrears_rate > 5 ? "text-red-600" : "text-gray-900"
                                }`}
                            >
                                {building.arrears_rate?.toFixed(1)}%
                            </p>
                        </div>
                        <div className="bg-white rounded-xl border p-4 shadow-sm">
                            <p className="text-xs text-gray-500 uppercase tracking-wide">Open Work Orders</p>
                            <p
                                className={`text-3xl font-bold mt-1 ${
                                    building.open_work_orders > 0 ? "text-amber-600" : "text-gray-900"
                                }`}
                            >
                                {building.open_work_orders}
                            </p>
                        </div>
                    </div>

                    <div className="bg-white rounded-xl border p-6 shadow-sm">
                        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                            <Activity className="w-5 h-5 text-blue-600"/>
                            Building Status
                        </h2>
                        <div className="flex items-center gap-2 text-sm">
                            {building.health_score != null && building.health_score >= 70 ? (
                                <CheckCircle className="w-4 h-4 text-green-600"/>
                            ) : (
                                <AlertTriangle className="w-4 h-4 text-amber-500"/>
                            )}
                            <span className="text-gray-700">
                {building.health_score != null && building.health_score >= 70
                    ? "Building is in good health."
                    : "Building requires attention."}
              </span>
                        </div>
                        {building.last_computed_at && (
                            <p className="text-xs text-gray-400 mt-3">
                                Last updated: {new Date(building.last_computed_at).toLocaleString()}
                            </p>
                        )}
                    </div>

                    <div className="flex gap-3">
                        <button
                            onClick={() => router.push("/financials/overview")}
                            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
                        >
                            View Financials
                        </button>
                        <button
                            onClick={() => router.push("/maintenance")}
                            className="px-4 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
                        >
                            View Maintenance
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
