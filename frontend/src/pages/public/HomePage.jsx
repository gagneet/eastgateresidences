"use client";
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import axios from 'axios';
import { useAuth } from '../../contexts/AuthContext';
import { Button } from '../../components/ui/button';
import { Card, CardContent } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import {
    ArrowRight,
    Building2,
    Calendar,
    FileText,
    MessageSquare,
    Phone,
    Shield,
    ShoppingBag,
    Users
} from 'lucide-react';
import { formatDate, listingTypeNames } from '../../lib/utils';

const API_URL = `${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/api`;
// Matches the convention in AuthContext.tsx/Header.tsx/DashboardLayout.tsx — never fall
// back to selectedBuilding.id (a Postgres scheme UUID): a stale cached selectedBuilding
// from before the id/building_id split existed may have only `.id`, and sending a UUID
// as X-Building-ID silently returns zero building-scoped data (get_optional_building()
// passes the header straight into Mongo building_id matching, no UUID resolution).
const DEFAULT_BUILDING_ID = process.env.NEXT_PUBLIC_DEFAULT_BUILDING_ID || '13195';
/**
 * @generated FunctionHeader
 * Function: HomePage
 * Path: frontend/src/pages/public/HomePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const HomePage = () => {
    const router = useRouter();
    const {api, isAuthenticated, selectedBuilding} = useAuth();
    const [settings, setSettings] = useState(null);
    const [announcements, setAnnouncements] = useState([]);
    const [listings, setListings] = useState([]);
    const [blogPosts, setBlogPosts] = useState([]);
    const [ecMembers, setEcMembers] = useState([]);
    const [allBuildings, setAllBuildings] = useState([]);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchData
         * Path: frontend/src/pages/public/HomePage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchData = async () => {
            try {
                const buildingId = selectedBuilding?.building_id || DEFAULT_BUILDING_ID;
                const headers = {'X-Building-ID': buildingId};

                // Use authenticated api if available, else axios with header
                const fetcher = isAuthenticated ? api : {
get: (url, config) => axios.get(url, {...config, headers})};

                const [settingsRes, announcementsRes, listingsRes, blogRes, ecRes, buildingsRes] = await Promise.all([
                    fetcher.get(`${API_URL}/settings`),
                    fetcher.get(`${API_URL}/announcements`),
                    fetcher.get(`${API_URL}/listings`),
                    fetcher.get(`${API_URL}/blog`),
                    fetcher.get(`${API_URL}/ec-members`),
                    axios.get(`${API_URL}/auth/buildings`)
                ]);
                setSettings(settingsRes.data);
                setAllBuildings(buildingsRes.data || []);
                setAnnouncements(announcementsRes.data.filter(a => a.is_public).slice(0, 3));
                setListings(listingsRes.data.filter(l => l.is_public).slice(0, 4));
                setBlogPosts(blogRes.data.slice(0, 3));
                setEcMembers(ecRes.data.slice(0, 4));
            } catch (error) {
                console.error('Failed to fetch data:', error);
            }
        };
        fetchData();
    }, [selectedBuilding, isAuthenticated, api]);

    const fetchData = useCallback(async () => {
        try {
            const [settingsRes, announcementsRes, listingsRes, blogRes, ecRes] = await Promise.all([
                api.get('/settings'),
                api.get('/announcements'),
                api.get('/listings', {params: {scope: 'global'}}),
                api.get('/blog', {params: {scope: 'global'}}),
                api.get('/ec-members')
            ]);
            setSettings(settingsRes.data);
            setAnnouncements(( announcementsRes.data || [] ).filter(a => a.is_public).slice(0, 3));
            setListings(( listingsRes.data || [] ).slice(0, 4));
            setBlogPosts(( blogRes.data || [] ).slice(0, 3));
            setEcMembers(( ecRes.data || [] ).slice(0, 4));
        } catch (error) {
            console.error('Failed to fetch data:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData, selectedBuilding?.id]);

    const features = [
        {
            icon: MessageSquare,
            title: 'Community Communication',
            description: 'Keep everyone informed with building announcements, maintenance schedules, and owners corporation updates.',
            link: '/community/notices',
            requiresAuth: true
        },
        {
            icon: ShoppingBag,
            title: 'Resident Marketplace',
            description: 'The community hub for townhouse sales, apartment rentals, and local services like garage sales.',
            link: '/marketplace',
            requiresAuth: false
        },
        {
            icon: FileText,
            title: 'Digital Documents',
            description: 'Securely access by-laws, meeting minutes, and common property plans whenever you need them.',
            link: '/documents',
            requiresAuth: true
        },
        {
            icon: Calendar,
            title: 'Events & Meetings',
            description: 'Stay active in the community by attending upcoming committee meetings and social events.',
            link: '/community/events',
            requiresAuth: true
        },
        {
            icon: Shield,
            title: 'Privacy & Security',
            description: 'Role-based access ensures that owners and tenants can enjoy community features with complete peace of mind.',
            link: '/register',
            requiresAuth: false
        },
        {
            icon: Phone,
            title: 'Essential Services',
            description: 'Direct access to emergency contacts and professional building management services for your home.',
            link: '/emergency-services',
            requiresAuth: false
        }
    ];

    return (
        <div className="min-h-screen" data-testid="home-page">
            {/* Hero Section */}
            <section className="relative h-[80vh] min-h-[600px] flex items-center" data-testid="hero-section">
                <div
                    className="absolute inset-0 bg-cover bg-center"
                    style={{
                        backgroundImage: `url(${settings?.hero_image || '/images/east_gate_residences.jpg'}?v=1)`
                    }}
                />
                <div className="absolute inset-0 hero-gradient"/>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20">
                    <div className="max-w-2xl animate-fade-in">
                        <Badge className="mb-4 bg-secondary/20 text-secondary-foreground border-secondary/30">
                            Welcome to Our Community
                        </Badge>
                        <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-white mb-6 leading-tight">
                            {settings?.building_name || 'East Gate Residences'}
                        </h1>
                        <p className="text-lg sm:text-xl text-white/90 mb-8 leading-relaxed">
                            {settings?.building_description || 'A modern residential community where neighbors become friends. Experience premium living with world-class amenities and a vibrant community spirit.'}
                        </p>
                        <div className="flex flex-col sm:flex-row gap-4">
                            <Button
                                size="lg"
                                className="btn-secondary text-lg"
                                onClick={() => router.push('/register')}
                                data-testid="hero-join-btn"
                            >
                                Join Our Community
                                <ArrowRight className="ml-2 h-5 w-5"/>
                            </Button>
                            <Button
                                size="lg"
                                variant="outline"
                                className="border-white text-white bg-transparent hover:bg-white/10 hover:text-white text-lg"
                                onClick={() => router.push('/about')}
                                data-testid="hero-learn-btn"
                            >
                                Learn More
                            </Button>
                        </div>
                    </div>
                </div>
            </section>

            {/* Features Section */}
            <section className="py-20 bg-muted/30" data-testid="features-section">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center mb-16">
                        <h2 className="text-3xl sm:text-4xl font-bold mb-4">Everything You Need</h2>
                        <p className="text-muted-foreground text-lg max-w-2xl mx-auto">
                            Our comprehensive strata management platform brings your community together with powerful
                            tools and features.
                        </p>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                        {features.map((feature, index) => (
                            <Link
                                key={index}
                                href={feature.link}
                                className="block"
                                data-testid={`feature-card-${index}`}
                            >
                                <Card className="card-marketing group cursor-pointer h-full">
                                    <CardContent className="p-8">
                                        <div
                                            className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center mb-6 group-hover:bg-primary/20 transition-colors">
                                            <feature.icon className="h-7 w-7 text-primary"/>
                                        </div>
                                        <h3 className="text-xl font-semibold mb-3">{feature.title}</h3>
                                        <p className="text-muted-foreground">{feature.description}</p>
                                        <div
                                            className="mt-4 flex items-center text-primary font-medium opacity-0 group-hover:opacity-100 transition-opacity">
                                            Learn More <ArrowRight className="ml-1 h-4 w-4"/>
                                        </div>
                                    </CardContent>
                                </Card>
                            </Link>
                        ))}
                    </div>
                </div>
            </section>

            {/* Announcements Section */}
            {announcements.length > 0 && (
                <section className="py-20" data-testid="announcements-section">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="flex items-center justify-between mb-12">
                            <div>
                                <h2 className="text-3xl font-bold mb-2">Latest Announcements</h2>
                                <p className="text-muted-foreground">Stay updated with community news</p>
                            </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            {announcements.map((announcement) => (
                                <Card key={announcement.id}
                                      className="card-dashboard hover:shadow-lg transition-shadow">
                                    <CardContent className="p-6">
                                        <Badge
                                            className={`mb-3 ${
                                                announcement.priority === 'urgent' ? 'bg-red-100 text-red-800' :
                                                    announcement.priority === 'high' ? 'bg-orange-100 text-orange-800' :
                                                        'bg-blue-100 text-blue-800'
                                            }`}
                                        >
                                            {announcement.priority}
                                        </Badge>
                                        <h3 className="font-semibold mb-2">{announcement.title}</h3>
                                        <p className="text-sm text-muted-foreground line-clamp-3">{announcement.content}</p>
                                        <p className="text-xs text-muted-foreground mt-4">{formatDate(announcement.created_at)}</p>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    </div>
                </section>
            )}

            {/* Marketplace Preview */}
            {listings.length > 0 && (
                <section className="py-20 bg-muted/30" data-testid="marketplace-section">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="flex items-center justify-between mb-12">
                            <div>
                                <h2 className="text-3xl font-bold mb-2">Community Marketplace</h2>
                                <p className="text-muted-foreground">Find great deals within our community</p>
                            </div>
                            <Button variant="outline" onClick={() => router.push('/marketplace')}
                                    data-testid="view-marketplace-btn">
                                View All
                                <ArrowRight className="ml-2 h-4 w-4"/>
                            </Button>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                            {listings.map((listing) => (
                                <Card key={listing.id} className="card-marketing overflow-hidden">
                                    {listing.images?.[ 0 ] && (
                                        <div className="aspect-video bg-muted">
                                            <img
                                                src={listing.images[ 0 ]}
                                                alt={listing.title}
                                                className="w-full h-full object-cover"
                                            />
                                        </div>
                                    )}
                                    <CardContent className="p-4">
                                        <Badge className={`mb-2 listing-${listing.listing_type.replace('_', '-')}`}>
                                            {listingTypeNames[ listing.listing_type ]}
                                        </Badge>
                                        <h3 className="font-semibold mb-1 line-clamp-1">{listing.title}</h3>
                                        {listing.price && (
                                            <p className="text-primary font-bold">${listing.price.toLocaleString()}</p>
                                        )}
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    </div>
                </section>
            )}

            {/* EC Members Preview */}
            {ecMembers.length > 0 && (
                <section className="py-20" data-testid="ec-members-section">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="text-center mb-12">
                            <h2 className="text-3xl font-bold mb-2">Meet Your Executive Council</h2>
                            <p className="text-muted-foreground">Dedicated volunteers serving our community</p>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
                            {ecMembers.map((member) => (
                                <Card key={member.id} className="card-marketing text-center">
                                    <CardContent className="p-6">
                                        <div className="w-24 h-24 mx-auto mb-4 rounded-full overflow-hidden bg-muted">
                                            {member.image ? (
                                                <img src={member.image} alt={member.name}
                                                     className="w-full h-full object-cover"/>
                                            ) : (
                                                <div
                                                    className="w-full h-full flex items-center justify-center bg-primary/10">
                                                    <Users className="h-10 w-10 text-primary"/>
                                                </div>
                                            )}
                                        </div>
                                        <h3 className="font-semibold">{member.name}</h3>
                                        <p className="text-sm text-primary">{member.position}</p>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>

                        <div className="text-center mt-8">
                            <Button variant="outline" onClick={() => router.push('/about')} data-testid="meet-team-btn">
                                Meet the Full Team
                                <ArrowRight className="ml-2 h-4 w-4"/>
                            </Button>
                        </div>
                    </div>
                </section>
            )}

            {/* Regional Support Section */}
            <section className="py-16 bg-white border-t border-border">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-2xl font-bold mb-8">Supporting Premier Owners Corporations</h2>

                    {allBuildings.length > 0 && (
                        <div className="mb-12">
                            <p className="text-sm uppercase tracking-widest text-muted-foreground font-bold mb-6">Our
                                Growing Network</p>
                            <div className="flex flex-wrap justify-center gap-4">
                                {allBuildings.map(b => (
                                    <Badge key={b.id} variant="outline" className="px-4 py-2 text-sm bg-muted/30">
                                        <Building2 className="mr-2 h-3 w-3 text-primary"/>
                                        {b.name}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    )}

                    <div
                        className="flex flex-wrap justify-center gap-x-8 gap-y-4 text-sm text-muted-foreground font-medium">
                        <span>{settings?.building_name || 'East Gate Residences'}{settings?.building_address ? `, ${settings.building_address}` : ''}</span>
                        <span className="text-slate-300">•</span>
                        <span>Supporting Communities Australia-wide</span>
                    </div>
                    <p className="mt-8 text-muted-foreground max-w-3xl mx-auto">
                        This platform provides premium strata management and owners corporation visibility for
                        residential communities.
                        Designed to scale across Australia and New Zealand, we ensure transparency, fairness, and
                        community spirit for
                        townhouse, apartment, and mixed-use complexes.
                    </p>
                </div>
            </section>

            {/* CTA Section */}
            <section className="py-20 bg-primary" data-testid="cta-section">
                <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <h2 className="text-3xl sm:text-4xl font-bold text-white mb-6">
                        Ready to Join Our Community?
                    </h2>
                    <p className="text-xl text-white/90 mb-8">
                        Register today to access all resident features, connect with neighbors, and stay informed about
                        community matters.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-4 justify-center">
                        <Button
                            size="lg"
                            className="bg-white text-primary hover:bg-white/90"
                            onClick={() => router.push('/register')}
                            data-testid="cta-register-btn"
                        >
                            Create Account
                        </Button>
                        <Button
                            size="lg"
                            variant="outline"
                            className="border-white text-white bg-transparent hover:bg-white/10 hover:text-white"
                            onClick={() => router.push('/login')}
                            data-testid="cta-login-btn"
                        >
                            Sign In
                        </Button>
                    </div>
                </div>
            </section>
        </div>
    );
};

export default HomePage;
