import {AxiosInstance} from 'axios';
import {Permission, UserResponse} from './user';

export interface AuthNotification {
    id: string;
    title: string;
    message: string;
    is_read: boolean;
    created_at: string;
    link?: string;
}

export interface RegisterRequest {
    email: string;
    password: string;
    full_name: string;
    role?: string;
    unit_number?: string;
    phone?: string;

    [key: string]: unknown;
}

export interface AuthContextType {
    user: UserResponse | null;
    token: string | null;
    loading: boolean;
    featureAccess: Record<string, boolean>;
    notifications: AuthNotification[];
    unreadCount: number;
    pendingApprovalsCount: number;
    setPendingApprovalsCount: (count: number) => void;
    selectedYear: string | null;
    setSelectedYear: (year: string) => void;
    availableYears: string[];
    financialYearStartMonth: number;
    login: (email: string, password: string) => Promise<{ error?: string; ok?: boolean }>;
    register: (userData: RegisterRequest) => Promise<UserResponse>;
    logout: () => void;
    updateProfile: (updateData: Partial<UserResponse>) => Promise<UserResponse | undefined>;
    hasPermission: (permission: keyof Permission | 'can_manage_users') => boolean;
    isAdmin: () => boolean;
    isRealAdmin: () => boolean;
    isManager: () => boolean;
    isECMember: () => boolean;
    hasFeatureAccess: (featureKey: string) => boolean;
    isImpersonating: boolean;
    impersonate: (userId: string) => Promise<boolean>;
    exitImpersonation: () => Promise<void>;
    api: AxiosInstance;
    fetchNotifications: () => Promise<void>;
    markNotificationRead: (notifId: string) => Promise<void>;
    markAllRead: () => Promise<void>;
    fetchPendingApprovalsCount: (force?: boolean) => Promise<void>;
    isAuthenticated: boolean;
}
