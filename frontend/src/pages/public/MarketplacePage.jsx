// @featuretrace:marketplace — Public marketplace page: community listings (for_sale, for_rent, item_sale, service).
// Layer: frontend
// Data flow: this page → GET /api/listings?scope={building|global} → db.listings (building-scoped).
// Scope toggle: authenticated users see "This Building" / "Global Feed" switch.
// Building identity: X-Building-ID header injected by AuthContext api interceptor.
// Related: backend/server.py #marketplace-routes (GET/POST/PUT/DELETE /listings)
//           backend/cron/cron_property_scraper.py (auto-populates property category listings)
//           backend/server.py POST /listings/scrape (manual scraper trigger)
//           frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx (scraper management UI)
"use client";
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { Card, CardContent } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../components/ui/dialog';
import {
    AlertCircle,
    CheckCircle2,
    DollarSign,
    Home,
    Info,
    Mail,
    MapPin,
    Package,
    Pencil,
    Phone,
    Search,
    ShoppingBag,
    Tag,
    TrendingUp,
    User,
    Wrench,
    X
} from 'lucide-react';
import { formatCurrency, listingTypeNames } from '../../lib/utils';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';

const API_URL = `${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8003'}/api`;

const listingTypeConfig = {
    for_sale: {icon: Home, color: 'bg-green-100 text-green-800'},
    for_rent: {icon: Home, color: 'bg-blue-100 text-blue-800'},
    service: {icon: Wrench, color: 'bg-purple-100 text-purple-800'},
    item_sale: {icon: Package, color: 'bg-orange-100 text-orange-800'}
};
/**
 * @generated FunctionHeader
 * Function: formatEstimate
 * Path: frontend/src/pages/public/MarketplacePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatEstimate(price) {
    if (!price) return null;
    if (price >= 1_000_000) return `$${( price / 1_000_000 ).toFixed(2).replace(/\.?0+$/, '')}M`;
    if (price >= 1_000) return `$${Math.round(price / 1_000)}K`;
    return `$${price.toLocaleString()}`;
}
/**
 * @generated FunctionHeader
 * Function: MarketplacePage
 * Path: frontend/src/pages/public/MarketplacePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MarketplacePage = () => {
    const {isAuthenticated, api, selectedBuilding} = useAuth();
    // The personal market estimate belongs to a unit, so it follows the active one.
    const {activeUnit} = useActiveUnit();
    const router = useRouter();
    const searchParams = useSearchParams();
    const [listings, setListings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [selectedType, setSelectedType] = useState('all');
    const [scope, setScope] = useState('global');

    // Sync selectedType with query param if present (e.g. from footer links)
    const locationFilter = searchParams.get('location');

    // Market estimate state — only used when authenticated with a unit
    const [estimatedPrice, setEstimatedPrice] = useState(null);
    const [priceLastUpdated, setPriceLastUpdated] = useState(null);
    const [priceNotes, setPriceNotes] = useState('');
    const [marketData, setMarketData] = useState(null);
    const [marketValuePopupOpen, setMarketValuePopupOpen] = useState(false);
    const [priceInput, setPriceInput] = useState('');
    const [notesInput, setNotesInput] = useState('');
    const [savingPrice, setSavingPrice] = useState(false);

    const fetchListings = useCallback(async () => {
        setLoading(true);
        try {
            const response = await api.get('/listings', {
                params: {scope}
            });
            setListings(response.data);
        } catch (error) {
            console.error('Failed to fetch listings:', error);
        } finally {
            setLoading(false);
        }
    }, [api, scope]);

    useEffect(() => {
        fetchListings();
    }, [fetchListings]);

    // Fetch saved estimate + market pulse when authenticated with a unit
    useEffect(() => {
        if (!isAuthenticated || !activeUnit || !api) return;
        api.get(`/units/${activeUnit}/market-valuation`)
            .then((res) => {
                if (res.data?.estimated_market_price) {
                    setEstimatedPrice(res.data.estimated_market_price);
                    setPriceLastUpdated(res.data.market_price_updated_at ?? null);
                    setPriceNotes(res.data.market_price_notes ?? '');
                }
            })
            .catch(() => {
            });
        api.get('/market/pulse')
            .then((res) => setMarketData(res.data))
            .catch(() => {
            });
    }, [isAuthenticated, activeUnit, api]);

    const openEstimateDialog = useCallback(() => {
        setPriceInput(estimatedPrice ? String(Math.round(estimatedPrice)) : '');
        setNotesInput(priceNotes);
        setMarketValuePopupOpen(true);
    }, [estimatedPrice, priceNotes]);

    const saveEstimate = useCallback(async () => {
        const price = Number(priceInput.replace(/[^0-9.]/g, ''));
        if (!price || price <= 0) return;
        setSavingPrice(true);
        try {
            const res = await api.put(`/units/${activeUnit}/market-valuation`, {
                estimated_market_price: price,
                market_price_notes: notesInput,
            });
            setEstimatedPrice(res.data.estimated_market_price);
            setPriceLastUpdated(res.data.market_price_updated_at);
            setPriceNotes(res.data.market_price_notes ?? '');
            setMarketValuePopupOpen(false);
        } catch {
            /* error toast handled by axios interceptor */
        } finally {
            setSavingPrice(false);
        }
    }, [api, activeUnit, priceInput, notesInput]);

    const types = [
        {id: 'all', label: 'All'},
        {id: 'for_sale', label: 'For Sale'},
        {id: 'for_rent', label: 'For Rent'},
        {id: 'item_sale', label: 'Items'},
        {id: 'service', label: 'Services'}
    ];

    const filteredListings = useMemo(() => {
        return listings.filter(listing => {
            const searchLower = searchTerm.toLowerCase();

            // Precise location matching (whole word boundary)
            const locationMatch = locationFilter ? (
                new RegExp(`\\b${locationFilter}\\b`, 'i').test(listing.title || '') ||
                new RegExp(`\\b${locationFilter}\\b`, 'i').test(listing.description || '')
            ) : false;

            const matchesSearch = ( listing.title || '' ).toLowerCase().includes(searchLower) ||
                listing.description?.toLowerCase().includes(searchLower) ||
                locationMatch;

            const matchesType = selectedType === 'all' || listing.listing_type === selectedType;

            return matchesSearch && matchesType;
        });
    }, [listings, searchTerm, selectedType, locationFilter]);

    const hasUnit = isAuthenticated && !!activeUnit;

    return (
        <div className="min-h-screen" data-testid="marketplace-page">
            {/* Hero Section */}
            <section className="py-16 bg-primary">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center text-white">
                        <Badge className="mb-4 bg-white/20 text-white border-white/30">Marketplace</Badge>
                        <h1 className="text-4xl sm:text-5xl font-bold mb-4">
                            {scope === 'global' ? 'Global Community Marketplace' : `${selectedBuilding?.name || 'Community'} Marketplace`}
                        </h1>
                        <p className="text-xl text-white/90 max-w-2xl mx-auto mb-8">
                            Discover townhouse sales, apartment rentals, and professional strata services.
                            Your local hub for garage sales, garden sales, and unit listings.
                        </p>

                        {isAuthenticated && (
                            <div className="flex justify-center gap-2 mb-8">
                                <Button
                                    variant={scope === 'building' ? 'secondary' : 'outline'}
                                    size="sm"
                                    onClick={() => setScope('building')}
                                    className={scope === 'building' ? 'bg-white text-primary' : 'bg-transparent text-white border-white/30 hover:bg-white/10 hover:text-white'}
                                >
                                    This Building
                                </Button>
                                <Button
                                    variant={scope === 'global' ? 'secondary' : 'outline'}
                                    size="sm"
                                    onClick={() => setScope('global')}
                                    className={scope === 'global' ? 'bg-white text-primary' : 'bg-transparent text-white border-white/30 hover:bg-white/10 hover:text-white'}
                                >
                                    Global Feed
                                </Button>
                            </div>
                        )}
                        {isAuthenticated ? (
                            <Button
                                className="bg-white text-primary hover:bg-white/90"
                                onClick={() => router.push('/community/my-listings')}
                                data-testid="create-listing-btn"
                            >
                                <ShoppingBag className="mr-2 h-5 w-5"/>
                                Post a Listing
                            </Button>
                        ) : (
                            <Button
                                className="bg-white text-primary hover:bg-white/90"
                                onClick={() => router.push('/login')}
                                data-testid="login-to-list-btn"
                            >
                                Sign in to Post
                            </Button>
                        )}
                    </div>
                </div>
            </section>

            {/* Search and Filter */}
            <section className="py-8 border-b border-border bg-card">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col sm:flex-row gap-4 items-center">
                        <div className="relative flex-1 w-full">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                placeholder="Search listings..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-10 pr-10"
                                data-testid="marketplace-search"
                            />
                            {searchTerm && (
                                <button
                                    type="button"
                                    onClick={() => setSearchTerm('')}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                                    aria-label="Clear search"
                                >
                                    <X className="h-4 w-4"/>
                                </button>
                            )}
                        </div>
                        <div className="pill-nav flex-wrap justify-center">
                            {types.map((type) => (
                                <button
                                    key={type.id}
                                    onClick={() => setSelectedType(type.id)}
                                    className={`pill-nav-item ${selectedType === type.id ? 'active' : ''}`}
                                    data-testid={`filter-${type.id}`}
                                >
                                    {type.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            </section>

            {/* ── My Unit Estimate Banner ── (authenticated unit-holders only) */}
            {hasUnit && (
                <section className="py-4 bg-card border-b border-border/50">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <button
                            onClick={openEstimateDialog}
                            className="w-full text-left rounded-2xl bg-gradient-to-r from-indigo-600 to-violet-700 text-white px-6 py-4 flex items-center justify-between shadow-lg hover:shadow-indigo-200 hover:shadow-xl transition-all duration-200 active:scale-[0.995]"
                            aria-label="Set or update your unit market estimate"
                        >
                            <div className="flex items-center gap-4">
                                <div className="p-2.5 bg-white/20 rounded-xl shrink-0">
                                    <Home className="w-5 h-5 text-white"/>
                                </div>
                                <div>
                                    <p className="text-[10px] font-bold text-indigo-200 uppercase tracking-widest mb-0.5">
                                        Unit {activeUnit} — My Market Estimate
                                    </p>
                                    {estimatedPrice ? (
                                        <p className="text-xl font-black leading-none">{formatEstimate(estimatedPrice)}</p>
                                    ) : (
                                        <p className="text-sm font-bold text-white/80">Set your estimated market
                                            value</p>
                                    )}
                                </div>
                            </div>
                            <div className="flex items-center gap-3 shrink-0">
                                {priceLastUpdated && (
                                    <p className="text-[10px] text-indigo-300 hidden sm:block">
                                        Updated {new Date(priceLastUpdated).toLocaleDateString('en-AU', {
                                        day: 'numeric',
                                        month: 'short',
                                        year: 'numeric'
                                    })}
                                    </p>
                                )}
                                {!estimatedPrice && (
                                    <div
                                        className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 bg-white/15 rounded-lg border border-dashed border-white/30">
                                        <Tag className="w-3.5 h-3.5 text-indigo-300"/>
                                        <span className="text-[11px] font-bold text-indigo-200">Not set</span>
                                    </div>
                                )}
                                <div
                                    className="p-2 bg-white/15 rounded-lg border border-white/20 hover:bg-white/25 transition-colors">
                                    <Pencil className="w-4 h-4 text-white"/>
                                </div>
                            </div>
                        </button>
                    </div>
                </section>
            )}

            {/* Listings Grid */}
            <section className="py-12" data-testid="listings-grid">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    {loading ? (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                            {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                                <Card key={i} className="card-marketing">
                                    <div className="aspect-video skeleton"/>
                                    <CardContent className="p-4">
                                        <div className="skeleton h-5 w-20 mb-3"/>
                                        <div className="skeleton h-6 w-3/4 mb-2"/>
                                        <div className="skeleton h-4 w-full mb-4"/>
                                        <div className="skeleton h-6 w-1/3"/>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    ) : filteredListings.length === 0 ? (
                        <Card className="card-dashboard">
                            <CardContent className="py-16 text-center">
                                <ShoppingBag className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                                <h3 className="text-lg font-medium mb-2">No Listings Found</h3>
                                <p className="text-muted-foreground mb-6">
                                    {listings.length === 0
                                        ? 'Be the first to post a listing in the marketplace!'
                                        : 'Try adjusting your search or filter criteria.'}
                                </p>
                                {isAuthenticated && (
                                    <Button onClick={() => router.push('/community/my-listings')}>
                                        Post a Listing
                                    </Button>
                                )}
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                            {filteredListings.map((listing) => {
                                const typeConfig = listingTypeConfig[ listing.listing_type ] || listingTypeConfig.item_sale;
                                const TypeIcon = typeConfig.icon;

                                return (
                                    <Card
                                        key={listing.id}
                                        className="card-marketing cursor-pointer group"
                                        onClick={() => router.push(`/marketplace/${listing.id}`)}
                                        data-testid={`listing-${listing.id}`}
                                    >
                                        <div className="aspect-video bg-muted overflow-hidden">
                                            {listing.images?.[ 0 ] ? (
                                                <img
                                                    src={listing.images[ 0 ]}
                                                    alt={listing.title}
                                                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                                />
                                            ) : (
                                                <div
                                                    className="w-full h-full flex items-center justify-center bg-primary/5">
                                                    <TypeIcon className="h-12 w-12 text-primary/30"/>
                                                </div>
                                            )}
                                        </div>
                                        <CardContent className="p-4">
                                            <Badge className={`mb-3 ${typeConfig.color}`}>
                                                {listingTypeNames[ listing.listing_type ]}
                                            </Badge>
                                            <h3 className="font-semibold mb-2 line-clamp-1 group-hover:text-primary transition-colors">
                                                {listing.title}
                                            </h3>
                                            <p className="text-sm text-muted-foreground mb-3 line-clamp-2">
                                                {listing.description}
                                            </p>
                                            {listing.price && (
                                                <p className="text-lg font-bold text-primary">
                                                    {formatCurrency(listing.price)}
                                                </p>
                                            )}
                                            <p className="text-xs text-muted-foreground mt-2">
                                                Posted by {listing.created_by_name}
                                            </p>
                                        </CardContent>
                                    </Card>
                                );
                            })}
                        </div>
                    )}
                </div>
            </section>

            {/* ── My Unit Market Estimate Dialog ── */}
            {hasUnit && (
                <Dialog open={marketValuePopupOpen} onOpenChange={setMarketValuePopupOpen}>
                    <DialogContent className="sm:max-w-lg rounded-3xl p-0 overflow-hidden">
                        {/* Gradient header */}
                        <div className="bg-gradient-to-br from-indigo-600 to-violet-700 px-7 pt-7 pb-6 text-white">
                            <div className="flex items-center gap-3 mb-4">
                                <div className="p-3 bg-white/20 rounded-2xl">
                                    <Home className="w-5 h-5 text-white"/>
                                </div>
                                <div>
                                    <DialogTitle className="text-lg font-black text-white">My Unit
                                        Estimate</DialogTitle>
                                    <DialogDescription className="text-indigo-200 text-xs">
                                        Unit {activeUnit} — set your personal market value estimate
                                    </DialogDescription>
                                </div>
                            </div>

                            {/* Market context chips */}
                            <div className="grid grid-cols-2 gap-2 text-xs">
                                {marketData?.unit_median && (
                                    <div className="bg-white/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                        <MapPin className="w-3 h-3 text-indigo-200 shrink-0"/>
                                        <div>
                                            <p className="text-[9px] font-bold text-indigo-200 uppercase tracking-widest">Suburb
                                                Median</p>
                                            <p className="font-black text-white">{marketData.unit_median}</p>
                                        </div>
                                    </div>
                                )}
                                {marketData?.growth_12m && (
                                    <div className="bg-white/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                        <TrendingUp className="w-3 h-3 text-emerald-300 shrink-0"/>
                                        <div>
                                            <p className="text-[9px] font-bold text-indigo-200 uppercase tracking-widest">12-Mo
                                                Growth</p>
                                            <p className="font-black text-emerald-300">{marketData.growth_12m}</p>
                                        </div>
                                    </div>
                                )}
                                {marketData?.rental_yield && (
                                    <div className="bg-white/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                        <DollarSign className="w-3 h-3 text-amber-300 shrink-0"/>
                                        <div>
                                            <p className="text-[9px] font-bold text-indigo-200 uppercase tracking-widest">Rental
                                                Yield</p>
                                            <p className="font-black text-amber-300">{marketData.rental_yield}</p>
                                        </div>
                                    </div>
                                )}
                                {marketData?.last_sale?.price && (
                                    <div className="bg-white/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                        <CheckCircle2 className="w-3 h-3 text-emerald-300 shrink-0"/>
                                        <div>
                                            <p className="text-[9px] font-bold text-indigo-200 uppercase tracking-widest">Last
                                                Sale</p>
                                            <p className="font-black text-white text-[11px]">{marketData.last_sale.price}</p>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="px-7 py-5 space-y-5">
                            {/* Pricing tips */}
                            <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4">
                                <div className="flex items-center gap-2 mb-2">
                                    <Info className="w-3.5 h-3.5 text-amber-600 shrink-0"/>
                                    <p className="text-[10px] font-black text-amber-700 uppercase tracking-widest">Pricing
                                        Tips</p>
                                </div>
                                <ul className="space-y-1 text-[11px] text-amber-800">
                                    <li className="flex items-start gap-1.5"><span
                                        className="text-emerald-600 font-bold mt-px">+</span><span>Higher floor, north-facing aspect, or building views</span>
                                    </li>
                                    <li className="flex items-start gap-1.5"><span
                                        className="text-emerald-600 font-bold mt-px">+</span><span>2+ car spaces, large balcony, recent kitchen/bath reno</span>
                                    </li>
                                    <li className="flex items-start gap-1.5"><span
                                        className="text-emerald-600 font-bold mt-px">+</span><span>Strong building fund health — buyers pay a premium</span>
                                    </li>
                                    <li className="flex items-start gap-1.5"><span
                                        className="text-rose-500 font-bold mt-px">−</span><span>Ground floor, west-facing, single or no car space</span>
                                    </li>
                                    <li className="flex items-start gap-1.5"><span
                                        className="text-rose-500 font-bold mt-px">−</span><span>Original fit-out, busy road exposure, smaller floorplan</span>
                                    </li>
                                </ul>
                            </div>

                            {/* Price input */}
                            <div className="space-y-1.5">
                                <label
                                    className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center gap-1.5">
                                    <DollarSign className="w-3 h-3"/>
                                    Your Estimated Market Price (AUD)
                                </label>
                                <div className="relative">
                                    <span
                                        className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 font-bold text-sm">$</span>
                                    <Input
                                        type="text"
                                        inputMode="numeric"
                                        placeholder="e.g. 850000"
                                        value={priceInput}
                                        onChange={(e) => setPriceInput(e.target.value.replace(/[^0-9]/g, ''))}
                                        className="pl-7 rounded-xl font-bold text-slate-800 border-slate-200 focus:border-indigo-400"
                                    />
                                </div>
                                {priceInput && Number(priceInput) > 0 && (
                                    <p className="text-[11px] text-indigo-600 font-bold">
                                        = {Number(priceInput) >= 1_000_000
                                        ? `$${( Number(priceInput) / 1_000_000 ).toFixed(2).replace(/\.?0+$/, '')}M`
                                        : `$${Math.round(Number(priceInput) / 1_000)}K`}
                                    </p>
                                )}
                            </div>

                            {/* Notes */}
                            <div className="space-y-1.5">
                                <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest">
                                    Notes (optional)
                                </label>
                                <textarea
                                    placeholder="e.g. Based on recent comparable sales, pending renovation..."
                                    value={notesInput}
                                    onChange={(e) => setNotesInput(e.target.value)}
                                    rows={2}
                                    maxLength={500}
                                    className="w-full rounded-xl border border-slate-200 focus:border-indigo-400 focus:outline-none px-3.5 py-2.5 text-sm text-slate-700 resize-none"
                                />
                            </div>

                            {/* Disclaimer */}
                            <div className="flex gap-2 text-[10px] text-slate-400 leading-relaxed">
                                <AlertCircle className="w-3 h-3 shrink-0 mt-px"/>
                                <span>This is a personal estimate only — not an official valuation. Consult a licensed agent for a formal appraisal.</span>
                            </div>

                            {/* Actions */}
                            <div className="flex gap-3 pt-1">
                                <Button
                                    variant="outline"
                                    className="flex-1 rounded-2xl border-slate-200 text-slate-600 font-bold"
                                    onClick={() => setMarketValuePopupOpen(false)}
                                >
                                    Cancel
                                </Button>
                                <Button
                                    className="flex-1 rounded-2xl bg-indigo-600 hover:bg-indigo-700 text-white font-black shadow-lg"
                                    disabled={!priceInput || Number(priceInput) <= 0 || savingPrice}
                                    onClick={saveEstimate}
                                >
                                    {savingPrice ? 'Saving…' : 'Save Estimate'}
                                </Button>
                            </div>
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: ListingDetailPage
 * Path: frontend/src/pages/public/MarketplacePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ListingDetailPage = () => {
    const {api} = useAuth();
    const {listingId} = useParams();
    const router = useRouter();
    const [listing, setListing] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchListing
         * Path: frontend/src/pages/public/MarketplacePage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchListing = async () => {
            try {
                const response = await api.get(`/listings/${listingId}`);
                setListing(response.data);
            } catch (error) {
                console.error('Failed to fetch listing:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchListing();
    }, [api, listingId]);

    if (loading) {
        return (
            <div className="min-h-screen py-12">
                <div className="max-w-4xl mx-auto px-4">
                    <div className="skeleton h-8 w-32 mb-8"/>
                    <div className="skeleton h-96 w-full mb-8 rounded-xl"/>
                    <div className="skeleton h-8 w-3/4 mb-4"/>
                    <div className="skeleton h-4 w-full mb-2"/>
                    <div className="skeleton h-4 w-2/3"/>
                </div>
            </div>
        );
    }

    if (!listing) {
        return (
            <div className="min-h-screen py-12">
                <div className="max-w-4xl mx-auto px-4 text-center">
                    <h1 className="text-2xl font-bold mb-4">Listing Not Found</h1>
                    <Button onClick={() => router.push('/marketplace')}>Back to Marketplace</Button>
                </div>
            </div>
        );
    }

    const typeConfig = listingTypeConfig[ listing.listing_type ] || listingTypeConfig.item_sale;

    return (
        <div className="min-h-screen" data-testid="listing-detail">
            <div className="max-w-6xl mx-auto px-4 py-12">
                <Button variant="ghost" className="mb-8" onClick={() => router.push('/marketplace')}>
                    ← Back to Marketplace
                </Button>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                    {/* Images */}
                    <div className="lg:col-span-2">
                        <div className="aspect-video bg-muted rounded-2xl overflow-hidden mb-4">
                            {listing.images?.[ 0 ] ? (
                                <img
                                    src={listing.images[ 0 ]}
                                    alt={listing.title}
                                    className="w-full h-full object-cover"
                                />
                            ) : (
                                <div className="w-full h-full flex items-center justify-center bg-primary/5">
                                    <ShoppingBag className="h-16 w-16 text-primary/30"/>
                                </div>
                            )}
                        </div>

                        {listing.images?.length > 1 && (
                            <div className="grid grid-cols-4 gap-2">
                                {listing.images.slice(1).map((image, index) => (
                                    <div key={index} className="aspect-square bg-muted rounded-lg overflow-hidden">
                                        <img src={image} alt="" className="w-full h-full object-cover"/>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Details */}
                    <div>
                        <Badge className={`mb-4 ${typeConfig.color}`}>
                            {listingTypeNames[ listing.listing_type ]}
                        </Badge>

                        <h1 className="text-2xl font-bold mb-4">{listing.title}</h1>

                        {listing.price && (
                            <p className="text-3xl font-bold text-primary mb-6">
                                {formatCurrency(listing.price)}
                            </p>
                        )}

                        <div className="prose max-w-none mb-8">
                            <p className="whitespace-pre-wrap">{listing.description}</p>
                        </div>

                        <Card className="card-dashboard">
                            <CardContent className="p-6">
                                <h3 className="font-semibold mb-4">Contact Seller</h3>
                                <div className="space-y-3">
                                    <div className="flex items-center gap-3">
                                        <User className="h-5 w-5 text-muted-foreground"/>
                                        <span>{listing.created_by_name}</span>
                                    </div>
                                    {listing.contact_email && (
                                        <a
                                            href={`mailto:${listing.contact_email}`}
                                            className="flex items-center gap-3 text-primary hover:underline"
                                        >
                                            <Mail className="h-5 w-5"/>
                                            {listing.contact_email}
                                        </a>
                                    )}
                                    {listing.contact_phone && (
                                        <a
                                            href={`tel:${listing.contact_phone}`}
                                            className="flex items-center gap-3 text-primary hover:underline"
                                        >
                                            <Phone className="h-5 w-5"/>
                                            {listing.contact_phone}
                                        </a>
                                    )}
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </div>
            </div>
        </div>
    );
};

// Need to export for route params
export { useParams } from 'next/navigation';

export default MarketplacePage;
