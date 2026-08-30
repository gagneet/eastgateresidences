"use client";

import React from 'react';

import {useAuth} from '../../contexts/AuthContext';
import {SITE_URL, absoluteUrl} from '@/lib/siteConfig';
/**
 * @generated FunctionHeader
 * Function: safeJsonLd
 * Path: frontend/src/components/shared/StructuredData.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function safeJsonLd(data: object): string {
    return JSON.stringify(data)
        .replace(/</g, '\\u003c')
        .replace(/>/g, '\\u003e')
        .replace(/&/g, '\\u0026');
}
/**
 * @generated FunctionHeader
 * Function: StructuredData
 * Path: frontend/src/components/shared/StructuredData.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function StructuredData() {
    const {selectedBuilding} = useAuth();

    const bName = selectedBuilding?.name || "Our Residences";
    const bUrl = SITE_URL;

    const organizationData = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": bName,
        "url": bUrl,
        "logo": `${bUrl}/favicon.svg`,
        "description": "Premier Strata & Owners Corporation Management platform.",
        "contactPoint": {
            "@type": "ContactPoint",
            "contactType": "customer service"
        }
    };

    const localBusinessData = {
        "@context": "https://schema.org",
        "@type": "ApartmentComplex",
        "name": bName,
        "image": absoluteUrl("/images/east_gate_residences.jpg"),
        "@id": bUrl,
        "url": bUrl,
        "telephone": "+61451234567",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "14 Hoolihan Street",
            "addressLocality": "Denman Prospect",
            "addressRegion": "ACT",
            "postalCode": "2611",
            "addressCountry": "AU"
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": -35.3117,
            "longitude": 149.0344
        },
        "openingHoursSpecification": {
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday"
            ],
            "opens": "09:00",
            "closes": "17:00"
        }
    };

    return (
        <>
            <script
                type="application/ld+json"
                dangerouslySetInnerHTML={{__html: safeJsonLd(organizationData)}}
            />
            <script
                type="application/ld+json"
                dangerouslySetInnerHTML={{__html: safeJsonLd(localBusinessData)}}
            />
        </>
    );
}
