"use client";
import React, { useCallback, useEffect, useState } from 'react';
import DOMPurify from 'dompurify';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Bell,
    Building2,
    Calendar,
    CheckCircle2,
    Edit,
    ExternalLink,
    Home,
    Info,
    Loader2,
    Mail,
    MapPin,
    Phone,
    Plus,
    Trash2,
    Users
} from 'lucide-react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../components/ui/dialog';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: AboutPage
 * Path: frontend/src/pages/public/AboutPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AboutPage = () => {
    const {api, user, selectedBuilding, isAuthenticated} = useAuth();
    const [settings, setSettings] = useState(null);
    const [ecMembers, setEcMembers] = useState([]);
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [currentMember, setCurrentMember] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [formData, setFormData] = useState({
        name: '',
        position: '',
        bio: '',
        email: '',
        phone: '',
        image: '',
        order: 0
    });

    // SEO and Metadata
    useEffect(() => {
        const bName = settings?.building_name || 'Our Residences';
        const bAddr = settings?.building_address || '';
        document.title = `About ${bName} | ${bAddr}`;
        let metaDesc = document.querySelector('meta[name="description"]');
        if (!metaDesc) {
            metaDesc = document.createElement('meta');
            metaDesc.name = 'description';
            document.head.appendChild(metaDesc);
        }
        const desc = settings?.building_description?.replace(/<[^>]*>/g, '').substring(0, 160) || `Learn about ${bName}. A premier residential community.`;
        metaDesc.setAttribute("content", desc);
    }, [settings]);

    const canManage = user && ['super_admin'].includes(user.role);

    const fetchData = useCallback(async () => {
        const buildingId = selectedBuilding?.id;
        if (!buildingId) return;  // wait until building context resolves
        try {
            const headers = {'X-Building-ID': buildingId};
            const fetcher = isAuthenticated ? api : {

                get: (url, config) => axios.get(`${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/api${url}`, {
                    ...config,
                    headers
                })
            };

            const [settingsRes, ecRes] = await Promise.all([
                fetcher.get('/settings', {headers}),
                fetcher.get('/ec-members', {headers})
            ]);
            setSettings(settingsRes.data);
            setEcMembers(ecRes.data || []);
        } catch (error) {
            console.error('Failed to fetch data:', error);
        }
    }, [api, selectedBuilding, isAuthenticated]);

    useEffect(() => {
        fetchData();
    }, [fetchData, selectedBuilding?.id]);
    /**
     * @generated FunctionHeader
     * Function: handleOpenDialog
     * Path: frontend/src/pages/public/AboutPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleOpenDialog = (member = null) => {
        if (member) {
            setCurrentMember(member);
            setFormData({
                name: member.name,
                position: member.position,
                bio: member.bio || '',
                email: member.email || '',
                phone: member.phone || '',
                image: member.image || '',
                order: member.order || 0
            });
        } else {
            setCurrentMember(null);
            setFormData({
                name: '',
                position: '',
                bio: '',
                email: '',
                phone: '',
                image: '',
                order: 0
            });
        }
        setIsDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/public/AboutPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            if (currentMember) {
                await api.put(`/ec-members/${currentMember.id}`, formData);
                toast.success('EC Member updated');
            } else {
                await api.post('/ec-members', formData);
                toast.success('EC Member added');
            }
            setIsDialogOpen(false);
            fetchData();
        } catch (error) {
            toast.error('Failed to save EC member');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/public/AboutPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id) => {
        if (!window.confirm('Remove this EC member?')) return;
        try {
            await api.delete(`/ec-members/${id}`);
            toast.success('Member removed');
            fetchData();
        } catch (error) {
            toast.error('Failed to remove');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleTriggerAGMAlert
     * Path: frontend/src/pages/public/AboutPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleTriggerAGMAlert = async () => {
        if (!window.confirm('Send AGM alert to Super Admin? This will notify them to update EC details.')) return;
        try {
            await api.post('/agm/trigger-alert');
            toast.success('AGM alert sent to Super Admin for review.');
        } catch (error) {
            toast.error('Failed to send alert');
        }
    };

    return (
        <div className="min-h-screen bg-white" data-testid="about-page">
            {/* Hero Section */}
            <section className="relative h-[60vh] min-h-[500px] flex items-center justify-center overflow-hidden">
                <div className="absolute inset-0">
                    <img
                        src="/images/east-gate-exterior.jpg"
                        alt="East Gate Residences Exterior"
                        className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 bg-gradient-to-b from-black/70 via-black/40 to-black/80"/>
                </div>
                <div className="relative z-10 text-center px-4 max-w-4xl mx-auto">
                    <Badge className="mb-4 bg-primary/20 text-white border-white/30 backdrop-blur-sm px-4 py-1">
                        {settings?.established_year ? `EST. ${settings.established_year}` : 'Our Community'}
                    </Badge>
                    <h1 className="text-4xl sm:text-5xl lg:text-7xl font-bold text-white mb-6 drop-shadow-lg uppercase tracking-tight">
                        {settings?.building_name || selectedBuilding?.name || 'Our Residences'}
                    </h1>
                    <p className="text-xl sm:text-2xl text-white/90 leading-relaxed drop-shadow max-w-2xl mx-auto font-light">
                        {settings?.building_tagline || 'A vibrant community committed to excellence and quality living.'}
                    </p>
                </div>
            </section>

            {/* Quick Stats */}
            <section className="relative z-20 -mt-16 mb-12">
                <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-6">
                        {[
                            {label: 'Residences', value: '87', icon: Home, color: 'text-primary'},
                            {label: 'Apartments', value: '70', icon: Building2, color: 'text-blue-600'},
                            {label: 'Townhouses', value: '17', icon: Home, color: 'text-green-600'},
                            {label: 'Completed', value: '2020', icon: Calendar, color: 'text-purple-600'}
                        ].map((stat, i) => (
                            <Card key={i} className="text-center shadow-xl border-none bg-white/95 backdrop-blur">
                                <CardContent className="p-6">
                                    <stat.icon className={`h-8 w-8 ${stat.color} mx-auto mb-3`}/>
                                    <p className="text-3xl font-bold text-slate-900">{stat.value}</p>
                                    <p className="text-xs text-muted-foreground uppercase tracking-widest font-bold">{stat.label}</p>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </div>
            </section>

            {/* EC Management Bar (Admins only) */}
            {canManage && (
                <div className="bg-slate-900 border-b border-white/10 py-4 sticky top-16 z-30 shadow-xl">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <Badge className="bg-primary/20 text-primary border-primary/30">Admin Mode</Badge>
                            <span className="text-white font-medium text-sm hidden md:inline">Executive Committee Management</span>
                        </div>
                        <div className="flex gap-3">
                            <Button onClick={handleTriggerAGMAlert} variant="outline" size="sm"
                                    className="bg-transparent border-white/20 text-white hover:bg-white/10 gap-2">
                                <Bell className="h-4 w-4"/> Trigger AGM Alert
                            </Button>
                            <Button onClick={() => handleOpenDialog()} size="sm" className="gap-2">
                                <Plus className="h-4 w-4"/> Add EC Member
                            </Button>
                        </div>
                    </div>
                </div>
            )}

            {/* Property Overview */}
            <section className="py-20 bg-white">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="grid lg:grid-cols-2 gap-16 items-center">
                        <div>
                            <h2 className="text-3xl font-black mb-6 uppercase tracking-tighter text-slate-900 flex items-center gap-3">
                                <Info className="text-primary h-8 w-8"/> Property Overview
                            </h2>
                            <div className="prose prose-slate lg:prose-lg">
                                {settings?.about_content ? (
                                    <div
                                        className="text-lg text-slate-600 leading-relaxed mb-6 rich-text-content"
                                        dangerouslySetInnerHTML={{__html: DOMPurify.sanitize(settings.about_content)}}
                                    />
                                ) : (
                                    <>
                                        <p className="text-lg text-slate-600 leading-relaxed mb-6">
                                            {settings?.building_name || 'This complex'} is a premium residential
                                            development.
                                            Designed for quality and lifestyle, it provides a benchmark for modern
                                            community living.
                                        </p>
                                        <p className="text-lg text-slate-600 leading-relaxed mb-6">
                                            Our community features beautifully crafted residences designed to complement
                                            the local landscape
                                            while offering residents a vibrant and secure environment.
                                        </p>
                                    </>
                                )}
                                <div className="grid sm:grid-cols-2 gap-4 mt-8">
                                    {[
                                        "Architect: Stewart Architects",
                                        "Developer: Core Developments",
                                        "Builder: Core Constructions",
                                        "Completed: June 2020"
                                    ].map((item, i) => (
                                        <div key={i} className="flex items-center gap-2 text-slate-700 font-medium">
                                            <CheckCircle2 className="text-primary h-5 w-5"/> {item}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <img src="/images/east-gate-bathroom.jpg" alt="East Gate Interior"
                                 className="rounded-2xl shadow-lg w-full h-64 object-cover mt-8"/>
                            <img src="/images/east-gate-living.jpg" alt="East Gate Living"
                                 className="rounded-2xl shadow-lg w-full h-64 object-cover"/>
                        </div>
                    </div>
                </div>
            </section>

            {/* Key Features */}
            <section className="py-24 bg-slate-50">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl font-black mb-4 uppercase tracking-tighter text-slate-900">Key Features
                            & Inclusions</h2>
                        <div className="w-20 h-1 bg-primary mx-auto"/>
                    </div>
                    <div className="grid md:grid-cols-3 gap-8">
                        {[
                            {
                                title: "Premium Finishes",
                                desc: "High-end inclusions featuring AEG appliances, induction cooktops, and reconstituted stone benchtops.",
                                features: ["AEG Appliances", "Induction Cooking", "Stone Benchtops"]
                            },
                            {
                                title: "Year-Round Comfort",
                                desc: "Designed for Canberra's climate with double glazing and under-tile heating in all bathrooms.",
                                features: ["Double Glazing", "Heated Bathroom Floors", "Ducted AC"]
                            },
                            {
                                title: "Sustainable Living",
                                desc: "Average 7.5-star EER rating across the development, minimizing environmental impact and energy costs.",
                                features: ["7.5 Star EER", "LED Lighting", "Efficient Hot Water"]
                            }
                        ].map((box, i) => (
                            <Card key={i} className="border-none shadow-lg hover:shadow-xl transition-shadow">
                                <CardHeader>
                                    <CardTitle className="text-xl font-bold">{box.title}</CardTitle>
                                    <CardDescription>{box.desc}</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <ul className="space-y-2">
                                        {box.features.map((f, j) => (
                                            <li key={j} className="flex items-center gap-2 text-sm text-slate-600">
                                                <CheckCircle2 className="h-4 w-4 text-green-500"/> {f}
                                            </li>
                                        ))}
                                    </ul>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                </div>
            </section>

            {/* Location */}
            <section className="py-24 bg-white">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="grid lg:grid-cols-2 gap-16 items-center">
                        <div className="order-2 lg:order-1 relative">
                            <div className="aspect-video bg-slate-100 rounded-3xl overflow-hidden shadow-2xl">
                                <img src="/images/east-gate-living.jpg" alt="Living Area"
                                     className="w-full h-full object-cover"/>
                            </div>
                            <div
                                className="absolute -bottom-6 -right-6 bg-primary p-8 rounded-3xl shadow-xl hidden md:block">
                                <p className="text-white font-bold text-2xl">Denman Prospect</p>
                                <p className="text-white/80">Canberra's most remarkable suburb</p>
                            </div>
                        </div>
                        <div className="order-1 lg:order-2">
                            <h2 className="text-3xl font-black mb-6 uppercase tracking-tighter text-slate-900 flex items-center gap-3">
                                <MapPin className="text-primary h-8 w-8"/> Location & Lifestyle
                            </h2>
                            <p className="text-lg text-slate-600 leading-relaxed mb-8">
                                Denman Prospect is renowned for its commitment to quality, public art, and incredible
                                views of the Molonglo Valley and National Arboretum.
                                East Gate residents enjoy immediate access to:
                            </p>
                            <ul className="space-y-4 mb-8">
                                {[
                                    "Denman Village Shops (IGA, Morning Dew Cafe, Medical Centre)",
                                    "Ridgeline Park & Playground",
                                    "Future Molonglo Town Centre",
                                    "15-minute drive to Canberra City"
                                ].map((item, i) => (
                                    <li key={i} className="flex items-start gap-3 text-slate-700">
                                        <CheckCircle2 className="text-primary h-6 w-6 shrink-0"/>
                                        <span className="font-medium">{item}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </div>
            </section>

            {/* Executive Council Section */}
            <section className="py-24 bg-slate-50" data-testid="ec-section">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl font-black mb-4 uppercase tracking-tighter text-slate-900">Executive
                            Committee</h2>
                        <div className="w-20 h-1 bg-primary mx-auto mb-6"/>
                        <p className="text-muted-foreground max-w-2xl mx-auto leading-relaxed">
                            Our Executive Committee is composed of dedicated owner volunteers elected annually.
                            They work to ensure the smooth operation and continuous improvement of East Gate.
                        </p>
                    </div>

                    {ecMembers.length > 0 ? (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-8">
                            {ecMembers.map((member) => (
                                <div key={member.id} className="group relative" data-testid={`ec-member-${member.id}`}>
                                    <div
                                        className="aspect-[4/5] bg-slate-100 rounded-2xl overflow-hidden mb-4 shadow-lg border-2 border-transparent group-hover:border-primary transition-all">
                                        {member.image ? (
                                            <img src={member.image} alt={member.name}
                                                 className="w-full h-full object-cover grayscale group-hover:grayscale-0 transition-all duration-500"/>
                                        ) : (
                                            <div
                                                className="w-full h-full flex items-center justify-center bg-slate-200">
                                                <Users className="h-20 w-20 text-slate-400"/>
                                            </div>
                                        )}
                                        {canManage && (
                                            <div
                                                className="absolute top-4 right-4 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                                <Button size="icon" variant="secondary" className="h-8 w-8 shadow-lg"
                                                        onClick={() => handleOpenDialog(member)}>
                                                    <Edit className="h-4 w-4"/>
                                                </Button>
                                                <Button size="icon" variant="destructive" className="h-8 w-8 shadow-lg"
                                                        onClick={() => handleDelete(member.id)}>
                                                    <Trash2 className="h-4 w-4"/>
                                                </Button>
                                            </div>
                                        )}
                                    </div>
                                    <div className="text-center">
                                        <h3 className="font-bold text-lg text-slate-900">{member.name}</h3>
                                        <p className="text-primary text-xs uppercase font-black tracking-widest mb-2">{member.position}</p>
                                        <div className="flex items-center justify-center gap-4 text-muted-foreground">
                                            {member.email && (
                                                <a href={`mailto:${member.email}`}
                                                   className="hover:text-primary transition-colors">
                                                    <Mail className="h-4 w-4"/>
                                                </a>
                                            )}
                                            {member.phone && (
                                                <a href={`tel:${member.phone}`}
                                                   className="hover:text-primary transition-colors">
                                                    <Phone className="h-4 w-4"/>
                                                </a>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-center py-20 bg-white rounded-3xl border-2 border-dashed border-slate-200">
                            <Users className="h-16 w-16 text-slate-300 mx-auto mb-4"/>
                            <h3 className="text-xl font-bold text-slate-900">Elections Pending</h3>
                            <p className="text-muted-foreground max-w-sm mx-auto mt-2">Committee details will be updated
                                following the next Annual General Meeting.</p>
                        </div>
                    )}
                </div>
            </section>

            {/* External Resources */}
            <section className="py-24 bg-white border-t border-slate-100">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-3xl font-black mb-12 uppercase tracking-tighter text-slate-900">Project
                        Resources</h2>
                    <div className="flex flex-wrap justify-center gap-4">
                        {[
                            {name: "Core Developments", url: "https://coredev.com.au/projects/east-gate/"},
                            {
                                name: "LJ Hooker Projects",
                                url: "https://www.ljhookerprojects.com.au/past-projects/east-gate-denman-prospect"
                            },
                            {
                                name: "OpenLot Estate Details",
                                url: "https://www.openlot.com.au/east-gate-estate-denman-prospect"
                            },
                            {name: "Construction Updates", url: "https://www.facebook.com/coredevelopments/"}
                        ].map((link, i) => (
                            <Button key={i} variant="outline" asChild className="rounded-full px-6">
                                <a href={link.url} target="_blank" rel="noopener noreferrer"
                                   className="flex items-center gap-2">
                                    {link.name} <ExternalLink className="h-4 w-4"/>
                                </a>
                            </Button>
                        ))}
                    </div>
                </div>
            </section>

            {/* Contact Section */}
            <section className="py-24 bg-slate-900 text-white">
                <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-3xl font-black mb-6 uppercase tracking-tighter">Get in Touch</h2>
                    <p className="text-slate-400 mb-10 leading-relaxed font-light">
                        Have questions about East Gate Residences? We're here to help you.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-6 justify-center">
                        {settings?.contact_email && (
                            <a href={`mailto:${settings.contact_email}`}
                               className="flex items-center justify-center gap-3 px-8 py-4 bg-white text-slate-900 rounded-full font-bold hover:bg-slate-200 transition-colors shadow-xl">
                                <Mail className="h-5 w-5"/> {settings.contact_email}
                            </a>
                        )}
                        {settings?.contact_phone && (
                            <a href={`tel:${settings.contact_phone}`}
                               className="flex items-center justify-center gap-3 px-8 py-4 border-2 border-white/20 text-white rounded-full font-bold hover:bg-white/10 transition-colors">
                                <Phone className="h-5 w-5"/> {settings.contact_phone}
                            </a>
                        )}
                    </div>
                </div>
            </section>

            {/* EC Dialog */}
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>{currentMember ? 'Edit Committee Member' : 'Add Committee Member'}</DialogTitle>
                        <DialogDescription>Enter the details of the EC member.</DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleSubmit} className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label htmlFor="name">Full Name</Label>
                            <Input id="name" value={formData.name}
                                   onChange={(e) => setFormData({...formData, name: e.target.value})} required/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="position">Position</Label>
                            <Input id="position" value={formData.position}
                                   onChange={(e) => setFormData({...formData, position: e.target.value})}
                                   placeholder="e.g., Chairperson, Secretary" required/>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="email">Email</Label>
                                <Input id="email" type="email" value={formData.email}
                                       onChange={(e) => setFormData({...formData, email: e.target.value})}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="phone">Phone</Label>
                                <Input id="phone" value={formData.phone}
                                       onChange={(e) => setFormData({...formData, phone: e.target.value})}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="image">Profile Image URL</Label>
                            <Input id="image" value={formData.image}
                                   onChange={(e) => setFormData({...formData, image: e.target.value})}
                                   placeholder="https://..."/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="bio">Brief Bio</Label>
                            <Textarea id="bio" value={formData.bio}
                                      onChange={(e) => setFormData({...formData, bio: e.target.value})}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="order">Display Order</Label>
                            <Input id="order" type="number" value={formData.order}
                                   onChange={(e) => setFormData({...formData, order: parseInt(e.target.value)})}/>
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline"
                                    onClick={() => setIsDialogOpen(false)}>Cancel</Button>
                            <Button type="submit" disabled={submitting}>
                                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                                {currentMember ? 'Update' : 'Add Member'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default AboutPage;
