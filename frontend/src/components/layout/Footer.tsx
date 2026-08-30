"use client";
import React, {useEffect, useState} from 'react';
import Link from 'next/link';
import DOMPurify from 'dompurify';
import {Building2, ExternalLink, Mail, MapPin, Phone, ShieldCheck} from 'lucide-react';
import axios from 'axios';
import {useIntegrity} from '../../contexts/IntegrityContext';

interface FooterSettings {
    building_name?: string;
    building_address?: string;
    footer_text?: string;
    contact_email?: string;
    contact_phone?: string;
    ip_string?: string;
    quick_links?: Array<{ label: string; url: string }>;
    resident_links?: Array<{ label: string; url: string }>;
}
/**
 * @generated FunctionHeader
 * Function: Footer
 * Path: frontend/src/components/layout/Footer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const Footer: React.FC = () => {
    const [settings, setSettings] = useState<FooterSettings | null>(null);
    const {ipString} = useIntegrity();
    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchSettings
         * Path: frontend/src/components/layout/Footer.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchSettings = async () => {
            try {
                const response = await axios.get(`${backendUrl}/api/settings`);
                setSettings(response.data);
            } catch (error) {
                console.error('Footer settings fetch failed:', error);
            }
        };
        fetchSettings();
    }, [backendUrl]);

    return (
        <footer className="bg-card border-t border-border" data-testid="main-footer">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
                <div className="grid grid-cols-1 md:grid-cols-4 gap-12">
                    {/* Brand & Dynamic Content */}
                    <div className="col-span-1 md:col-span-2 space-y-6">
                        <div className="flex items-center gap-3">
                            <div className="p-2 bg-primary rounded-lg shadow-sm">
                                <Building2 className="h-6 w-6 text-primary-foreground"/>
                            </div>
                            <div>
                <span className="font-black text-xl tracking-tight block leading-none">
                  {(settings?.building_name || 'Our Residences').toUpperCase()}
                </span>
                                <span
                                    className="text-[10px] uppercase tracking-[0.2em] font-bold text-muted-foreground/60">Community Platform</span>
                            </div>
                        </div>

                        {settings?.footer_text ? (
                            <div
                                className="text-muted-foreground text-sm leading-relaxed max-w-md rich-text-footer"
                                dangerouslySetInnerHTML={{__html: DOMPurify.sanitize(settings.footer_text)}}
                            />
                        ) : (
                            <p className="text-muted-foreground text-sm leading-relaxed max-w-md font-light">
                                A vibrant residential community committed to creating a welcoming environment for all
                                residents.
                                Together, we build more than just homes — we build connections in the heart of Denman
                                Prospect.
                            </p>
                        )}

                        <div className="flex flex-col gap-3 text-sm text-muted-foreground">
                            {settings?.contact_email && (
                                <a href={`mailto:${settings.contact_email}`}
                                   className="flex items-center gap-3 hover:text-primary transition-colors group">
                                    <div className="p-1.5 bg-muted rounded group-hover:bg-primary/10 transition-colors">
                                        <Mail className="h-3.5 w-3.5"/>
                                    </div>
                                    {settings.contact_email}
                                </a>
                            )}
                            {settings?.contact_phone && (
                                <a href={`tel:${settings.contact_phone}`}
                                   className="flex items-center gap-3 hover:text-primary transition-colors group">
                                    <div className="p-1.5 bg-muted rounded group-hover:bg-primary/10 transition-colors">
                                        <Phone className="h-3.5 w-3.5"/>
                                    </div>
                                    {settings.contact_phone}
                                </a>
                            )}
                            {settings?.building_address && (
                                <div className="flex items-center gap-3 group">
                                    <div className="p-1.5 bg-muted rounded">
                                        <MapPin className="h-3.5 w-3.5"/>
                                    </div>
                                    <span>{settings.building_address}</span>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Dynamic Quick Links */}
                    <div className="space-y-6">
                        <div className="flex items-center gap-2">
                            <ExternalLink className="h-4 w-4 text-primary/60"/>
                            <h4 className="font-bold text-sm uppercase tracking-widest">Quick Links</h4>
                        </div>
                        <nav className="flex flex-col gap-3 text-sm">
                            {settings?.quick_links && settings.quick_links.length > 0 ? (
                                settings.quick_links.map((link, i) => (
                                    <Link
                                        key={i}
                                        href={link.url}
                                        className="text-muted-foreground hover:text-foreground transition-all hover:translate-x-1 duration-200"
                                    >
                                        {link.label}
                                    </Link>
                                ))
                            ) : (
                                <>
                                    <Link href="/"
                                          className="text-muted-foreground hover:text-foreground transition-colors">Home</Link>
                                    <Link href="/about"
                                          className="text-muted-foreground hover:text-foreground transition-colors">About
                                        Us</Link>
                                    <Link href="/blog"
                                          className="text-muted-foreground hover:text-foreground transition-colors">News
                                        & Updates</Link>
                                    <Link href="/marketplace"
                                          className="text-muted-foreground hover:text-foreground transition-colors">Marketplace</Link>
                                </>
                            )}
                        </nav>
                    </div>

                    {/* Dynamic Resident Resources */}
                    <div className="space-y-6">
                        <div className="flex items-center gap-2">
                            <ShieldCheck className="h-4 w-4 text-emerald-500/60"/>
                            <h4 className="font-bold text-sm uppercase tracking-widest text-foreground">For
                                Residents</h4>
                        </div>
                        <nav className="flex flex-col gap-3 text-sm font-medium">
                            {settings?.resident_links && settings.resident_links.length > 0 ? (
                                settings.resident_links.map((link, i) => (
                                    <Link
                                        key={i}
                                        href={link.url}
                                        className="text-muted-foreground hover:text-emerald-600 transition-all hover:translate-x-1 duration-200"
                                    >
                                        {link.label}
                                    </Link>
                                ))
                            ) : (
                                <>
                                    <Link href="/login"
                                          className="text-muted-foreground hover:text-foreground transition-colors">Resident
                                        Login</Link>
                                    <Link href="/register"
                                          className="text-muted-foreground hover:text-foreground transition-colors">Apply
                                        for Access</Link>
                                    <Link href="/documents"
                                          className="text-muted-foreground hover:text-foreground transition-colors">Community
                                        Library</Link>
                                    <a href="/user-guides/quick_index.html" target="_blank" rel="noopener noreferrer"
                                       className="text-muted-foreground hover:text-foreground transition-colors">Resident
                                        Handbook</a>
                                </>
                            )}
                        </nav>
                    </div>
                </div>

                {/* SEO Regional Links / Keywords */}
                <div className="mt-12 pt-8 border-t border-dashed border-border/50">
                    <div
                        className="text-[10px] text-muted-foreground uppercase tracking-widest font-bold mb-4">Strategic
                        Locations
                    </div>
                    <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px] text-muted-foreground/70 font-medium">
                        <Link href="/marketplace?location=act" className="hover:text-primary transition-colors">ACT
                            Strata</Link>
                        <Link href="/marketplace?location=nsw" className="hover:text-primary transition-colors">NSW
                            Owners Corporation</Link>
                        <Link href="/marketplace?location=vic" className="hover:text-primary transition-colors">Victoria
                            Strata Management</Link>
                        <Link href="/marketplace?location=wa" className="hover:text-primary transition-colors">Western
                            Australia Properties</Link>
                        <Link href="/marketplace?location=qld" className="hover:text-primary transition-colors">Queensland
                            Living</Link>
                        <Link href="/marketplace?location=sa" className="hover:text-primary transition-colors">South
                            Australia Services</Link>
                        <Link href="/marketplace?location=tas" className="hover:text-primary transition-colors">Tasmania
                            Residences</Link>
                        <Link href="/marketplace?location=nz" className="hover:text-primary transition-colors">New
                            Zealand Strata</Link>
                    </div>
                </div>

                <div className="mt-8 pt-8 border-t border-border/50">
                    <div className="flex flex-col md:flex-row justify-between items-center gap-6">
                        <div className="flex items-center gap-4">
                            {!settings?.building_name && <img src="/eastgate-logo.png" alt="Logo"
                                                              className="h-6 w-auto opacity-40 grayscale hover:grayscale-0 transition-all"/>}
                            <p className="text-xs text-muted-foreground font-medium">
                                © {new Date().getFullYear()} {settings?.building_name || 'Your Strata Community'}.
                            </p>
                        </div>
                        <div
                            className="flex gap-8 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                            <Link href="/privacy" className="hover:text-primary transition-colors">Privacy</Link>
                            <Link href="/terms" className="hover:text-primary transition-colors">Terms</Link>
                            {settings?.contact_email && (
                                <a href={`mailto:${settings.contact_email}`}
                                   className="hover:text-primary transition-colors">Contact Support</a>
                            )}
                        </div>
                    </div>
                    <div className="mt-8 text-center border-t border-dashed pt-4 border-muted-foreground/10">
                        <p id="ip-protection-notice"
                           className="text-[10px] text-muted-foreground/40 font-medium transition-all duration-300 opacity-100 visible"
                           style={{display: 'block', visibility: 'visible', opacity: 1}}>
                            {ipString || settings?.ip_string || "A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au"}
                        </p>
                    </div>
                </div>
            </div>
        </footer>
    );
};

export default Footer;
