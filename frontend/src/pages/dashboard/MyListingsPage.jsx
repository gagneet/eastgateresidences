import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Textarea } from '../../components/ui/textarea';
import { Label } from '../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Edit, Home, Loader2, Package, Plus, ShoppingBag, Trash2, Wrench } from 'lucide-react';
import { formatCurrency, formatDate, listingTypeNames } from '../../lib/utils';
import { toast } from 'sonner';

const listingTypeConfig = {
    for_sale: {icon: Home, color: 'bg-green-100 text-green-800'},
    for_rent: {icon: Home, color: 'bg-blue-100 text-blue-800'},
    service: {icon: Wrench, color: 'bg-purple-100 text-purple-800'},
    item_sale: {icon: Package, color: 'bg-orange-100 text-orange-800'}
};
/**
 * @generated FunctionHeader
 * Function: MyListingsPage
 * Path: frontend/src/pages/dashboard/MyListingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MyListingsPage = () => {
    const {user, api} = useAuth();
    const [listings, setListings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [editingId, setEditingId] = useState(null);
    const canNetworkScope = ['super_admin', 'strata_manager', 'owner'].includes(user?.role || '');

    const [formData, setFormData] = useState({
        title: '',
        description: '',
        listing_type: 'item_sale',
        price: '',
        contact_email: '',
        contact_phone: '',
        is_public: true,
        images: [],
        scope: 'building'
    });

    const fetchListings = useCallback(async () => {
        try {
            const response = await api.get('/listings');
            // Filter to only show user's listings
            const myListings = response.data.filter(l => l.created_by === user?.id);
            setListings(myListings);
        } catch (error) {
            console.error('Failed to fetch listings:', error);
        } finally {
            setLoading(false);
        }
    }, [api, user]);

    useEffect(() => {
        if (user) {
            fetchListings();
        }
    }, [user, fetchListings]);
    /**
     * @generated FunctionHeader
     * Function: resetForm
     * Path: frontend/src/pages/dashboard/MyListingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const resetForm = () => {
        setFormData({
            title: '',
            description: '',
            listing_type: 'item_sale',
            price: '',
            contact_email: user?.email || '',
            contact_phone: user?.phone || '',
            is_public: true,
            images: [],
            scope: 'building'
        });
        setEditingId(null);
    };
    /**
     * @generated FunctionHeader
     * Function: openCreateDialog
     * Path: frontend/src/pages/dashboard/MyListingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCreateDialog = () => {
        resetForm();
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openEditDialog
     * Path: frontend/src/pages/dashboard/MyListingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditDialog = (listing) => {
        setFormData({
            title: listing.title,
            description: listing.description,
            listing_type: listing.listing_type,
            price: listing.price?.toString() || '',
            contact_email: listing.contact_email || '',
            contact_phone: listing.contact_phone || '',
            is_public: listing.is_public,
            images: listing.images || [],
            scope: listing.scope || 'building'
        });
        setEditingId(listing.id);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/MyListingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);

        try {
            const data = {
                ...formData,
                price: formData.price ? parseFloat(formData.price) : null
            };

            if (editingId) {
                await api.put(`/listings/${editingId}`, data);
                toast.success('Listing updated successfully');
            } else {
                await api.post('/listings', data);
                toast.success('Listing created successfully');
            }

            setDialogOpen(false);
            resetForm();
            fetchListings();
        } catch (error) {
            toast.error(editingId ? 'Failed to update listing' : 'Failed to create listing');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/MyListingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (listingId) => {
        if (!confirm('Are you sure you want to delete this listing?')) return;

        try {
            await api.delete(`/listings/${listingId}`);
            toast.success('Listing deleted');
            fetchListings();
        } catch (error) {
            toast.error('Failed to delete listing');
        }
    };

    return (
        <div className="space-y-6" data-testid="my-listings-page">
            {/* Header */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">My Listings</h1>
                    <p className="text-muted-foreground">Manage your marketplace listings</p>
                </div>
                <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                    <DialogTrigger asChild>
                        <Button onClick={openCreateDialog} data-testid="create-listing-btn">
                            <Plus className="mr-2 h-4 w-4"/>
                            Create Listing
                        </Button>
                    </DialogTrigger>
                    <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
                        <DialogHeader>
                            <DialogTitle>{editingId ? 'Edit Listing' : 'Create New Listing'}</DialogTitle>
                            <DialogDescription>
                                {editingId ? 'Update your listing details' : 'Add a new item or service to the marketplace'}
                            </DialogDescription>
                        </DialogHeader>
                        <form onSubmit={handleSubmit} className="space-y-4" data-testid="listing-form">
                            <div className="space-y-2">
                                <Label htmlFor="title">Title</Label>
                                <Input
                                    id="title"
                                    value={formData.title}
                                    onChange={(e) => setFormData(prev => ( {...prev, title: e.target.value} ))}
                                    placeholder="e.g., 2BR Apartment for Rent"
                                    required
                                    data-testid="listing-title-input"
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="listing_type">Type</Label>
                                <Select
                                    value={formData.listing_type}
                                    onValueChange={(value) => setFormData(prev => ( {...prev, listing_type: value} ))}
                                >
                                    <SelectTrigger data-testid="listing-type-select">
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="for_sale">Property For Sale</SelectItem>
                                        <SelectItem value="for_rent">Property For Rent</SelectItem>
                                        <SelectItem value="item_sale">Item For Sale</SelectItem>
                                        <SelectItem value="service">Service Offered</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="description">Description</Label>
                                <Textarea
                                    id="description"
                                    value={formData.description}
                                    onChange={(e) => setFormData(prev => ( {...prev, description: e.target.value} ))}
                                    placeholder="Describe your listing..."
                                    rows={4}
                                    required
                                    data-testid="listing-description-input"
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="price">Price (AUD)</Label>
                                <Input
                                    id="price"
                                    type="number"
                                    value={formData.price}
                                    onChange={(e) => setFormData(prev => ( {...prev, price: e.target.value} ))}
                                    placeholder="Leave empty if negotiable"
                                    data-testid="listing-price-input"
                                />
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label htmlFor="contact_email">Contact Email</Label>
                                    <Input
                                        id="contact_email"
                                        type="email"
                                        value={formData.contact_email}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            contact_email: e.target.value
                                        } ))}
                                        data-testid="listing-email-input"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="contact_phone">Contact Phone</Label>
                                    <Input
                                        id="contact_phone"
                                        type="tel"
                                        value={formData.contact_phone}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            contact_phone: e.target.value
                                        } ))}
                                        data-testid="listing-phone-input"
                                    />
                                </div>
                            </div>

                            <div className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    id="is_public"
                                    checked={formData.is_public}
                                    onChange={(e) => setFormData(prev => ( {...prev, is_public: e.target.checked} ))}
                                    className="rounded border-border"
                                />
                                <Label htmlFor="is_public">
                                    Make visible to public (non-residents)
                                </Label>
                            </div>

                            {canNetworkScope && (
                                <div
                                    className="flex items-center gap-2 p-3 rounded-lg bg-slate-50 border border-slate-200">
                                    <input
                                        type="checkbox"
                                        id="scope_global"
                                        checked={formData.scope === 'global'}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            scope: e.target.checked ? 'global' : 'building'
                                        } ))}
                                        className="rounded border-border"
                                        data-testid="listing-global-scope"
                                    />
                                    <Label htmlFor="scope_global" className="text-slate-800 text-sm">
                                        Global Scope (visible in Global feed across all buildings)
                                    </Label>
                                </div>
                            )}

                            <Button type="submit" className="w-full" disabled={submitting}>
                                {submitting ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                        {editingId ? 'Updating...' : 'Creating...'}
                                    </>
                                ) : (
                                    editingId ? 'Update Listing' : 'Create Listing'
                                )}
                            </Button>
                        </form>
                    </DialogContent>
                </Dialog>
            </div>

            {/* Listings Grid */}
            {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {[1, 2, 3].map((i) => (
                        <Card key={i} className="card-dashboard">
                            <CardContent className="p-4">
                                <div className="skeleton h-32 w-full mb-4 rounded-lg"/>
                                <div className="skeleton h-5 w-3/4 mb-2"/>
                                <div className="skeleton h-4 w-full mb-4"/>
                                <div className="skeleton h-6 w-1/3"/>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : listings.length === 0 ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <ShoppingBag className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Listings Yet</h3>
                        <p className="text-muted-foreground mb-6">
                            Create your first listing to start selling or renting in the community.
                        </p>
                        <Button onClick={openCreateDialog}>
                            <Plus className="mr-2 h-4 w-4"/>
                            Create Your First Listing
                        </Button>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {listings.map((listing) => {
                        const typeConfig = listingTypeConfig[ listing.listing_type ] || listingTypeConfig.item_sale;
                        const TypeIcon = typeConfig.icon;

                        return (
                            <Card
                                key={listing.id}
                                className="card-dashboard overflow-hidden"
                                data-testid={`listing-${listing.id}`}
                            >
                                <div className="aspect-video bg-muted flex items-center justify-center">
                                    {listing.images?.[ 0 ] ? (
                                        <img
                                            src={listing.images[ 0 ]}
                                            alt={listing.title}
                                            className="w-full h-full object-cover"
                                        />
                                    ) : (
                                        <TypeIcon className="h-12 w-12 text-primary/30"/>
                                    )}
                                </div>
                                <CardContent className="p-4">
                                    <div className="flex items-center justify-between mb-2">
                                        <Badge className={typeConfig.color}>
                                            {listingTypeNames[ listing.listing_type ]}
                                        </Badge>
                                        <Badge variant={listing.status === 'active' ? 'default' : 'secondary'}>
                                            {listing.status}
                                        </Badge>
                                    </div>
                                    <h3 className="font-semibold mb-2 line-clamp-1">{listing.title}</h3>
                                    <p className="text-sm text-muted-foreground mb-3 line-clamp-2">
                                        {listing.description}
                                    </p>
                                    {listing.price && (
                                        <p className="text-lg font-bold text-primary mb-3">
                                            {formatCurrency(listing.price)}
                                        </p>
                                    )}
                                    <p className="text-xs text-muted-foreground mb-4">
                                        Posted {formatDate(listing.created_at)}
                                    </p>
                                    <div className="flex gap-2">
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="flex-1"
                                            onClick={() => openEditDialog(listing)}
                                            data-testid={`edit-${listing.id}`}
                                        >
                                            <Edit className="mr-2 h-4 w-4"/>
                                            Edit
                                        </Button>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => handleDelete(listing.id)}
                                            className="text-destructive hover:text-destructive"
                                            data-testid={`delete-${listing.id}`}
                                        >
                                            <Trash2 className="h-4 w-4"/>
                                        </Button>
                                    </div>
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default MyListingsPage;
