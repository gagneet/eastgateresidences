// @featuretrace:news — Admin blog management: create, edit, delete articles; trigger news scraper.
// Layer: frontend
// Data flow: this page → POST/PUT/DELETE /api/blog → db.blog_posts (building-scoped).
// Scrape trigger: POST /api/blog/scrape → cron_news_scraper.py subprocess → db.blog_posts.
// Related: backend/server.py #news-routes
//           backend/cron/cron_news_scraper.py (news scraper, writes building_id on every article)
//           frontend/src/pages/public/BlogPage.jsx (public reader view)
//           frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx (schedule + settings)
import React, { useCallback, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Switch } from '../../components/ui/switch';
import { Badge } from '../../components/ui/badge';
import { Clock, Edit, Eye, FileText, Loader2, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../components/ui/dialog';
import { toast } from 'sonner';
import { formatDate } from '../../lib/utils';
import { useApiData } from '../../hooks/useApiData';
import { useFormData } from '../../hooks/useFormData';
/**
 * @generated FunctionHeader
 * Function: BlogManagementPage
 * Path: frontend/src/pages/dashboard/BlogManagementPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BlogManagementPage = () => {
    const {api, user} = useAuth();
    const [searchQuery, setSearchQuery] = useState('');
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isDeleting, setIsDeleting] = useState(false);
    const [currentPost, setCurrentPost] = useState(null);
    const [scraping, setScraping] = useState(false);

    // Use custom hooks to reduce duplication
    const {
        data: posts,
        loading: postsLoading,
        refetch: refetchPosts,
        setData: setPosts
    } = useApiData(
        useCallback(() => api.get('/blog?include_drafts=true'), [api])
    );

    const {
        formData,
        setFormData,
        handleChange,
        handleSubmit,
        resetForm,
        isSubmitting,
    } = useFormData({
        title: '',
        content: '',
        excerpt: '',
        cover_image: '',
        tags: '',
        is_published: true,
        is_public: true,
        scope: 'building'
    });
    /**
     * @generated FunctionHeader
     * Function: handleScrape
     * Path: frontend/src/pages/dashboard/BlogManagementPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleScrape = async () => {
        setScraping(true);
        try {
            const response = await api.post('/blog/scrape');
            toast.success(response.data.message || 'News scrape completed');
            refetchPosts();
        } catch (error) {
            console.error('Failed to trigger scrape:', error);
            toast.error(error.response?.data?.detail || 'Failed to trigger scrape');
        } finally {
            setScraping(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleOpenDialog
     * Path: frontend/src/pages/dashboard/BlogManagementPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleOpenDialog = (post = null) => {
        if (post) {
            setCurrentPost(post);
            setFormData({
                title: post.title,
                content: post.content,
                excerpt: post.excerpt || '',
                cover_image: post.cover_image || '',
                tags: post.tags?.join(', ') || '',
                is_published: post.is_published,
                is_public: post.is_public ?? true,
                scope: post.scope || 'building'
            });
        } else {
            setCurrentPost(null);
            resetForm();
        }
        setIsDialogOpen(true);
    };

    const handleSavePost = handleSubmit(async (data) => {
        const payload = {
            ...data,
            tags: data.tags.split(',').map(tag => tag.trim()).filter(tag => tag !== '')
        };

        try {
            if (currentPost) {
                await api.put(`/blog/${currentPost.id}`, payload);
                toast.success('Article updated successfully');
            } else {
                await api.post('/blog', payload);
                toast.success('Article created successfully');
            }
            setIsDialogOpen(false);
            refetchPosts();
        } catch (error) {
            console.error('Failed to save article:', error);
            toast.error(error.response?.data?.detail || 'Failed to save article');
            throw error;
        }
    });
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/BlogManagementPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (postId) => {
        if (!window.confirm('Are you sure you want to delete this article?')) return;

        try {
            await api.delete(`/blog/${postId}`);
            toast.success('Article deleted');
            refetchPosts();
        } catch (error) {
            console.error('Failed to delete article:', error);
            toast.error('Failed to delete article');
        }
    };

    const filteredPosts = ( posts || [] ).filter(post =>
        post.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        post.author_name.toLowerCase().includes(searchQuery.toLowerCase())
    );

    return (
        <div className="space-y-6">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Blog & News Management</h1>
                    <p className="text-muted-foreground">Create and manage articles for the community blog</p>
                </div>
                <div className="flex gap-2">
                    {['super_admin'].includes(user?.role) && (
                        <Button
                            variant="outline"
                            onClick={handleScrape}
                            disabled={scraping}
                            className="hidden md:flex gap-2"
                        >
                            <RefreshCw className={`h-4 w-4 ${scraping ? 'animate-spin' : ''}`}/>
                            Scrape News
                        </Button>
                    )}
                    <Button onClick={() => handleOpenDialog()} className="gap-2">
                        <Plus className="h-4 w-4"/>
                        Create Article
                    </Button>
                </div>
            </div>

            <Card className="card-dashboard">
                <CardHeader className="pb-3">
                    <div className="flex items-center gap-2">
                        <div className="relative flex-1">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                placeholder="Search articles..."
                                className="pl-9"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    {postsLoading ? (
                        <div className="flex items-center justify-center py-12">
                            <Loader2 className="h-8 w-8 animate-spin text-primary"/>
                        </div>
                    ) : filteredPosts.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground">
                            <FileText className="h-12 w-12 mx-auto mb-4 opacity-20"/>
                            <p>No articles found</p>
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                            {filteredPosts.map((post) => (
                                <Card key={post.id}
                                      className="overflow-hidden border-2 hover:border-primary/50 transition-colors">
                                    <div className="aspect-video w-full bg-muted relative">
                                        {post.cover_image ? (
                                            <img
                                                src={post.cover_image}
                                                alt={post.title}
                                                className="w-full h-full object-cover"
                                            />
                                        ) : (
                                            <div className="w-full h-full flex items-center justify-center">
                                                <FileText className="h-12 w-12 opacity-10"/>
                                            </div>
                                        )}
                                        <div className="absolute top-2 right-2 flex gap-1">
                                            {post.is_draft ? (
                                                <Badge variant="secondary"
                                                       className="bg-yellow-100 text-yellow-800">Draft</Badge>
                                            ) : (
                                                <Badge variant="secondary"
                                                       className="bg-green-100 text-green-800">Published</Badge>
                                            )}
                                        </div>
                                    </div>
                                    <CardContent className="p-4 space-y-3">
                                        <div>
                                            <h3 className="font-bold line-clamp-1">{post.title}</h3>
                                            <p className="text-xs text-muted-foreground mt-1">
                                                By {post.author_name} • {formatDate(post.created_at)}
                                            </p>
                                        </div>
                                        <p className="text-sm text-muted-foreground line-clamp-2 min-h-[40px]">
                                            {post.excerpt || post.content.substring(0, 100) + '...'}
                                        </p>
                                        <div className="flex flex-wrap gap-1">
                                            {post.tags?.map(tag => (
                                                <Badge key={tag} variant="outline"
                                                       className="text-[10px] py-0">{tag}</Badge>
                                            ))}
                                        </div>
                                        {post.expires_at && (
                                            <p className="text-[10px] text-orange-600 flex items-center gap-1">
                                                <Clock className="h-3 w-3"/>
                                                Expires: {formatDate(post.expires_at)}
                                            </p>
                                        )}
                                        <div className="flex items-center justify-between pt-2 border-t">
                                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                                <Eye className="h-3 w-3"/>
                                                {post.views} views
                                            </div>
                                            <div className="flex gap-2">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8 text-muted-foreground hover:text-primary"
                                                    onClick={() => handleOpenDialog(post)}
                                                >
                                                    <Edit className="h-4 w-4"/>
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    className="h-8 w-8 text-muted-foreground hover:text-destructive"
                                                    onClick={() => handleDelete(post.id)}
                                                >
                                                    <Trash2 className="h-4 w-4"/>
                                                </Button>
                                            </div>
                                        </div>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>{currentPost ? 'Edit Article' : 'Create New Article'}</DialogTitle>
                        <DialogDescription>
                            Articles will be displayed on the public blog page.
                        </DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleSavePost} className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label htmlFor="title">Title</Label>
                            <Input
                                id="title"
                                name="title"
                                value={formData.title}
                                onChange={handleChange}
                                placeholder="Enter a catchy title"
                                required
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="cover_image">Cover Image URL</Label>
                                <Input
                                    id="cover_image"
                                    name="cover_image"
                                    value={formData.cover_image}
                                    onChange={handleChange}
                                    placeholder="https://..."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="tags">Tags (comma separated)</Label>
                                <Input
                                    id="tags"
                                    name="tags"
                                    value={formData.tags}
                                    onChange={handleChange}
                                    placeholder="Community, News, Update"
                                />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="excerpt">Excerpt / Summary</Label>
                            <Input
                                id="excerpt"
                                name="excerpt"
                                value={formData.excerpt}
                                onChange={handleChange}
                                placeholder="Brief summary of the article"
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="content">Content (Markdown supported)</Label>
                            <Textarea
                                id="content"
                                name="content"
                                value={formData.content}
                                onChange={handleChange}
                                placeholder="Write your article content here..."
                                className="min-h-[200px]"
                                required
                            />
                        </div>

                        <div className="flex items-center justify-between p-3 border rounded-lg">
                            <div className="space-y-0.5">
                                <Label>Publish Immediately</Label>
                                <p className="text-xs text-muted-foreground">
                                    If turned off, this will be saved as a draft.
                                </p>
                            </div>
                            <Switch
                                checked={formData.is_published}
                                onCheckedChange={(checked) => handleChange('is_published', checked)}
                            />
                        </div>

                        <div className="flex items-center justify-between p-3 border rounded-lg">
                            <div className="space-y-0.5">
                                <Label>Public Access</Label>
                                <p className="text-xs text-muted-foreground">
                                    Visible to unauthenticated users.
                                </p>
                            </div>
                            <Switch
                                checked={formData.is_public}
                                onCheckedChange={(checked) => handleChange('is_public', checked)}
                            />
                        </div>

                        <div
                            className="flex items-center justify-between p-3 border rounded-lg bg-slate-50 border-slate-200">
                            <div className="space-y-0.5">
                                <Label>Global Scope</Label>
                                <p className="text-xs text-slate-600">
                                    Visible in Global feed across all buildings.
                                </p>
                            </div>
                            <Switch
                                checked={formData.scope === 'global'}
                                onCheckedChange={(checked) => handleChange('scope', checked ? 'global' : 'building')}
                            />
                            <Label htmlFor="scope_global" className="text-blue-800 text-sm">
                                Share globally (visible in all buildings)
                            </Label>
                        </div>

                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setIsDialogOpen(false)}>
                                Cancel
                            </Button>
                            <Button type="submit" disabled={isSubmitting}>
                                {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                                {currentPost ? 'Update Article' : 'Create Article'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default BlogManagementPage;
