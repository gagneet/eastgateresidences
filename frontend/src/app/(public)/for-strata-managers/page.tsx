import React from 'react';
import {Button} from '@/components/ui/button';
import {Card, CardContent} from '@/components/ui/card';
import {ChevronRight, LayoutDashboard, ShieldCheck, Zap} from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: ForStrataManagersPage
 * Path: frontend/src/app/(public)/for-strata-managers/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ForStrataManagersPage() {
    return (
        <div className="min-h-screen bg-slate-50">
            {/* Hero */}
            <section className="bg-slate-900 text-white py-24 px-6 overflow-hidden relative">
                <div className="absolute top-0 left-1/4 w-1/2 h-full bg-primary/20 blur-[120px] pointer-events-none"/>
                <div className="max-w-6xl mx-auto relative z-10 text-center">
                    <Badge className="mb-6 bg-primary/20 text-primary border-primary/30">FOR STRATA
                        PROFESSIONALS</Badge>
                    <h1 className="text-5xl md:text-7xl font-black tracking-tight mb-8">
                        The Strata Platform <br/>
                        <span className="text-primary">That Triage's Itself.</span>
                    </h1>
                    <p className="text-xl text-slate-400 max-w-3xl mx-auto mb-12">
                        Reduce administrative load by up to 40% with smart request routing,
                        automated transparency, and built-in resident self-service.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-4 justify-center">
                        <Button size="lg" className="rounded-full px-8 h-14 text-lg font-bold">Request a Demo</Button>
                        <Button variant="outline" size="lg"
                                className="rounded-full px-8 h-14 text-lg font-bold bg-white/5 border-white/20 text-white hover:bg-white/10">View
                            Features</Button>
                    </div>
                </div>
            </section>

            {/* Features */}
            <section className="py-24 px-6 max-w-6xl mx-auto">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                    <Card className="border-none shadow-sm group hover:shadow-xl transition-all">
                        <CardContent className="p-8">
                            <div
                                className="h-12 w-12 rounded-2xl bg-sky-50 text-sky-600 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                                <Zap size={24}/>
                            </div>
                            <h3 className="text-xl font-bold mb-4">Smart Intake Router</h3>
                            <p className="text-slate-500 leading-relaxed">
                                Auto-classify every resident email and portal message. deterministic keyword matching
                                ensures 90%+ routing accuracy.
                            </p>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-sm group hover:shadow-xl transition-all">
                        <CardContent className="p-8">
                            <div
                                className="h-12 w-12 rounded-2xl bg-emerald-50 text-emerald-600 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                                <LayoutDashboard size={24}/>
                            </div>
                            <h3 className="text-xl font-bold mb-4">Portfolio Command</h3>
                            <p className="text-slate-500 leading-relaxed">
                                Unified oversight for your entire portfolio. Track health scores, arrears, and
                                compliance across all managed buildings in one cockpit.
                            </p>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-sm group hover:shadow-xl transition-all">
                        <CardContent className="p-8">
                            <div
                                className="h-12 w-12 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                                <ShieldCheck size={24}/>
                            </div>
                            <h3 className="text-xl font-bold mb-4">Governance Engine</h3>
                            <p className="text-slate-500 leading-relaxed">
                                Full transparency for EC members and committees. From weighted voting to automated
                                by-law acknowledgement.
                            </p>
                        </CardContent>
                    </Card>
                </div>
            </section>

            {/* CTA */}
            <section className="py-24 bg-white border-t border-slate-100">
                <div className="max-w-4xl mx-auto text-center px-6">
                    <h2 className="text-4xl font-black mb-6">Ready to scale your management business?</h2>
                    <p className="text-lg text-slate-500 mb-10">Join the waitlist for our Australian nationwide SaaS
                        release.</p>
                    <Button size="lg" className="rounded-full px-12 h-16 text-xl font-bold">
                        Join the Waitlist <ChevronRight className="ml-2 h-6 w-6"/>
                    </Button>
                </div>
            </section>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: Badge
 * Path: frontend/src/app/(public)/for-strata-managers/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Badge({children, className}: { children: React.ReactNode, className?: string }) {
    return (
        <span
            className={`inline-block px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${className}`}>
      {children}
    </span>
    );
}
