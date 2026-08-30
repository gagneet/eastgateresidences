import type {Metadata} from "next";
import {Inter, JetBrains_Mono, Manrope} from "next/font/google";
import {AuthProvider} from "@/contexts/AuthContext";
import {IntegrityProvider} from "@/contexts/IntegrityContext";
import {TooltipProvider} from "@/components/ui/tooltip";
import {Toaster} from "sonner";
import {auth} from "@/auth";
import {SITE_NAME, SITE_URL} from "@/lib/siteConfig";
import "./globals.css";

const manrope = Manrope({
    variable: "--font-manrope",
    subsets: ["latin"],
    display: "swap",
});

const inter = Inter({
    variable: "--font-inter",
    subsets: ["latin"],
    display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
    variable: "--font-jetbrains-mono",
    subsets: ["latin"],
    display: "swap",
});

export const metadata: Metadata = {
    title: {
        default: "East Gate | Premier Strata & Owners Corporation Management",
        template: "%s | East Gate"
    },
    description: "Comprehensive Owners Corporation and Strata Management platform for modern residential communities. Serving ACT, NSW, VIC, WA, QLD, SA, TAS, and New Zealand.",
    keywords: [
        "east gate", "eastgate", "denman prospect", "mixed living", "strata management", "strata",
        "owners", "owners corporation", "townhouse sales", "apartments rent", "apartments sales",
        "unit sales", "services", "garden sale", "garage sale", "ACT", "New South Wales", "Victoria",
        "Western Australia", "Queensland", "South Australia", "Melbourne", "Canberra", "Sydney",
        "Adelaide", "Brisbane", "Perth", "Tasmania", "Hobart", "New Zealand"
    ],
    authors: [{name: "Silverfox Technologies"}],
    creator: "Silverfox Technologies",
    publisher: "East Gate",
    formatDetection: {
        email: false,
        address: false,
        telephone: false,
    },
    metadataBase: new URL(SITE_URL),
    openGraph: {
        title: "East Gate | Strata Management Platform",
        description: "Leading strata and owners corporation management platform for premium residential living in Australia and New Zealand.",
        url: SITE_URL,
        siteName: SITE_NAME,
        images: [
            {
                url: "/images/east_gate_residences.jpg",
                width: 1200,
                height: 630,
                alt: "East Gate",
            },
        ],
        locale: "en_AU",
        type: "website",
    },
    twitter: {
        card: "summary_large_image",
        title: "East Gate | Strata Management Platform",
        description: "Leading strata and owners corporation management platform for premium residential living.",
        images: ["/images/east_gate_residences.jpg"],
    },
    icons: {
        icon: [
            {url: "/favicon.svg", type: "image/svg+xml"},
            {url: "/east-gate.ico", sizes: "any"},
        ],
        shortcut: "/east-gate.ico",
    },
    robots: {
        index: true,
        follow: true,
        googleBot: {
            index: true,
            follow: true,
            "max-video-preview": -1,
            "max-image-preview": "large",
            "max-snippet": -1,
        },
    },
};

export const dynamic = "force-dynamic";
/**
 * @generated FunctionHeader
 * Function: RootLayout
 * Path: frontend/src/app/layout.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default async function RootLayout({
                                             children,
                                         }: Readonly<{
    children: React.ReactNode;
}>) {
    const session = await auth();

    return (
        <html lang="en" suppressHydrationWarning>
        <body
            className={`${manrope.variable} ${inter.variable} ${jetbrainsMono.variable} antialiased`}
            suppressHydrationWarning
        >
        <AuthProvider session={session}>
            <TooltipProvider>
                <IntegrityProvider>
                    {children}
                </IntegrityProvider>
            </TooltipProvider>
            <Toaster
                position="top-right"
                richColors
                closeButton
                toastOptions={{
                    className: 'font-sans',
                }}
            />
        </AuthProvider>
        </body>
        </html>
    );
}
