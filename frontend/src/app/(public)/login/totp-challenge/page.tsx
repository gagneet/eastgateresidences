"use client";

import React, {Suspense, useState} from "react";
import {useRouter} from "next/navigation";
import {signIn, useSession} from "next-auth/react";
import {Building2, Loader2, Shield} from "lucide-react";
import {toast} from "sonner";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {Card, CardContent} from "@/components/ui/card";
/**
 * TOTP Challenge Page — /login/totp-challenge
 *
 * Flow:
 *  1. The password login produces status=totp_required → NextAuth stores a
 *     totp-pending session (session.totp_required = true, session.totp_token).
 *  2. LoginPage.jsx detects totp_required in the freshly-fetched session and
 *     redirects here (full navigation so the cookie is already set).
 *  3. This page reads session.totp_token, sends it + the user's 6-digit code
 *     to POST /api/auth/totp/challenge.
 *  4. On success the backend returns a full JWT.  We call
 *     signIn("token-exchange", { token }) which validates the JWT via
 *     /api/auth/me and replaces the totp-pending NextAuth session with a
 *     fully-authenticated one.
 *  5. Redirect to /dashboard.
 */
/**
 * @generated FunctionHeader
 * Function: TOTPChallengeContent
 * Path: frontend/src/app/(public)/login/totp-challenge/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function TOTPChallengeContent() {
    const router = useRouter();
    const {data: session, status} = useSession();
    const sess = session as any;

    const [code, setCode] = useState("");
    const [loading, setLoading] = useState(false);
    const [useBackup, setUseBackup] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/app/(public)/login/totp-challenge/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        const trimmed = code.trim();
        if (!trimmed) return;

        const totpToken = sess?.totp_token;
        if (!totpToken) {
            toast.error("Session expired. Please sign in again.");
            router.push("/login");
            return;
        }

        setLoading(true);
        try {
            const backendUrl =
                process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8003";

            // Step 1 — verify the TOTP code with the backend
            const res = await fetch(`${backendUrl}/api/auth/totp/challenge`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    totp_token: totpToken,
                    code: trimmed,
                    is_backup_code: useBackup,
                }),
            });

            const data = await res.json();

            if (!res.ok) {
                toast.error(data.detail || "Invalid code. Please try again.");
                setLoading(false);
                return;
            }

            if (!data.token) {
                toast.error("Unexpected server response. Please try again.");
                setLoading(false);
                return;
            }

            // Step 2 — exchange the full JWT for a proper NextAuth session.
            // The "token-exchange" provider in auth.ts calls /api/auth/me with the
            // token to validate it, then creates a session with accessToken set.
            const result = await signIn("token-exchange", {
                token: data.token,
                redirect: false,
            });

            if (result?.error || result?.ok === false) {
                toast.error("Authentication failed after TOTP. Please sign in again.");
                router.push("/login");
                return;
            }

            toast.success("Verification successful!");
            // Full navigation so the new session cookie is read before rendering /dashboard
            window.location.href = "/dashboard";
        } catch {
            toast.error("Something went wrong. Please try again.");
            setLoading(false);
        }
    };

    // Wait until session status is determined before deciding to redirect
    if (status === "loading") {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }

    // If there is no TOTP-pending session, redirect back to login
    if (status === "unauthenticated" || (status === "authenticated" && !sess?.totp_required)) {
        router.replace("/login");
        return null;
    }

    return (
        <div
            className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12"
            data-testid="totp-challenge-page"
        >
            <div className="w-full max-w-md">
                <div className="text-center mb-8">
                    <div className="inline-flex items-center gap-2 mb-6">
                        <Building2 className="h-10 w-10 text-primary"/>
                        <span className="text-2xl font-bold">East Gate Residences</span>
                    </div>
                    <div className="flex justify-center mb-4">
                        <Shield className="h-12 w-12 text-primary/70"/>
                    </div>
                    <h1 className="text-2xl font-bold">Two-Factor Authentication</h1>
                    <p className="text-muted-foreground mt-1">
                        {useBackup
                            ? "Enter one of your saved backup codes"
                            : "Enter the 6-digit code from your authenticator app"}
                    </p>
                </div>

                <Card className="card-dashboard">
                    <CardContent className="pt-6">
                        <form
                            onSubmit={handleSubmit}
                            className="space-y-4"
                            data-testid="totp-form"
                        >
                            <div className="space-y-2">
                                <Label htmlFor="totp-code">
                                    {useBackup ? "Backup Code" : "Authenticator Code"}
                                </Label>
                                <Input
                                    id="totp-code"
                                    name="code"
                                    type="text"
                                    inputMode={useBackup ? "text" : "numeric"}
                                    pattern={useBackup ? undefined : "[0-9]*"}
                                    maxLength={useBackup ? 12 : 6}
                                    placeholder={useBackup ? "XXXXXXXX" : "000000"}
                                    value={code}
                                    onChange={(e) => setCode(e.target.value)}
                                    autoComplete="one-time-code"
                                    autoFocus
                                    required
                                    className="text-center text-lg tracking-widest"
                                    data-testid="totp-code-input"
                                />
                            </div>

                            <Button
                                type="submit"
                                className="w-full btn-primary"
                                disabled={loading || code.trim().length === 0}
                                data-testid="totp-submit"
                            >
                                {loading ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                        Verifying…
                                    </>
                                ) : (
                                    "Verify"
                                )}
                            </Button>
                        </form>

                        <div className="mt-4 text-center">
                            <button
                                type="button"
                                onClick={() => {
                                    setUseBackup(!useBackup);
                                    setCode("");
                                }}
                                className="text-sm text-primary hover:underline"
                                data-testid="toggle-backup"
                            >
                                {useBackup
                                    ? "Use authenticator app instead"
                                    : "Use a backup code instead"}
                            </button>
                        </div>

                        <div className="mt-4 text-center">
                            <a
                                href="/login"
                                className="text-sm text-muted-foreground hover:underline"
                            >
                                Back to sign in
                            </a>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: TOTPChallengePage
 * Path: frontend/src/app/(public)/login/totp-challenge/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TOTPChallengePage() {
    return (
        <Suspense
            fallback={
                <div className="min-h-screen flex items-center justify-center">
                    <Loader2 className="h-8 w-8 animate-spin text-primary"/>
                </div>
            }
        >
            <TOTPChallengeContent/>
        </Suspense>
    );
}
