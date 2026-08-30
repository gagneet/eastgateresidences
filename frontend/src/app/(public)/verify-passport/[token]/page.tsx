import {notFound} from "next/navigation";
/**
 * @generated FunctionHeader
 * Function: getData
 * Path: frontend/src/app/(public)/verify-passport/[token]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
async function getData(token: string) {
    try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8003"}/api/residency/verify/${token}`, {cache: "no-store"});
        if (!res.ok) return null;
        return res.json();
    } catch {
        return null;
    }
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(public)/verify-passport/[token]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default async function Page({params}: { params: { token: string } }) {
    const data = await getData(params.token);
    if (!data) return notFound();
    return (
        <div className="min-h-screen flex items-center justify-center bg-gray-50 p-8">
            <div className="max-w-md w-full bg-white rounded-2xl shadow-lg p-8">
                <div className="text-4xl text-center mb-4">✅</div>
                <h1 className="text-xl font-bold text-center mb-2">Tenancy Passport Verified</h1>
                <p className="text-gray-600 text-center text-sm mb-6">{data.building_name}</p>
                <div className="space-y-2 text-sm">
                    <div className="flex justify-between"><span className="text-gray-500">Resident</span><span
                        className="font-medium">{data.resident_name_partial}</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">Unit</span><span
                        className="font-medium">{data.unit_number}</span></div>
                    <div className="flex justify-between"><span className="text-gray-500">Residency Score</span><span
                        className="font-bold text-green-700">{data.score}/100</span></div>
                    <div className="flex justify-between"><span
                        className="text-gray-500">Issued</span><span>{data.issued_at?.split("T")[0]}</span></div>
                </div>
                <div className="mt-8 p-4 bg-gray-50 rounded-lg text-xs text-gray-500 border">
                    <p>{data.pitch}</p>
                    <a href="https://eastgateresidences.com.au/for-strata-managers"
                       className="text-green-700 font-medium mt-1 block">Learn more →</a>
                </div>
            </div>
        </div>
    );
}
