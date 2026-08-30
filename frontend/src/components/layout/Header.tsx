"use client";
import React, {useState} from 'react';
import Link from 'next/link';
import {usePathname, useRouter} from 'next/navigation';
import {signOut as nextAuthSignOut} from 'next-auth/react';
import {useAuth} from '../../contexts/AuthContext';
import {Button} from '../ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import {Tooltip, TooltipContent, TooltipTrigger,} from '../ui/tooltip';
import {Avatar, AvatarFallback, AvatarImage} from '../ui/avatar';
import {ScrollArea} from '../ui/scroll-area';
import {
    Bell,
    Building2,
    FileText,
    Home,
    LogOut,
    Menu,
    MessageSquare,
    Newspaper,
    Phone,
    Settings,
    ShoppingBag,
    Users,
    UserSearch,
    X
} from 'lucide-react';
import {getInitials, roleDisplayNames} from '../../lib/utils';
import ImpersonateModal from './ImpersonateModal';
import ImpersonationBanner from './ImpersonationBanner';

const DEFAULT_BUILDING_ID = process.env.NEXT_PUBLIC_DEFAULT_BUILDING_ID || '13195';
/**
 * @generated FunctionHeader
 * Function: Header
 * Path: frontend/src/components/layout/Header.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const Header: React.FC = () => {
    const {
        user, logout, isAuthenticated, isAdmin, isRealAdmin,
        notifications, unreadCount, markNotificationRead, markAllRead,
        isImpersonating, selectedBuilding
    } = useAuth();
    const router = useRouter();
    const pathname = usePathname();
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
    const [impersonateModalOpen, setImpersonateModalOpen] = useState(false);

    const publicNavItems = [
        {href: '/', label: 'Home', icon: Home},
        {href: '/about', label: 'About', icon: Users},
        {href: '/blog', label: 'News', icon: Newspaper},
        {href: '/emergency-services', label: 'Emergency', icon: Phone},
        {href: '/marketplace', label: 'Marketplace', icon: ShoppingBag},
    ];
    /**
     * @generated FunctionHeader
     * Function: handleLogout
     * Path: frontend/src/components/layout/Header.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleLogout = async () => {
        try {
            await nextAuthSignOut({redirect: false});
        } finally {
            logout();
            router.replace('/');
        }
    };

    return (
        <>
            <ImpersonationBanner/>
            <header className="glass-header sticky top-0 z-50" data-testid="main-header">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex items-center justify-between h-16">
                        {/* Logo */}
                        <Link
                            href="/"
                            className="flex items-center gap-2 hover:opacity-80 transition-opacity"
                            data-testid="header-logo"
                        >
                            <div className="flex items-center gap-2">
                                {selectedBuilding?.id === DEFAULT_BUILDING_ID ? (
                                    <div className="flex items-center gap-2">
                                        <img src="/eastgate-logo.png" alt="East Gate Residences Logo"
                                             className="h-10 w-auto"/>
                                        <span
                                            className="font-bold text-xl hidden sm:block tracking-tight text-slate-800">
                     East Gate <span className="text-primary">Residences</span>
                   </span>
                                    </div>
                                ) : (
                                    <>
                                        <Building2 className="h-8 w-8 text-primary"/>
                                        <span className="font-bold text-lg hidden sm:block">
                    {selectedBuilding?.name || 'Our Residences'}
                  </span>
                                    </>
                                )}
                            </div>
                        </Link>

                        {/* Desktop Navigation */}
                        <nav className="hidden md:flex items-center gap-1" data-testid="desktop-nav">
                            {publicNavItems.map((item) => (
                                <Link
                                    key={item.href}
                                    href={item.href}
                                    className={`px-4 py-2 rounded-full text-sm font-medium transition-colors ${
                                        pathname === item.href
                                            ? 'bg-primary/10 text-primary'
                                            : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                                    }`}
                                    data-testid={`nav-${item.label.toLowerCase()}`}
                                >
                                    {item.label}
                                </Link>
                            ))}
                        </nav>

                        {/* Right side actions */}
                        <div className="flex items-center gap-3">
                            {isAuthenticated ? (
                                <>
                                    {/* Notifications */}
                                    <DropdownMenu>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <DropdownMenuTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="relative"
                                                        data-testid="notifications-btn"
                                                        aria-label="Notifications"
                                                    >
                                                        <Bell className="h-5 w-5"/>
                                                        {unreadCount > 0 && (
                                                            <span
                                                                className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] font-medium text-destructive-foreground">
                              {unreadCount > 9 ? '9+' : unreadCount}
                            </span>
                                                        )}
                                                    </Button>
                                                </DropdownMenuTrigger>
                                            </TooltipTrigger>
                                            <TooltipContent>
                                                <p>Notifications</p>
                                            </TooltipContent>
                                        </Tooltip>
                                        <DropdownMenuContent className="w-80" align="end">
                                            <DropdownMenuLabel className="flex items-center justify-between">
                                                <span>Notifications</span>
                                                {unreadCount > 0 && (
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="h-auto p-0 text-xs font-normal text-primary"
                                                        onClick={(e) => {
                                                            e.preventDefault();
                                                            markAllRead();
                                                        }}
                                                    >
                                                        Mark all as read
                                                    </Button>
                                                )}
                                            </DropdownMenuLabel>
                                            <DropdownMenuSeparator/>
                                            <ScrollArea className="h-[300px]">
                                                {notifications.length > 0 ? (
                                                    notifications.map((notif) => (
                                                        <DropdownMenuItem
                                                            key={notif.id}
                                                            className="flex flex-col items-start p-4 cursor-pointer"
                                                            onClick={() => {
                                                                markNotificationRead(notif.id);
                                                                if (notif.link) router.push(notif.link);
                                                            }}
                                                        >
                                                            <div
                                                                className="flex w-full justify-between items-start mb-1">
                                                                <span
                                                                    className="font-semibold text-sm">{notif.title}</span>
                                                                <span
                                                                    className="text-[10px] text-muted-foreground whitespace-nowrap">
                                {new Date(notif.created_at).toLocaleDateString()}
                              </span>
                                                            </div>
                                                            <p className="text-xs text-muted-foreground line-clamp-2">
                                                                {notif.message}
                                                            </p>
                                                        </DropdownMenuItem>
                                                    ))
                                                ) : (
                                                    <div
                                                        className="flex flex-col items-center justify-center p-8 text-center">
                                                        <Bell className="h-8 w-8 text-muted-foreground/30 mb-2"/>
                                                        <p className="text-sm text-muted-foreground">No unread
                                                            notifications</p>
                                                    </div>
                                                )}
                                            </ScrollArea>
                                            <DropdownMenuSeparator/>
                                            <DropdownMenuItem
                                                className="justify-center text-xs font-medium text-primary py-2"
                                                onClick={() => router.push('/dashboard')}
                                            >
                                                View all activities
                                            </DropdownMenuItem>
                                        </DropdownMenuContent>
                                    </DropdownMenu>

                                    {/* User Menu */}
                                    <DropdownMenu>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <DropdownMenuTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        className="relative h-10 w-10 rounded-full"
                                                        data-testid="user-menu-trigger"
                                                        aria-label="User account menu"
                                                    >
                                                        <Avatar className="h-10 w-10">
                                                            <AvatarImage src={user?.profile_image}
                                                                         alt={user?.full_name}/>
                                                            <AvatarFallback
                                                                className="bg-primary text-primary-foreground">
                                                                {getInitials(user?.full_name)}
                                                            </AvatarFallback>
                                                        </Avatar>
                                                    </Button>
                                                </DropdownMenuTrigger>
                                            </TooltipTrigger>
                                            <TooltipContent>
                                                <p>User account menu</p>
                                            </TooltipContent>
                                        </Tooltip>
                                        <DropdownMenuContent className="w-56" align="end" data-testid="user-dropdown">
                                            <DropdownMenuLabel className="font-normal">
                                                <div className="flex flex-col space-y-1">
                                                    <p className="text-sm font-medium">{user?.full_name}</p>
                                                    <p className="text-xs text-muted-foreground">{user?.email}</p>
                                                    <p className="text-xs text-primary">{user?.role ? roleDisplayNames[user.role as keyof typeof roleDisplayNames] : ''}</p>
                                                </div>
                                            </DropdownMenuLabel>
                                            <DropdownMenuSeparator/>
                                            {!isImpersonating && (
                                                <DropdownMenuItem onClick={() => router.push('/profile')}
                                                                  data-testid="menu-profile">
                                                    <Settings className="mr-2 h-4 w-4"/>
                                                    Profile Settings
                                                </DropdownMenuItem>
                                            )}
                                            <DropdownMenuItem onClick={() => router.push('/dashboard')}
                                                              data-testid="menu-dashboard">
                                                <Home className="mr-2 h-4 w-4"/>
                                                Dashboard
                                            </DropdownMenuItem>
                                            <DropdownMenuItem onClick={() => router.push('/community/chat')}
                                                              data-testid="menu-chat">
                                                <MessageSquare className="mr-2 h-4 w-4"/>
                                                Chat
                                            </DropdownMenuItem>
                                            <DropdownMenuItem onClick={() => router.push('/documents')}
                                                              data-testid="menu-documents">
                                                <FileText className="mr-2 h-4 w-4"/>
                                                Documents
                                            </DropdownMenuItem>
                                            <DropdownMenuItem onClick={() => router.push('/community/my-listings')}
                                                              data-testid="menu-listings">
                                                <ShoppingBag className="mr-2 h-4 w-4"/>
                                                My Listings
                                            </DropdownMenuItem>
                                            {isRealAdmin() && (
                                                <>
                                                    {isAdmin() && (
                                                        <DropdownMenuItem
                                                            onClick={() => router.push('/settings')}
                                                            data-testid="menu-settings">
                                                            <Settings className="mr-2 h-4 w-4"/>
                                                            Settings
                                                        </DropdownMenuItem>
                                                    )}
                                                    {!isImpersonating && (
                                                        <DropdownMenuItem onClick={() => setImpersonateModalOpen(true)}
                                                                          data-testid="menu-impersonate">
                                                            <UserSearch className="mr-2 h-4 w-4"/>
                                                            Impersonate User
                                                        </DropdownMenuItem>
                                                    )}
                                                </>
                                            )}
                                            <DropdownMenuSeparator/>
                                            <DropdownMenuItem onClick={handleLogout} className="text-destructive"
                                                              data-testid="menu-logout">
                                                <LogOut className="mr-2 h-4 w-4"/>
                                                Log out
                                            </DropdownMenuItem>
                                        </DropdownMenuContent>
                                    </DropdownMenu>
                                </>
                            ) : (
                                <div className="flex items-center gap-2">
                                    <Button
                                        variant="ghost"
                                        onClick={() => router.push('/login')}
                                        className="hidden sm:flex"
                                        data-testid="login-btn"
                                    >
                                        Sign In
                                    </Button>
                                    <Button
                                        onClick={() => router.push('/register')}
                                        className="btn-primary"
                                        data-testid="register-btn"
                                    >
                                        Join Community
                                    </Button>
                                </div>
                            )}

                            {/* Mobile menu button */}
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="md:hidden"
                                        onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                                        data-testid="mobile-menu-toggle"
                                        aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
                                    >
                                        {mobileMenuOpen ? <X className="h-6 w-6"/> : <Menu className="h-6 w-6"/>}
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>
                                    <p>{mobileMenuOpen ? "Close menu" : "Open menu"}</p>
                                </TooltipContent>
                            </Tooltip>
                        </div>
                    </div>
                </div>

                {/* Mobile Navigation */}
                <ImpersonateModal
                    isOpen={impersonateModalOpen}
                    onClose={() => setImpersonateModalOpen(false)}
                />

                {mobileMenuOpen && (
                    <div className="md:hidden border-t border-border" data-testid="mobile-nav">
                        <nav className="px-4 py-4 space-y-1">
                            {publicNavItems.map((item) => (
                                <Link
                                    key={item.href}
                                    href={item.href}
                                    onClick={() => setMobileMenuOpen(false)}
                                    className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-colors ${
                                        pathname === item.href
                                            ? 'bg-primary/10 text-primary'
                                            : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                                    }`}
                                >
                                    <item.icon className="h-5 w-5"/>
                                    {item.label}
                                </Link>
                            ))}
                            {!isAuthenticated && (
                                <Link
                                    href="/login"
                                    onClick={() => setMobileMenuOpen(false)}
                                    className="flex items-center gap-3 px-4 py-3 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted"
                                >
                                    Sign In
                                </Link>
                            )}
                        </nav>
                    </div>
                )}
            </header>
        </>
    );
};

export default Header;
