"use client";
import React, {createContext, useContext, useEffect, useRef, useState} from 'react';
import {ShieldAlert} from 'lucide-react';
import axios from 'axios';

interface IntegrityContextValue {
    ipString: string;
}

const IntegrityContext = createContext<IntegrityContextValue | null>(null);
/**
 * @generated FunctionHeader
 * Function: IntegrityProvider
 * Path: frontend/src/contexts/IntegrityContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const IntegrityProvider: React.FC<{ children: React.ReactNode }> = ({children}) => {
    const [violation, setViolation] = useState<string | null>(null);
    const [ipString, setIpString] = useState("A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au");
    const [isSettled, setIsSettled] = useState(false);
    // useRef — imported explicitly; not React.useRef (consistent with other hooks).
    const consecutiveFailures = useRef(0);

    // Settle time for hydration and rendering
    useEffect(() => {
        const timer = setTimeout(() => setIsSettled(true), 20000);
        return () => clearTimeout(timer);
    }, []);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: checkIntegrity
         * Path: frontend/src/contexts/IntegrityContext.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const checkIntegrity = () => {
            if (!isSettled) return;
            const footerNotice = document.getElementById('ip-protection-notice');
            const dashboardNotice = document.getElementById('dashboard-ip-notice');
            /**
             * @generated FunctionHeader
             * Function: checkElement
             * Path: frontend/src/contexts/IntegrityContext.tsx
             *
             * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
             */
            const checkElement = (el: HTMLElement | null, name: string) => {
                if (!el) return `${name} missing from DOM`;

                const style = window.getComputedStyle(el);
                if (style.display === 'none') return `${name} is hidden (display: none)`;
                if (style.visibility === 'hidden') return `${name} is hidden (visibility: hidden)`;
                if (parseFloat(style.opacity) < 0.1) return `${name} is transparent`;

                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return `${name} has zero size`;

                // Verify content - allowed to be changed if it matches backend setting
                // We'll trust the ipString state which is updated from /api/settings
                if (!el.innerText.includes("Silverfox Technologies") && el.innerText !== ipString) {
                    return `${name} content tampered with`;
                }

                return null;
            };

            // Which notice applies is a function of which layout rendered this page,
            // not the URL prefix — DashboardLayout (used by both the (dashboard) and
            // (app) route groups) renders #dashboard-ip-notice; only the public
            // marketing Footer renders #ip-protection-notice. A hardcoded
            // pathname.startsWith('/dashboard') check broke here once ~150 pages
            // moved to /financials, /admin, /intelligence, /powerhouse, etc. while
            // still using DashboardLayout (2026-08-07 product-namespace migration) —
            // it kept looking for the footer notice on pages that never render one,
            // firing a false-positive "critical security violation" lockout. Checking
            // DOM presence directly is immune to any future route renames.
            const error = dashboardNotice
                ? checkElement(dashboardNotice, 'Dashboard IP Notice')
                : checkElement(footerNotice, 'Footer IP Notice');

            if (error) {
                consecutiveFailures.current += 1;
                console.error(`Integrity check failed (${consecutiveFailures.current}/2):`, error);
                // Require 2 consecutive failures to avoid false positives from hydration delays
                if (consecutiveFailures.current >= 2) {
                    setViolation(error);
                }
            } else {
                consecutiveFailures.current = 0;
                setViolation(null);
            }
        };
        /**
         * @generated FunctionHeader
         * Function: fetchSettings
         * Path: frontend/src/contexts/IntegrityContext.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchSettings = async () => {
            try {
                const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';
                const response = await axios.get(`${backendUrl}/api/settings`);
                if (response.data?.ip_string) {
                    setIpString(response.data.ip_string);
                }
            } catch (err) {
                console.error("Failed to fetch settings for integrity check", err);
            }
        };

        fetchSettings();
        // Check every 60 seconds
        const interval = setInterval(checkIntegrity, 60000);
        return () => clearInterval(interval);
    }, [ipString, isSettled]);

    if (violation) {
        return (
            <div
                className="fixed inset-0 z-[9999] bg-slate-950 flex items-center justify-center p-6 text-white font-sans">
                <div className="max-w-md w-full space-y-8 text-center">
                    <div className="flex justify-center">
                        <div className="p-4 bg-red-500/10 rounded-full">
                            <ShieldAlert className="h-16 w-16 text-red-500 animate-pulse"/>
                        </div>
                    </div>
                    <div className="space-y-4">
                        <h1 className="text-3xl font-bold tracking-tighter sm:text-4xl">System Integrity Error</h1>
                        <p className="text-slate-400 leading-relaxed">
                            A critical security violation has been detected. The application's intellectual property
                            protection has been tampered with or removed.
                        </p>
                        <div
                            className="p-4 bg-slate-900 border border-slate-800 rounded-lg text-left font-mono text-xs text-red-400">
                            <p className="font-bold mb-1 uppercase tracking-wider opacity-50 text-[10px]">Error
                                details:</p>
                            <p>{violation}</p>
                        </div>
                    </div>
                    <div className="pt-4 space-y-3">
                        <p className="text-sm text-slate-500 italic">
                            Please restore the required branding elements to resume normal operations.
                        </p>
                        <button
                            onClick={() => window.location.reload()}
                            className="mt-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white text-sm rounded-lg transition-colors"
                        >
                            Reload Page
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <IntegrityContext.Provider value={{ipString}}>
            {children}
        </IntegrityContext.Provider>
    );
};
/**
 * @generated FunctionHeader
 * Function: useIntegrity
 * Path: frontend/src/contexts/IntegrityContext.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const useIntegrity = () => {
    const context = useContext(IntegrityContext);
    if (!context) {
        // Return a default value during SSR
        if (typeof window === 'undefined') {
            return {ipString: ''};
        }
        throw new Error('useIntegrity must be used within an IntegrityProvider');
    }
    return context;
};
