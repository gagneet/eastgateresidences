"use client";
import Header from "@/components/layout/Header";
import Footer from "@/components/layout/Footer";
import StructuredData from "@/components/shared/StructuredData";
/**
 * @generated FunctionHeader
 * Function: PublicLayout
 * Path: frontend/src/app/(public)/layout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PublicLayout({children}: { children: React.ReactNode }) {
    return (
        <div className="min-h-screen flex flex-col">
            <StructuredData/>
            <Header/>
            <main className="flex-1">{children}</main>
            <Footer/>
        </div>
    );
}
