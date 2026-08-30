// @ts-nocheck
"use client";

/**
 * /register/update?token=<UUID>
 *
 * Token-based registration update page.
 * Allows a pending user to correct their unit number or role after
 * receiving a "Request Info" email from an admin.
 */

import React, {Suspense, useEffect, useState} from "react";
import {useRouter, useSearchParams} from "next/navigation";
import axios from "axios";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Label} from "@/components/ui/label";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from "@/components/ui/select";
import {AlertTriangle, CheckCircle, HelpCircle, Loader2} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

const ROLE_LABELS: Record<string, string> = {
    owner: "Owner",
    tenant: "Tenant",
    guest: "Guest",
};

const REASON_LABELS: Record<string, string> = {
    wrong_unit: "Wrong Unit Entered",
    wrong_user_type: "Wrong User Type Selected",
};
// ─── Inner component (uses useSearchParams) ──────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: UpdateForm
 * Path: frontend/src/app/(public)/register/update/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function UpdateForm() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const token = searchParams.get("token") || "";

    // State
    const [phase, setPhase] = useState<"loading" | "form" | "success" | "error">("loading");
    const [errorMsg, setErrorMsg] = useState("");
    const [userData, setUserData] = useState<any>(null);
    const [units, setUnits] = useState<any[]>([]);
    const [unitNumber, setUnitNumber] = useState("");
    const [role, setRole] = useState("");
    const [submitting, setSubmitting] = useState(false);

    // ── Validate token and pre-fill form ──────────────────────────────────────
    useEffect(() => {
        if (!token) {
            setErrorMsg("No update token found. Please check the link in your email.");
            setPhase("error");
            return;
        }

        (async () => {
            try {
                const [checkRes, unitsRes] = await Promise.all([
                    axios.get(`${API_BASE}/api/registration/update-check?token=${encodeURIComponent(token)}`),
                    axios.get(`${API_BASE}/api/units/all`).catch(() => ({data: []})),
                ]);

                setUserData(checkRes.data);
                setUnitNumber(checkRes.data.current_unit || "");
                setRole(checkRes.data.current_role || "owner");
                setUnits(unitsRes.data || []);
                setPhase("form");
            } catch (err: any) {
                const status = err?.response?.status;
                if (status === 410) {
                    setErrorMsg(
                        "This update link has expired (links are valid for 7 days). " +
                        "Please register again or contact your Strata Manager."
                    );
                } else if (status === 404) {
                    setErrorMsg(
                        "This link is invalid or has already been used. " +
                        "Please check your email for the most recent update link."
                    );
                } else {
                    setErrorMsg("Unable to load your registration details. Please try again or contact support.");
                }
                setPhase("error");
            }
        })();
    }, [token]);
    // ── Submit updated details ────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/app/(public)/register/update/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!unitNumber && !role) return;
        setSubmitting(true);
        try {
            await axios.put(`${API_BASE}/api/registration/update`, {
                token,
                unit_number: unitNumber || undefined,
                role: role || undefined,
            });
            setPhase("success");
        } catch (err: any) {
            const detail = err?.response?.data?.detail;
            if (detail === "Unit not found. Please select a valid unit.") {
                setErrorMsg("The selected unit was not found. Please choose a valid unit.");
            } else if (err?.response?.status === 410) {
                setErrorMsg("This link has expired. Please register again.");
                setPhase("error");
            } else {
                setErrorMsg(detail || "Failed to update your details. Please try again.");
            }
        } finally {
            setSubmitting(false);
        }
    };

    // ── Render ────────────────────────────────────────────────────────────────

    if (phase === "loading") {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-50">
                <div className="text-center space-y-3">
                    <Loader2 className="h-10 w-10 animate-spin text-[#2F4F4F] mx-auto"/>
                    <p className="text-gray-600">Verifying your update link…</p>
                </div>
            </div>
        );
    }

    if (phase === "error") {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
                <Card className="w-full max-w-md shadow-lg">
                    <CardHeader className="text-center">
                        <AlertTriangle className="h-12 w-12 text-red-500 mx-auto mb-3"/>
                        <CardTitle>Link Problem</CardTitle>
                        <CardDescription>{errorMsg}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                        <Button
                            variant="outline"
                            className="w-full"
                            onClick={() => router.push("/register")}
                        >
                            Register Again
                        </Button>
                        <p className="text-center text-sm text-gray-500">
                            Need help?{" "}
                            <a
                                href="mailto:admin@eastgateresidences.com.au"
                                className="text-[#2F4F4F] underline"
                            >
                                Contact Strata Manager
                            </a>
                        </p>
                    </CardContent>
                </Card>
            </div>
        );
    }

    if (phase === "success") {
        return (
            <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
                <Card className="w-full max-w-md shadow-lg">
                    <CardHeader className="text-center">
                        <CheckCircle className="h-12 w-12 text-green-500 mx-auto mb-3"/>
                        <CardTitle>Details Updated!</CardTitle>
                        <CardDescription>
                            Your registration details have been updated. Our admin team will review
                            your application and you will receive a confirmation email once approved.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <p className="text-center text-sm text-gray-500 mt-2">
                            Questions?{" "}
                            <a
                                href="mailto:admin@eastgateresidences.com.au"
                                className="text-[#2F4F4F] underline"
                            >
                                Contact the Strata Manager
                            </a>
                        </p>
                    </CardContent>
                </Card>
            </div>
        );
    }

    // ── Form ──────────────────────────────────────────────────────────────────

    const issueLabel = REASON_LABELS[userData?.info_request_reason] || "Registration Details";

    return (
        <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4 py-12">
            <Card className="w-full max-w-lg shadow-lg">
                <CardHeader>
                    {/* East Gate branding */}
                    <div className="text-center mb-4">
                        <div
                            className="inline-flex items-center justify-center w-16 h-16 rounded-full mb-3"
                            style={{background: "#2F4F4F"}}
                        >
                            <HelpCircle className="h-8 w-8 text-white"/>
                        </div>
                        <h1 className="text-2xl font-bold" style={{color: "#2F4F4F"}}>
                            East Gate Residences
                        </h1>
                    </div>

                    <CardTitle className="text-center">Update Your Registration</CardTitle>
                    <CardDescription className="text-center">
                        Hi <strong>{userData?.full_name}</strong>, our team noticed an issue with
                        your registration: <strong>{issueLabel}</strong>.
                        <br/>
                        Please correct the details below and resubmit.
                    </CardDescription>
                </CardHeader>

                <CardContent>
                    {errorMsg && (
                        <div
                            className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700 flex items-start gap-2">
                            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5"/>
                            {errorMsg}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="space-y-5">
                        {/* Unit selector */}
                        <div className="space-y-2">
                            <Label htmlFor="unit">Unit Number</Label>
                            <Select
                                value={unitNumber}
                                onValueChange={setUnitNumber}
                            >
                                <SelectTrigger id="unit" data-testid="update-unit-select">
                                    <SelectValue placeholder="Select your unit…"/>
                                </SelectTrigger>
                                <SelectContent className="max-h-64 overflow-y-auto">
                                    {units.length === 0 && (
                                        <SelectItem value={userData?.current_unit || ""} disabled>
                                            {userData?.current_unit || "Loading units…"}
                                        </SelectItem>
                                    )}
                                    {units.map((u: any) => (
                                        <SelectItem key={u.unit_number} value={u.unit_number}>
                                            {u.unit_number}
                                            {u.street_address ? ` — ${u.street_address}` : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-gray-500">
                                Current: <strong>{userData?.current_unit || "None"}</strong>
                            </p>
                        </div>

                        {/* Role selector */}
                        <div className="space-y-2">
                            <Label htmlFor="role">I am a…</Label>
                            <Select value={role} onValueChange={setRole}>
                                <SelectTrigger id="role" data-testid="update-role-select">
                                    <SelectValue placeholder="Select your role…"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {Object.entries(ROLE_LABELS).map(([val, label]) => (
                                        <SelectItem key={val} value={val}>{label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-gray-500">
                                Current: <strong>{ROLE_LABELS[userData?.current_role] || userData?.current_role || "—"}</strong>
                            </p>
                        </div>

                        {/* Info box */}
                        <div className="p-3 bg-blue-50 rounded border border-blue-200 text-sm text-blue-800">
                            ℹ️ After submitting, your application will be queued for admin review again.
                            You&apos;ll receive an approval email once processed.
                        </div>

                        <Button
                            type="submit"
                            className="w-full"
                            style={{background: "#2F4F4F"}}
                            disabled={submitting || !unitNumber}
                            data-testid="submit-update"
                        >
                            {submitting
                                ? <><Loader2 className="h-4 w-4 mr-2 animate-spin"/>Submitting…</>
                                : "Submit Updated Details"}
                        </Button>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
}
// ─── Page wrapper with Suspense (required for useSearchParams in Next.js) ────

/**
 * @generated FunctionHeader
 * Function: RegistrationUpdatePage
 * Path: frontend/src/app/(public)/register/update/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function RegistrationUpdatePage() {
    return (
        <Suspense
            fallback={
                <div className="min-h-screen flex items-center justify-center">
                    <Loader2 className="h-10 w-10 animate-spin text-[#2F4F4F]"/>
                </div>
            }
        >
            <UpdateForm/>
        </Suspense>
    );
}
