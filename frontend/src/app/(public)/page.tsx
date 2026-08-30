import type {Metadata} from "next";
import HomePage from "@/pages/public/HomePage";

export const metadata: Metadata = {
    title: {
        absolute: "Strata Management & Community Living Platform"
    },
    description: "Experience premium community living. Award-winning strata management and owners corporation services across Australia.",
    keywords: ["strata management", "owners corporation", "community living", "luxury apartments"],
};
/**
 * @generated FunctionHeader
 * Function: Home
 * Path: frontend/src/app/(public)/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Home() {
    return <HomePage/>;
}
