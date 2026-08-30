import {redirect} from "next/navigation";
// /insurance is the menu landing point for the insurance feature cluster.
// Claims is the current default workspace until a dedicated insurance overview exists.
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/insurance/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    redirect("/insurance/claims");
}
