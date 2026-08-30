"use client";
import {useAuth} from "@/contexts/AuthContext";
import {useEffect, useState} from "react";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/profile/passport/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    const {api} = useAuth();
    const [data, setData] = useState<any>(null);
    const [downloading, setDownloading] = useState(false);

    useEffect(() => {
        api.get("/residency/my-passport").then((r: any) => setData(r.data)).catch(() => {
        });
    }, []);
    /**
     * @generated FunctionHeader
     * Function: handleDownload
     * Path: frontend/src/app/(app)/profile/passport/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownload = async () => {
        setDownloading(true);
        try {
            const response = await api.get("/residency/my-passport/download", {responseType: "blob"});
            const url = window.URL.createObjectURL(new Blob([response.data]));
            const link = document.createElement("a");
            link.href = url;
            link.setAttribute("download", `tenancy-passport-${data?.unit_number || "passport"}.pdf`);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch {
            // silent failure — user will see nothing downloaded
        } finally {
            setDownloading(false);
        }
    };

    if (!data) return <div className="p-8">Loading passport...</div>;
    return (
        <div className="p-8 max-w-2xl mx-auto">
            <h1 className="text-2xl font-bold mb-4">My Tenancy Passport</h1>
            <div className="rounded-xl border p-6 bg-white space-y-3">
                <div className="text-4xl font-bold text-center">{data.score}<span
                    className="text-lg font-normal text-gray-500">/100</span></div>
                <div className="text-center text-gray-600">Grade {data.grade}</div>
                <div className="pt-4 space-y-2">
                    {data.components && Object.entries(data.components).map(([k, v]: any) => (
                        <div key={k} className="flex justify-between text-sm">
                            <span className="text-gray-600 capitalize">{k.replace(/_/g, " ")}</span>
                            <span className="font-medium">{v}/100</span>
                        </div>
                    ))}
                </div>
                <button
                    onClick={handleDownload}
                    disabled={downloading}
                    className="block w-full text-center mt-4 bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 disabled:opacity-50"
                >
                    {downloading ? "Downloading…" : "Download PDF Passport"}
                </button>
            </div>
        </div>
    );
}
