import type {Metadata, ResolvingMetadata} from 'next';
import {BlogPostPage} from "@/pages/public/BlogPage";
import {truncateDescription} from '@/lib/utils';

const BACKEND_URL = process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8003';

type Props = {
    params: Promise<{ postId: string }>;
};
/**
 * @generated FunctionHeader
 * Function: generateMetadata
 * Path: frontend/src/app/(public)/blog/[postId]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export async function generateMetadata(
    {params}: Props,
    parent: ResolvingMetadata
): Promise<Metadata> {
    const {postId} = await params;

    try {
        const response = await fetch(`${BACKEND_URL}/api/blog/${postId}`);
        if (!response.ok) {
            console.error(`Failed to fetch post ${postId}: ${response.status}`);
            return {title: 'Community Blog Post'};
        }
        const post = await response.json();

        if (!post) return {title: 'Post Not Found'};

        const title = `${post.title} | Community News`;
        const description = truncateDescription(post.excerpt || post.content) || "Read the latest news from East Gate Residences.";
        const image = post.cover_image || "/images/east_gate_residences.jpg";

        return {
            title,
            description,
            keywords: [...(post.tags || []), "strata news", "Canberra blog", "owners corporation"],
            openGraph: {
                title,
                description,
                images: [image],
                type: 'article',
                publishedTime: post.created_at,
                authors: [post.author_name],
            },
            twitter: {
                card: 'summary_large_image',
                title,
                description,
                images: [image],
            },
        };
    } catch (error) {
        console.error(`Error generating metadata for blog post ${postId}:`, error);
        return {title: 'Community Blog Post'};
    }
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(public)/blog/[postId]/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return <BlogPostPage/>;
}
