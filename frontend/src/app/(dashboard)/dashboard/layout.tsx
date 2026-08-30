"use client";
import DashboardLayout from "@/components/layout/DashboardLayout";
import {motion} from "framer-motion";

export const dynamic = "force-dynamic";
/**
 * @generated FunctionHeader
 * Function: DashboardGroupLayout
 * Path: frontend/src/app/(dashboard)/dashboard/layout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function DashboardGroupLayout({children}: { children: React.ReactNode }) {
    return (
        <DashboardLayout>
            <div className="relative">
                {/* Mesh Gradient Background Elements - Only visible on this dashboard route */}
                <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
                    <div className="absolute top-0 left-0 w-full h-full bg-slate-50/50"/>
                    <motion.div
                        animate={{
                            scale: [1, 1.2, 1],
                            x: [0, 50, 0],
                            y: [0, 30, 0]
                        }}
                        transition={{duration: 20, repeat: Infinity, ease: "easeInOut"}}
                        className="absolute -top-[10%] -right-[10%] w-[50%] h-[50%] bg-indigo-500/10 blur-[120px] rounded-full"
                    />
                    <motion.div
                        animate={{
                            scale: [1, 1.3, 1],
                            x: [0, -40, 0],
                            y: [0, -50, 0]
                        }}
                        transition={{duration: 25, repeat: Infinity, ease: "easeInOut"}}
                        className="absolute -bottom-[10%] -left-[10%] w-[60%] h-[60%] bg-violet-500/10 blur-[120px] rounded-full"
                    />
                </div>

                {/* Content wrapper to ensure it stays above the fixed background */}
                <div className="relative z-10">
                    {children}
                </div>
            </div>
        </DashboardLayout>
    );
}
