"use client";

import {Suspense} from "react";
import CommunicationsWorkspacePage from "@/pages/dashboard/CommunicationsWorkspacePage";

export default function Page() {
    return (
        <Suspense>
            <CommunicationsWorkspacePage/>
        </Suspense>
    );
}
