import {auth} from "@/auth"
import {NextResponse} from "next/server"

const PUBLIC_PATHS = [
    '/',
    '/login',
    '/register',
    '/about',
    '/blog',
    '/marketplace',
    '/emergency-services',
    '/privacy',
    '/terms',
    '/forgot-password',
] as const

// Keep this list aligned with frontend/src/app/(app). These are authenticated
// application namespaces after the route cleanup moved feature pages out from
// /dashboard/* into their product/menu URL roots.
const PRIVATE_APP_PATHS = [
    '/dashboard',
    '/admin',
    '/building-info',
    '/community',
    '/compliance',
    '/documents',
    '/financials',
    '/governance',
    '/insurance',
    '/intelligence',
    '/maintenance',
    '/management',
    '/my-communications',
    '/notifications',
    '/owner-hub',
    '/owner-view',
    '/powerhouse',
    '/profile',
    '/real-estate',
    '/reports',
    '/requests',
    '/settings',
] as const

function isPathWithin(pathname: string, basePath: string): boolean {
    return basePath === '/'
        ? pathname === '/'
        : pathname === basePath || pathname.startsWith(`${basePath}/`)
}

export default auth((req) => {
    const {nextUrl, auth: session} = req
    const isLoggedIn = !!session

    const isPublicPath = PUBLIC_PATHS.some(path => isPathWithin(nextUrl.pathname, path))

    const isPrivateAppPath = !isPublicPath && PRIVATE_APP_PATHS.some(path => isPathWithin(nextUrl.pathname, path))

    // Middleware must protect the browser URL roots, not the legacy component
    // folder names. /dashboard remains the authenticated home only.
    if (isPrivateAppPath && !isLoggedIn) {
        return NextResponse.redirect(new URL('/login', req.url))
    }

    // Redirect authenticated users away from login/register
    if (isLoggedIn && (nextUrl.pathname === '/login' || nextUrl.pathname === '/register')) {
        return NextResponse.redirect(new URL('/dashboard', req.url))
    }

    return NextResponse.next()
})

export const config = {
    matcher: ['/((?!api|_next/static|_next/image|favicon.ico|favicon.svg|east-gate.ico|images|user-guides).*)'],
}
