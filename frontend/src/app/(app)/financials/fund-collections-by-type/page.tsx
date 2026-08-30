"use client";
import {Suspense} from "react";
import FundCollectionsByUnitTypePage from "@/pages/dashboard/FundCollectionsByUnitTypePage";

export default function Page() {
    return (
        <Suspense>
            <FundCollectionsByUnitTypePage/>
        </Suspense>
    );
}
