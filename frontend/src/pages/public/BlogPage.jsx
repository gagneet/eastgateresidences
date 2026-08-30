// @featuretrace:news — Public news/blog page: articles auto-scraped or manually posted by admins.
// Layer: frontend
// Data flow: this page → GET /api/blog?scope={building|global} → db.blog_posts (building-scoped).
// Scope toggle: authenticated users see "This Building" / "Global Feed" switch; guests always see global.
// Building identity: X-Building-ID header injected by AuthContext api interceptor.
// Related: backend/server.py #news-routes (GET/POST/PUT/DELETE /blog)
//           backend/cron/cron_news_scraper.py (auto-populates articles with building_id)
//           backend/server.py POST /blog/scrape (manual scraper trigger)
//           frontend/src/pages/dashboard/BlogManagementPage.jsx (admin CRUD + scrape trigger UI)
//           frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx (scraper management UI)
"use client";
import Link from 'next/link';
import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import axios from 'axios';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Avatar, AvatarFallback } from '../../components/ui/avatar';
import { ArrowLeft, Calendar, Eye, Newspaper } from 'lucide-react';
import { formatDate, getInitials } from '../../lib/utils';
import { Button } from '../../components/ui/button';

const API_URL = `${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8003'}/api`;
// Matches the convention in AuthContext.tsx/Header.tsx/DashboardLayout.tsx — never fall
// back to selectedBuilding.id (a Postgres scheme UUID): a stale cached selectedBuilding
// from before the id/building_id split existed may have only `.id`, and sending a UUID
// as X-Building-ID silently returns an empty feed (get_optional_building() passes the
// header straight into Mongo building_id matching, no UUID resolution).
const DEFAULT_BUILDING_ID = process.env.NEXT_PUBLIC_DEFAULT_BUILDING_ID || '13195';
/**
 * @generated FunctionHeader
 * Function: BlogPage
 * Path: frontend/src/pages/public/BlogPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BlogPage = () => {
    const {api, isAuthenticated, selectedBuilding} = useAuth();
    const [posts, setPosts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [scope, setScope] = useState('global');

    const fetchPosts = useCallback(async () => {
        setLoading(true);
        try {
            const response = await api.get('/blog', {
                params: {scope}
            });
            setPosts(response.data);
        } catch (error) {
            console.error('Failed to fetch posts:', error);
        } finally {
            setLoading(false);
        }
    }, [api, scope]);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchPosts
         * Path: frontend/src/pages/public/BlogPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchPosts = async () => {
            setLoading(true);
            try {
                const buildingId = selectedBuilding?.building_id || DEFAULT_BUILDING_ID;
                const headers = {'X-Building-ID': buildingId};
                const fetcher = isAuthenticated ? api : {
get: (url, config) => axios.get(url, {...config, headers})};

                const response = await fetcher.get(`${API_URL}/blog`, {
                    params: {scope: isAuthenticated ? scope : 'global'}
                });
                setPosts(response.data);
            } catch (error) {
                console.error('Failed to fetch posts:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchPosts();
    }, [isAuthenticated, selectedBuilding, api, scope]);


    return (
        <div className="min-h-screen" data-testid="blog-page">
            {/* Hero Section */}
            <section className="py-16 bg-primary">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="text-center text-white">
                        <Badge className="mb-4 bg-white/20 text-white border-white/30">News & Updates</Badge>
                        <h1 className="text-4xl sm:text-5xl font-bold mb-4">
                            {scope === 'global' ? 'Global Community News' : `${selectedBuilding?.name || 'Community'} News`}
                        </h1>
                        <p className="text-xl text-white/90 max-w-2xl mx-auto mb-8">
                            Stay informed with the latest updates, announcements, and stories.
                        </p>

                        {/* Scope Toggle */}
                        {isAuthenticated && (
                            <div className="flex justify-center gap-2">
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
                    </div>
                </div>
            </section>

            {/* Posts List */}
            <section className="py-12" data-testid="blog-posts">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    {loading ? (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                            {[1, 2, 3, 4, 5, 6].map((i) => (
                                <Card key={i} className="card-marketing">
                                    <div className="aspect-video skeleton"/>
                                    <CardContent className="p-6">
                                        <div className="skeleton h-6 w-3/4 mb-4"/>
                                        <div className="skeleton h-4 w-full mb-2"/>
                                        <div className="skeleton h-4 w-2/3"/>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    ) : posts.length === 0 ? (
                        <Card className="card-dashboard">
                            <CardContent className="py-16 text-center">
                                <Newspaper className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                                <h3 className="text-lg font-medium mb-2">No Posts Yet</h3>
                                <p className="text-muted-foreground">
                                    Community news and updates will appear here once published.
                                </p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                            {posts.map((post) => (
                                <Link
                                    key={post.id}
                                    href={`/blog/${post.id}`}
                                    className="block group"
                                    data-testid={`blog-post-${post.id}`}
                                >
                                    <Card className="card-marketing h-full">
                                        <div className="aspect-video bg-muted overflow-hidden">
                                            {post.cover_image ? (
                                                <img
                                                    src={post.cover_image}
                                                    alt={post.title}
                                                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                                                />
                                            ) : (
                                                <div
                                                    className="w-full h-full flex items-center justify-center bg-primary/5">
                                                    <Newspaper className="h-12 w-12 text-primary/30"/>
                                                </div>
                                            )}
                                        </div>
                                        <CardContent className="p-6">
                                            {post.tags?.length > 0 && (
                                                <div className="flex flex-wrap gap-2 mb-3">
                                                    {post.tags.slice(0, 2).map((tag) => (
                                                        <Badge key={tag} variant="secondary" className="text-xs">
                                                            {tag}
                                                        </Badge>
                                                    ))}
                                                </div>
                                            )}
                                            <h2 className="text-xl font-semibold mb-3 group-hover:text-primary transition-colors line-clamp-2">
                                                {post.title}
                                            </h2>
                                            {post.excerpt && (
                                                <p className="text-muted-foreground mb-4 line-clamp-3">{post.excerpt}</p>
                                            )}
                                            <div
                                                className="flex items-center justify-between text-sm text-muted-foreground">
                                                <div className="flex items-center gap-2">
                                                    <Avatar className="h-6 w-6">
                                                        <AvatarFallback className="text-xs bg-primary/10 text-primary">
                                                            {getInitials(post.author_name)}
                                                        </AvatarFallback>
                                                    </Avatar>
                                                    <span>{post.author_name}</span>
                                                </div>
                                                <div className="flex items-center gap-4">
                          <span className="flex items-center gap-1">
                            <Calendar className="h-4 w-4"/>
                              {formatDate(post.created_at)}
                          </span>
                                                </div>
                                            </div>
                                        </CardContent>
                                    </Card>
                                </Link>
                            ))}
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: BlogPostPage
 * Path: frontend/src/pages/public/BlogPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const BlogPostPage = () => {
    const {api} = useAuth();
    const {postId} = useParams();
    const [post, setPost] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchPost
         * Path: frontend/src/pages/public/BlogPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchPost = async () => {
            try {
                const response = await api.get(`/blog/${postId}`);
                setPost(response.data);
            } catch (error) {
                console.error('Failed to fetch post:', error);
            } finally {
                setLoading(false);
            }
        };
        fetchPost();
    }, [api, postId]);

    if (loading) {
        return (
            <div className="min-h-screen py-12">
                <div className="max-w-4xl mx-auto px-4">
                    <div className="skeleton h-8 w-32 mb-8"/>
                    <div className="skeleton h-12 w-3/4 mb-4"/>
                    <div className="skeleton h-64 w-full mb-8"/>
                    <div className="space-y-4">
                        <div className="skeleton h-4 w-full"/>
                        <div className="skeleton h-4 w-full"/>
                        <div className="skeleton h-4 w-3/4"/>
                    </div>
                </div>
            </div>
        );
    }

    if (!post) {
        return (
            <div className="min-h-screen py-12">
                <div className="max-w-4xl mx-auto px-4 text-center">
                    <h1 className="text-2xl font-bold mb-4">Post Not Found</h1>
                    <Button asChild>
                        <Link href="/blog">Back to Blog</Link>
                    </Button>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen" data-testid="blog-post-detail">
            {/* Cover Image */}
            {post.cover_image && (
                <div className="relative h-[40vh] min-h-[300px] bg-muted">
                    <img
                        src={post.cover_image}
                        alt={post.title}
                        className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/50 to-transparent"/>
                </div>
            )}

            <div className="max-w-4xl mx-auto px-4 py-12">
                <Button variant="ghost" className="mb-8" asChild>
                    <Link href="/blog">
                        <ArrowLeft className="mr-2 h-4 w-4"/>
                        Back to Blog
                    </Link>
                </Button>

                {post.tags?.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-4">
                        {post.tags.map((tag) => (
                            <Badge key={tag} variant="secondary">{tag}</Badge>
                        ))}
                    </div>
                )}

                <h1 className="text-3xl sm:text-4xl font-bold mb-6">{post.title}</h1>

                <div className="flex items-center gap-4 mb-8 pb-8 border-b border-border">
                    <Avatar className="h-12 w-12">
                        <AvatarFallback className="bg-primary text-primary-foreground">
                            {getInitials(post.author_name)}
                        </AvatarFallback>
                    </Avatar>
                    <div>
                        <p className="font-medium">{post.author_name}</p>
                        <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <span className="flex items-center gap-1">
                <Calendar className="h-4 w-4"/>
                  {formatDate(post.created_at)}
              </span>
                            <span className="flex items-center gap-1">
                <Eye className="h-4 w-4"/>
                                {post.views} views
              </span>
                        </div>
                    </div>
                </div>

                <article className="prose prose-lg max-w-none">
                    <div className="whitespace-pre-wrap">{post.content}</div>
                </article>
            </div>
        </div>
    );
};

export default BlogPage;
