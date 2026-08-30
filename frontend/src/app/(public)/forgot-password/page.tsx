// @ts-nocheck
"use client";
import React, {useState} from "react";
import Link from "next/link";
import {ArrowLeft, Building2, CheckCircle, Loader2, Mail} from "lucide-react";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {Card, CardContent} from "@/components/ui/card";
import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";
/**
 * @generated FunctionHeader
 * Function: ForgotPasswordPage
 * Path: frontend/src/app/(public)/forgot-password/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ForgotPasswordPage() {
    const [email, setEmail] = useState("");
    const [loading, setLoading] = useState(false);
    const [sent, setSent] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/app/(public)/forgot-password/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!email) return;
        setLoading(true);
        try {
            await axios.post(`${API_BASE}/api/auth/forgot-password`, {email});
            setSent(true);
        } catch {
            // Always show success to prevent email enumeration
            setSent(true);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12">
            <div className="w-full max-w-md">
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <Building2 className="h-10 w-10 text-primary"/>
                        <span className="text-2xl font-bold">East Gate Residences</span>
                    </Link>
                    <h1 className="text-2xl font-bold">Reset Password</h1>
                    <p className="text-muted-foreground">
                        Enter your email and we&apos;ll send reset instructions.
                    </p>
                </div>

                <Card className="card-dashboard">
                    <CardContent className="pt-6">
                        {sent ? (
                            <div className="text-center py-4 space-y-4">
                                <CheckCircle className="h-12 w-12 text-emerald-500 mx-auto"/>
                                <div>
                                    <p className="font-semibold text-lg">Check your email</p>
                                    <p className="text-sm text-muted-foreground mt-1">
                                        If an account exists for <strong>{email}</strong>, you will
                                        receive password reset instructions shortly.
                                    </p>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Didn&apos;t receive an email? Contact the strata manager at{" "}
                                    <a
                                        href="mailto:admin@eastgateresidences.com.au"
                                        className="text-primary hover:underline"
                                    >
                                        admin@eastgateresidences.com.au
                                    </a>
                                </p>
                            </div>
                        ) : (
                            <form onSubmit={handleSubmit} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="email">Email Address</Label>
                                    <div className="relative">
                                        <Mail
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                        <Input
                                            id="email"
                                            type="email"
                                            placeholder="your@email.com"
                                            value={email}
                                            onChange={(e) => setEmail(e.target.value)}
                                            className="pl-10"
                                            required
                                        />
                                    </div>
                                </div>

                                <Button
                                    type="submit"
                                    className="w-full btn-primary"
                                    disabled={loading}
                                >
                                    {loading ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                            Sending...
                                        </>
                                    ) : (
                                        "Send Reset Instructions"
                                    )}
                                </Button>
                            </form>
                        )}

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
