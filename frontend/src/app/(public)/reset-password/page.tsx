// @ts-nocheck
"use client";

import React, {Suspense, useState} from "react";
import Link from "next/link";
import {useRouter, useSearchParams} from "next/navigation";
import {AlertTriangle, ArrowLeft, Building2, CheckCircle, Eye, EyeOff, Loader2, Lock,} from "lucide-react";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {Card, CardContent} from "@/components/ui/card";
import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";
/**
 * @generated FunctionHeader
 * Function: ResetPasswordForm
 * Path: frontend/src/app/(public)/reset-password/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ResetPasswordForm() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const token = searchParams.get("token") || "";

    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading] = useState(false);
    const [phase, setPhase] = useState<"form" | "success" | "error">("form");
    const [errorMsg, setErrorMsg] = useState("");
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/app/(public)/reset-password/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setErrorMsg("");

        if (!token) {
            setErrorMsg("No reset token found. Please use the link from your email.");
            setPhase("error");
            return;
        }

        if (password.length < 8) {
            setErrorMsg("Password must be at least 8 characters.");
            return;
        }

        if (password !== confirm) {
            setErrorMsg("Passwords do not match.");
            return;
        }

        setLoading(true);
        try {
            await axios.post(`${API_BASE}/api/auth/reset-password`, {
                token,
                new_password: password,
            });
            setPhase("success");
        } catch (err: any) {
            const detail = err?.response?.data?.detail;
            if (err?.response?.status === 400) {
                setErrorMsg(detail || "This reset link is invalid or has already been used.");
                setPhase("error");
            } else {
                setErrorMsg(detail || "Something went wrong. Please try again.");
            }
        } finally {
            setLoading(false);
        }
    };

    if (phase === "success") {
        return (
            <div className="text-center py-4 space-y-4">
                <CheckCircle className="h-12 w-12 text-emerald-500 mx-auto"/>
                <div>
                    <p className="font-semibold text-lg">Password updated</p>
                    <p className="text-sm text-muted-foreground mt-1">
                        Your password has been reset successfully. You can now sign in with your
                        new password.
                    </p>
                </div>
                <Button
                    className="w-full btn-primary"
                    onClick={() => router.push("/login")}
                >
                    Go to Sign In
                </Button>
            </div>
        );
    }

    if (phase === "error") {
        return (
            <div className="text-center py-4 space-y-4">
                <AlertTriangle className="h-12 w-12 text-red-500 mx-auto"/>
                <div>
                    <p className="font-semibold text-lg">Link problem</p>
                    <p className="text-sm text-muted-foreground mt-1">{errorMsg}</p>
                </div>
                <div className="flex flex-col gap-2">
                    <Button
                        variant="outline"
                        className="w-full"
                        onClick={() => router.push("/forgot-password")}
                    >
                        Request a new reset link
                    </Button>
                    <Link href="/login" className="text-sm text-primary hover:underline">
                        Back to Sign In
                    </Link>
                </div>
            </div>
        );
    }

    return (
        <form onSubmit={handleSubmit} className="space-y-4">
            {errorMsg && (
                <div className="p-3 rounded-md bg-red-50 border border-red-200 text-sm text-red-700">
                    {errorMsg}
                </div>
            )}

            <div className="space-y-2">
                <Label htmlFor="password">New Password</Label>
                <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                    <Input
                        id="password"
                        type={showPassword ? "text" : "password"}
                        placeholder="At least 8 characters"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        className="pl-10 pr-10"
                        required
                    />
                    <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                        {showPassword ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}
                    </button>
                </div>
                <p className="text-xs text-muted-foreground">Minimum 8 characters.</p>
            </div>

            <div className="space-y-2">
                <Label htmlFor="confirm">Confirm New Password</Label>
                <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                    <Input
                        id="confirm"
                        type={showPassword ? "text" : "password"}
                        placeholder="Re-enter your new password"
                        value={confirm}
                        onChange={(e) => setConfirm(e.target.value)}
                        className="pl-10"
                        required
                    />
                </div>
            </div>

            <Button type="submit" className="w-full btn-primary" disabled={loading}>
                {loading ? (
                    <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                        Updating password…
                    </>
                ) : (
                    "Set New Password"
                )}
            </Button>
        </form>
    );
}
/**
 * @generated FunctionHeader
 * Function: ResetPasswordPage
 * Path: frontend/src/app/(public)/reset-password/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ResetPasswordPage() {
    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12">
            <div className="w-full max-w-md">
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <Building2 className="h-10 w-10 text-primary"/>
                        <span className="text-2xl font-bold">East Gate Residences</span>
                    </Link>
                    <h1 className="text-2xl font-bold">Set New Password</h1>
                    <p className="text-muted-foreground">Enter and confirm your new password below.</p>
                </div>

                <Card className="card-dashboard">
                    <CardContent className="pt-6">
                        <Suspense
                            fallback={
                                <div className="flex items-center justify-center py-8">
                                    <Loader2 className="h-6 w-6 animate-spin text-primary"/>
                                </div>
                            }
                        >
                            <ResetPasswordForm/>
                        </Suspense>

                        <div className="mt-6 text-center">
                            <Link
                                href="/login"
                                className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                            >
                                <ArrowLeft className="h-3.5 w-3.5"/>
                                Back to Sign In
                            </Link>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
