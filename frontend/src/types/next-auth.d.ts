import {DefaultSession} from 'next-auth';

/**
 * Module augmentation for next-auth to include custom fields
 * returned by the credentials provider (accessToken + user.data).
 */
declare module 'next-auth' {
    interface Session extends DefaultSession {
        /** JWT access token forwarded from the backend */
        accessToken?: string;
        user?: DefaultSession['user'] & {
            /** Raw user object returned by the backend /auth/login endpoint */
            data?: {
                id: string;
                email: string;
                full_name: string;
                role: string;
                unit_number?: string;
                is_approved: boolean;
                permissions?: Record<string, boolean>;
                created_at: string;
                [key: string]: unknown;
            };
        };
    }

    interface JWT {
        accessToken?: string;
        user?: {
            id: string;
            email: string;
            full_name: string;
            role: string;
            [key: string]: unknown;
        };
    }
}
