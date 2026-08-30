// @featuretrace:scraper — Super-admin UI for managing both News and Marketplace scrapers.
// Layer: frontend
// Data flow: this page → GET/PUT /api/settings/scrapers → db.scraper_settings (building-scoped).
//            Trigger buttons → POST /api/blog/scrape or /api/listings/scrape → cron scripts as subprocesses.
//            Log viewer → GET /api/settings/scrapers/{scraper}/logs → log files on disk + db.scraper_run_logs.
// Related: backend/server.py #scraper-routes (settings, trigger, logs endpoints)
//           backend/cron/cron_news_scraper.py (@featuretrace:news)
//           backend/cron/cron_property_scraper.py (@featuretrace:marketplace)
//           frontend/src/pages/public/BlogPage.jsx (consumer of news scraper output)
//           frontend/src/pages/public/MarketplacePage.jsx (consumer of property scraper output)
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Switch } from '../../../components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../../components/ui/tabs';
import { Badge } from '../../../components/ui/badge';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { ScrollArea } from '../../../components/ui/scroll-area';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../../components/ui/dialog';
import {
    Activity,
    AlertCircle,
    Bell,
    CheckCircle2,
    Clock,
    Database,
    FileText,
    Globe,
    Home,
    Newspaper,
    Pause,
    Play,
    RefreshCw,
    Settings,
    TrendingUp,
    XCircle
} from 'lucide-react';
import { toast } from 'sonner';
import { formatDateTime } from '../../../lib/utils';
/**
 * Scraper Settings Management Page
 *
 * Provides comprehensive UI for managing automated scrapers:
 * - Property listings scraper
 * - News articles scraper
 *
 * Features:
 * - Schedule management (cron expressions)
 * - Enable/disable toggles
 * - Manual trigger buttons
 * - Real-time status monitoring
 * - Configuration settings
 * - Log viewer
 * - Statistics dashboard
 *
 * Based on industry best practices:
 * - Oxylabs Scheduler (https://developers.oxylabs.io/scraping-solutions/web-scraper-api/features/scheduler)
 * - Web Scraper Cloud (https://webscraper.io/documentation/web-scraper-cloud/scheduler)
 * - Cronicle (https://github.com/jhuckaby/Cronicle)
 * - Crontab UI (https://github.com/alseambusher/crontab-ui)
 */
/**
 * @generated FunctionHeader
 * Function: ScraperSettingsPage
 * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ScraperSettingsPage = () => {
    const {api, isAdmin} = useAuth();

    // State management
    const [loading, setLoading] = useState(false);
    const [newSuburb, setNewSuburb] = useState('');
    const [newSite, setNewSite] = useState('');
    const [scraping, setScraping] = useState({news: false, property: false});
    const [settings, setSettings] = useState({
        news: {
            enabled: true,
            schedule: '0 1 * * 6', // Weekly, Saturday 1 AM
            scheduleType: 'weekly',
            scheduleDay: '6',
            scheduleTime: '01:00',
            method: 'rss',
            maxArticles: 10,
            minContentLength: 150,
            relevanceThreshold: 0.12,
            notifyOnError: true,
            notifyOnSuccess: false,
        },
        property: {
            enabled: true,
            schedule: '0 6 * * *', // Daily, 6 AM
            scheduleType: 'daily',
            // scheduleDay must be present even for 'daily' mode so that switching to
            // 'weekly' in the UI doesn't produce an undefined (uncontrolled) Select value.
            scheduleDay: '1',
            scheduleTime: '06:00',
            suburbs: ['Coombs', 'Whitlam', 'Wright', 'Denman Prospect'],
            sites: ['realestate.com.au', 'domain.com.au', 'allhomes.com.au', 'zango.com.au'],
            maxListingsPerSuburb: 10,
            expiryDays: 30,
            notifyOnError: true,
            notifyOnSuccess: false,
        }
    });

    const [stats, setStats] = useState({
        news: {
            lastRun: null,
            nextRun: null,
            totalRuns: 0,
            successfulRuns: 0,
            failedRuns: 0,
            totalArticles: 0,
            averageArticles: 0,
        },
        property: {
            lastRun: null,
            nextRun: null,
            totalRuns: 0,
            successfulRuns: 0,
            failedRuns: 0,
            totalListings: 0,
            averageListings: 0,
        }
    });

    const [logs, setLogs] = useState({
        news: [],
        property: []
    });

    const [selectedLogView, setSelectedLogView] = useState('news');
    const [logDialogOpen, setLogDialogOpen] = useState(false);
    // null = closed; any other value = open showing that panel.
    // Single state avoids the open+type getting out of sync.
    const [statsDialog, setStatsDialog] = useState(null); // null | 'articles' | 'listings' | 'runs' | 'successRate'

    // Fetch current settings and stats
    const fetchSettings = useCallback(async () => {
        try {
            const response = await api.get('/settings/scrapers');
            if (response.data) {
                const apiSettings = response.data.settings;
                if (apiSettings) {
                    const news = apiSettings.news || {};
                    const property = apiSettings.property || {};
                    setSettings(prev => ( {
                        news: {
                            ...prev.news,
                            enabled: news.enabled ?? prev.news.enabled,
                            schedule: news.schedule ?? prev.news.schedule,
                            scheduleType: news.schedule_preset ?? prev.news.scheduleType,
                            method: news.method ?? prev.news.method,
                            maxArticles: news.max_articles ?? prev.news.maxArticles,
                            minContentLength: news.min_content_length ?? prev.news.minContentLength,
                            relevanceThreshold: news.relevance_threshold ?? prev.news.relevanceThreshold,
                            notifyOnError: news.notify_on_error ?? prev.news.notifyOnError,
                            notifyOnSuccess: news.notify_on_success ?? prev.news.notifyOnSuccess,
                        },
                        property: {
                            ...prev.property,
                            enabled: property.enabled ?? prev.property.enabled,
                            schedule: property.schedule ?? prev.property.schedule,
                            scheduleType: property.schedule_preset ?? prev.property.scheduleType,
                            suburbs: property.suburbs ?? prev.property.suburbs,
                            sites: property.property_sites ?? prev.property.sites,
                            maxListingsPerSuburb: property.max_listings_per_suburb ?? prev.property.maxListingsPerSuburb,
                            expiryDays: property.expiry_days ?? prev.property.expiryDays,
                            notifyOnError: property.notify_on_error ?? prev.property.notifyOnError,
                            notifyOnSuccess: property.notify_on_success ?? prev.property.notifyOnSuccess,
                        },
                    } ));
                }

                // Map snake_case stats keys to camelCase
                const apiStats = response.data.stats || {};
                const ns = apiStats.news || {};
                const ps = apiStats.property || {};
                setStats({
                    news: {
                        lastRun: ns.last_run ?? null,
                        nextRun: ns.next_run ?? null,
                        totalRuns: ns.total_runs ?? 0,
                        successfulRuns: ns.successful_runs ?? 0,
                        failedRuns: ns.failed_runs ?? 0,
                        totalArticles: ns.total_articles ?? 0,
                        averageArticles: ns.average_articles ?? 0,
                        lastRunCount: ns.last_run_count ?? 0,
                        successRate: ns.success_rate ?? 100,
                    },
                    property: {
                        lastRun: ps.last_run ?? null,
                        nextRun: ps.next_run ?? null,
                        totalRuns: ps.total_runs ?? 0,
                        successfulRuns: ps.successful_runs ?? 0,
                        failedRuns: ps.failed_runs ?? 0,
                        totalListings: ps.total_listings ?? 0,
                        averageListings: ps.average_listings ?? 0,
                        lastRunCount: ps.last_run_count ?? 0,
                        successRate: ps.success_rate ?? 100,
                    },
                });
            }
        } catch (error) {
            console.error('Error fetching scraper settings:', error);
        }
    }, [api]);

    // Fetch logs
    const fetchLogs = useCallback(async (scraper) => {
        try {
            const response = await api.get(`/settings/scrapers/${scraper}/logs`, {
                params: {limit: 50}
            });
            if (response.data) {
                const raw = response.data.logs || [];
                const normalized = raw.map(entry =>
                    typeof entry === 'string'
                        ? {timestamp: null, level: 'INFO', message: entry}
                        : entry
                );
                setLogs(prev => ( {...prev, [ scraper ]: normalized} ));
            }
        } catch (error) {
            console.error(`Error fetching ${scraper} logs:`, error);
        }
    }, [api]);

    useEffect(() => {
        fetchSettings();
    }, [fetchSettings]);
    // Manual trigger scraper
    /**
     * @generated FunctionHeader
     * Function: triggerScraper
     * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const triggerScraper = async (type) => {
        setScraping(prev => ( {...prev, [ type ]: true} ));
        try {
            const endpoint = type === 'news' ? '/blog/scrape' : '/listings/scrape';
            const response = await api.post(endpoint);
            const count = type === 'news'
                ? ( response.data?.articles_count ?? 0 )
                : ( response.data?.listings_count ?? 0 );
            const label = type === 'news' ? 'articles' : 'listings';

            toast.success(`${type === 'news' ? 'News' : 'Property'} Scraper Complete`, {
                description: count > 0
                    ? `Created ${count} new ${label}`
                    : `Scraper completed — no new ${label} found`,
            });

            // Refresh stats
            await fetchSettings();
        } catch (error) {
            toast.error('Scraper Failed', {
                description: error.response?.data?.detail || 'An error occurred while scraping',
            });
        } finally {
            setScraping(prev => ( {...prev, [ type ]: false} ));
        }
    };
    // Save settings
    /**
     * @generated FunctionHeader
     * Function: saveSettings
     * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const saveSettings = async () => {
        setLoading(true);
        try {
            // Map camelCase state to snake_case for backend Pydantic models
            const payload = {
                news: {
                    enabled: settings.news.enabled,
                    schedule: settings.news.schedule,
                    schedule_preset: settings.news.scheduleType,
                    method: settings.news.method,
                    max_articles: settings.news.maxArticles,
                    min_content_length: settings.news.minContentLength,
                    relevance_threshold: settings.news.relevanceThreshold,
                    notify_on_error: settings.news.notifyOnError,
                    notify_on_success: settings.news.notifyOnSuccess,
                },
                property: {
                    enabled: settings.property.enabled,
                    schedule: settings.property.schedule,
                    schedule_preset: settings.property.scheduleType,
                    suburbs: settings.property.suburbs,
                    property_sites: settings.property.sites,
                    max_listings_per_suburb: settings.property.maxListingsPerSuburb,
                    expiry_days: settings.property.expiryDays,
                    notify_on_error: settings.property.notifyOnError,
                    notify_on_success: settings.property.notifyOnSuccess,
                },
            };
            await api.put('/settings/scrapers', payload);
            toast.success('Settings Saved', {
                description: 'Scraper settings have been updated successfully',
            });
            await fetchSettings();
        } catch (error) {
            toast.error('Save Failed', {
                description: error.response?.data?.detail || 'Failed to save settings',
            });
        } finally {
            setLoading(false);
        }
    };
    // Update schedule based on preset
    /**
     * @generated FunctionHeader
     * Function: updateSchedule
     * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateSchedule = (scraper, scheduleType, day, time) => {
        let cronExpression = '';
        const [hour, minute] = time.split(':');

        switch (scheduleType) {
            case 'hourly':
                cronExpression = `0 * * * *`;
                break;
            case 'daily':
                cronExpression = `${minute} ${hour} * * *`;
                break;
            case 'weekly':
                cronExpression = `${minute} ${hour} * * ${day}`;
                break;
            case 'custom':
                // Keep existing cron expression
                return;
            default:
                cronExpression = `${minute} ${hour} * * *`;
        }

        setSettings(prev => ( {
            ...prev,
            [ scraper ]: {
                ...prev[ scraper ],
                schedule: cronExpression,
                scheduleType,
                scheduleDay: day,
                scheduleTime: time,
            }
        } ));
    };
    // Calculate next run time (simplified)
    /**
     * @generated FunctionHeader
     * Function: getNextRunTime
     * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getNextRunTime = (cronExpression) => {
        // This is a simplified calculation - in production, use a proper cron parser
        const now = new Date();
        const [minute, hour, , , day] = cronExpression.split(' ');

        if (day !== '*') {
            // Weekly schedule
            const targetDay = parseInt(day);
            const currentDay = now.getDay();
            const daysUntil = ( targetDay - currentDay + 7 ) % 7 || 7;
            const nextRun = new Date(now);
            nextRun.setDate(now.getDate() + daysUntil);
            nextRun.setHours(parseInt(hour), parseInt(minute), 0, 0);
            return nextRun;
        } else {
            // Daily schedule
            const nextRun = new Date(now);
            nextRun.setHours(parseInt(hour), parseInt(minute), 0, 0);
            if (nextRun <= now) {
                nextRun.setDate(nextRun.getDate() + 1);
            }
            return nextRun;
        }
    };
    // Use the shared formatDateTime from lib/utils; fall back to 'Never' for null/undefined.
    /**
     * @generated FunctionHeader
     * Function: formatDate
     * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const formatDate = (date) => date ? formatDateTime(date) : 'Never';

    // Derived success rates — computed once when stats change, not on every render.
    const successRates = useMemo(() => {
        const newsRate = stats.news.totalRuns > 0
            ? Math.round(( stats.news.successfulRuns / stats.news.totalRuns ) * 100) : 100;
        const propertyRate = stats.property.totalRuns > 0
            ? Math.round(( stats.property.successfulRuns / stats.property.totalRuns ) * 100) : 100;
        const combined = stats.news.totalRuns + stats.property.totalRuns > 0
            ? Math.round(
                ( stats.news.successfulRuns + stats.property.successfulRuns ) /
                ( stats.news.totalRuns + stats.property.totalRuns ) * 100
            ) : 100;
        return {newsRate, propertyRate, combined};
    }, [
        stats.news.successfulRuns, stats.news.totalRuns,
        stats.property.successfulRuns, stats.property.totalRuns,
    ]);
    // Render status badge
    /**
     * @generated FunctionHeader
     * Function: StatusBadge
     * Path: frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const StatusBadge = ({enabled, lastRun, failedRuns, totalRuns}) => {
        if (!enabled) {
            return <Badge variant="secondary"><Pause className="w-3 h-3 mr-1"/>Disabled</Badge>;
        }
        if (!lastRun) {
            return <Badge variant="outline"><Clock className="w-3 h-3 mr-1"/>Pending</Badge>;
        }
        if (failedRuns > 0 && totalRuns > 0 && failedRuns / totalRuns > 0.2) {
            return <Badge variant="destructive"><XCircle className="w-3 h-3 mr-1"/>Failing</Badge>;
        }
        return <Badge variant="default" className="bg-green-600"><CheckCircle2 className="w-3 h-3 mr-1"/>Active</Badge>;
    };

    return (
        <div>
            {/* Header */}
            <div className="mb-8">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-3xl font-bold flex items-center gap-3">
                            <Settings className="w-8 h-8"/>
                            Scraper Management
                        </h1>
                        <p className="text-muted-foreground mt-2">
                            Configure and monitor automated content scrapers
                        </p>
                    </div>
                    <Button onClick={saveSettings} disabled={loading} size="lg">
                        {loading ? (
                            <>
                                <RefreshCw className="w-4 h-4 mr-2 animate-spin"/>
                                Saving...
                            </>
                        ) : (
                            <>
                                <Database className="w-4 h-4 mr-2"/>
                                Save All Settings
                            </>
                        )}
                    </Button>
                </div>

                {/* Quick Stats — all cards clickable for details */}
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-6">
                    <Card
                        className="p-4 cursor-pointer hover:shadow-md hover:border-blue-400 transition-all"
                        onClick={() => {
                            setStatsDialog('articles');
                        }}
                    >
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-muted-foreground">News Articles</p>
                                <p className="text-2xl font-bold">{stats.news.totalArticles}</p>
                                <p className="text-xs text-muted-foreground mt-1">Click for details</p>
                            </div>
                            <Newspaper className="w-8 h-8 text-blue-500"/>
                        </div>
                    </Card>

                    <Card
                        className="p-4 cursor-pointer hover:shadow-md hover:border-green-400 transition-all"
                        onClick={() => {
                            setStatsDialog('listings');
                        }}
                    >
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-muted-foreground">Property Listings</p>
                                <p className="text-2xl font-bold">{stats.property.totalListings}</p>
                                <p className="text-xs text-muted-foreground mt-1">Click for details</p>
                            </div>
                            <Home className="w-8 h-8 text-green-500"/>
                        </div>
                    </Card>

                    <Card
                        className="p-4 cursor-pointer hover:shadow-md hover:border-purple-400 transition-all"
                        onClick={() => {
                            setStatsDialog('runs');
                        }}
                    >
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-muted-foreground">Total Runs</p>
                                <p className="text-2xl font-bold">{stats.news.totalRuns + stats.property.totalRuns}</p>
                                <p className="text-xs text-muted-foreground mt-1">Click for details</p>
                            </div>
                            <Activity className="w-8 h-8 text-purple-500"/>
                        </div>
                    </Card>

                    <Card
                        className="p-4 cursor-pointer hover:shadow-md hover:border-orange-400 transition-all"
                        onClick={() => {
                            setStatsDialog('successRate');
                        }}
                    >
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-muted-foreground">Success Rate</p>
                                <p className="text-2xl font-bold">{successRates.combined}%</p>
                                <p className="text-xs text-muted-foreground mt-1">Click for details</p>
                            </div>
                            <TrendingUp className="w-8 h-8 text-orange-500"/>
                        </div>
                    </Card>
                </div>
            </div>

            {/* Main Content */}
            <Tabs defaultValue="news" className="space-y-6">
                <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="news" className="flex items-center gap-2">
                        <Newspaper className="w-4 h-4"/>
                        News Scraper
                    </TabsTrigger>
                    <TabsTrigger value="property" className="flex items-center gap-2">
                        <Home className="w-4 h-4"/>
                        Property Scraper
                    </TabsTrigger>
                </TabsList>

                {/* NEWS SCRAPER TAB */}
                <TabsContent value="news" className="space-y-6">
                    {/* Status Card */}
                    <Card className="p-6">
                        <div className="flex items-start justify-between mb-4">
                            <div>
                                <h3 className="text-xl font-semibold flex items-center gap-2 mb-2">
                                    <Newspaper className="w-5 h-5"/>
                                    News Articles Scraper
                                </h3>
                                <p className="text-sm text-muted-foreground">
                                    Fetches news articles from 9 RSS feeds covering Canberra, ACT, and property news
                                </p>
                            </div>
                            <StatusBadge
                                enabled={settings.news.enabled}
                                lastRun={stats.news.lastRun}
                                failedRuns={stats.news.failedRuns}
                                totalRuns={stats.news.totalRuns}
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-6">
                            <div className="space-y-1">
                                <p className="text-sm text-muted-foreground">Last Run</p>
                                <p className="font-medium">{formatDate(stats.news.lastRun)}</p>
                            </div>
                            <div className="space-y-1">
                                <p className="text-sm text-muted-foreground">Next Scheduled</p>
                                <p className="font-medium">{formatDate(getNextRunTime(settings.news.schedule))}</p>
                            </div>
                            <div className="space-y-1">
                                <p className="text-sm text-muted-foreground">Average Articles/Run</p>
                                <p className="font-medium">{stats.news.averageArticles}</p>
                            </div>
                        </div>

                        <div className="flex gap-3 mt-6">
                            <Button
                                onClick={() => triggerScraper('news')}
                                disabled={scraping.news || !settings.news.enabled}
                                className="flex-1"
                            >
                                {scraping.news ? (
                                    <>
                                        <RefreshCw className="w-4 h-4 mr-2 animate-spin"/>
                                        Scraping...
                                    </>
                                ) : (
                                    <>
                                        <Play className="w-4 h-4 mr-2"/>
                                        Run Now
                                    </>
                                )}
                            </Button>

                            <Button
                                variant="outline"
                                onClick={() => {
                                    setSelectedLogView('news');
                                    setLogDialogOpen(true);
                                    fetchLogs('news');
                                }}
                            >
                                <FileText className="w-4 h-4 mr-2"/>
                                View Logs
                            </Button>
                        </div>
                    </Card>

                    {/* Configuration */}
                    <Card className="p-6">
                        <h3 className="text-lg font-semibold mb-4">Configuration</h3>

                        <div className="space-y-6">
                            {/* Enable/Disable */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Enable News Scraper</Label>
                                    <p className="text-sm text-muted-foreground">
                                        Automatically fetch news articles on schedule
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.news.enabled}
                                    onCheckedChange={(checked) =>
                                        setSettings(prev => ( {
                                            ...prev,
                                            news: {...prev.news, enabled: checked}
                                        } ))
                                    }
                                />
                            </div>

                            {/* Schedule */}
                            <div className="space-y-3">
                                <Label>Schedule Frequency</Label>
                                <Select
                                    value={settings.news.scheduleType}
                                    onValueChange={(value) => {
                                        const day = value === 'weekly' ? '6' : '0';
                                        updateSchedule('news', value, day, settings.news.scheduleTime);
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="hourly">Every Hour</SelectItem>
                                        <SelectItem value="daily">Daily</SelectItem>
                                        <SelectItem value="weekly">Weekly</SelectItem>
                                        <SelectItem value="custom">Custom Cron</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            {settings.news.scheduleType === 'weekly' && (
                                <div className="space-y-3">
                                    <Label>Day of Week</Label>
                                    <Select
                                        value={settings.news.scheduleDay}
                                        onValueChange={(value) =>
                                            updateSchedule('news', 'weekly', value, settings.news.scheduleTime)
                                        }
                                    >
                                        <SelectTrigger>
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="0">Sunday</SelectItem>
                                            <SelectItem value="1">Monday</SelectItem>
                                            <SelectItem value="2">Tuesday</SelectItem>
                                            <SelectItem value="3">Wednesday</SelectItem>
                                            <SelectItem value="4">Thursday</SelectItem>
                                            <SelectItem value="5">Friday</SelectItem>
                                            <SelectItem value="6">Saturday</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            )}

                            {settings.news.scheduleType !== 'hourly' && (
                                <div className="space-y-3">
                                    <Label>Time of Day</Label>
                                    <Input
                                        type="time"
                                        value={settings.news.scheduleTime}
                                        onChange={(e) =>
                                            updateSchedule(
                                                'news',
                                                settings.news.scheduleType,
                                                settings.news.scheduleDay,
                                                e.target.value
                                            )
                                        }
                                    />
                                </div>
                            )}

                            {settings.news.scheduleType === 'custom' && (
                                <div className="space-y-3">
                                    <Label>Cron Expression</Label>
                                    <Input
                                        value={settings.news.schedule}
                                        onChange={(e) =>
                                            setSettings(prev => ( {
                                                ...prev,
                                                news: {...prev.news, schedule: e.target.value}
                                            } ))
                                        }
                                        placeholder="0 1 * * 6"
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Format: minute hour day month weekday
                                    </p>
                                </div>
                            )}

                            {/* Advanced Settings */}
                            <div className="pt-4 border-t">
                                <h4 className="font-medium mb-4">Advanced Settings</h4>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label>Max Articles per Keyword</Label>
                                        <Input
                                            type="number"
                                            min="1"
                                            max="50"
                                            value={settings.news.maxArticles}
                                            onChange={(e) =>
                                                setSettings(prev => ( {
                                                    ...prev,
                                                    news: {...prev.news, maxArticles: parseInt(e.target.value)}
                                                } ))
                                            }
                                        />
                                    </div>

                                    <div className="space-y-2">
                                        <Label>Min Content Length</Label>
                                        <Input
                                            type="number"
                                            min="50"
                                            max="1000"
                                            value={settings.news.minContentLength}
                                            onChange={(e) =>
                                                setSettings(prev => ( {
                                                    ...prev,
                                                    news: {...prev.news, minContentLength: parseInt(e.target.value)}
                                                } ))
                                            }
                                        />
                                    </div>

                                    <div className="space-y-2">
                                        <Label>Relevance Threshold</Label>
                                        <Input
                                            type="number"
                                            step="0.01"
                                            min="0"
                                            max="1"
                                            value={settings.news.relevanceThreshold}
                                            onChange={(e) =>
                                                setSettings(prev => ( {
                                                    ...prev,
                                                    news: {...prev.news, relevanceThreshold: parseFloat(e.target.value)}
                                                } ))
                                            }
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Lower = more articles (0.12 recommended)
                                        </p>
                                    </div>

                                    <div className="space-y-2">
                                        <Label>Scraping Method</Label>
                                        <Select
                                            value={settings.news.method}
                                            onValueChange={(value) =>
                                                setSettings(prev => ( {
                                                    ...prev,
                                                    news: {...prev.news, method: value}
                                                } ))
                                            }
                                        >
                                            <SelectTrigger>
                                                <SelectValue/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="rss">RSS Feeds (Free)</SelectItem>
                                                <SelectItem value="serper">Serper API (Better Quality)</SelectItem>
                                                <SelectItem value="newsapi">NewsAPI</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                            </div>

                            {/* Notifications */}
                            <div className="pt-4 border-t space-y-4">
                                <h4 className="font-medium flex items-center gap-2">
                                    <Bell className="w-4 h-4"/>
                                    Notifications
                                </h4>

                                <div className="flex items-center justify-between">
                                    <Label>Notify on Error</Label>
                                    <Switch
                                        checked={settings.news.notifyOnError}
                                        onCheckedChange={(checked) =>
                                            setSettings(prev => ( {
                                                ...prev,
                                                news: {...prev.news, notifyOnError: checked}
                                            } ))
                                        }
                                    />
                                </div>

                                <div className="flex items-center justify-between">
                                    <Label>Notify on Success</Label>
                                    <Switch
                                        checked={settings.news.notifyOnSuccess}
                                        onCheckedChange={(checked) =>
                                            setSettings(prev => ( {
                                                ...prev,
                                                news: {...prev.news, notifyOnSuccess: checked}
                                            } ))
                                        }
                                    />
                                </div>
                            </div>
                        </div>
                    </Card>
                </TabsContent>

                {/* PROPERTY SCRAPER TAB */}
                <TabsContent value="property" className="space-y-6">
                    {/* Status Card */}
                    <Card className="p-6">
                        <div className="flex items-start justify-between mb-4">
                            <div>
                                <h3 className="text-xl font-semibold flex items-center gap-2 mb-2">
                                    <Home className="w-5 h-5"/>
                                    Property Listings Scraper
                                </h3>
                                <p className="text-sm text-muted-foreground">
                                    Fetches property listings from 4 major real estate sites for Molonglo Valley suburbs
                                </p>
                            </div>
                            <StatusBadge
                                enabled={settings.property.enabled}
                                lastRun={stats.property.lastRun}
                                failedRuns={stats.property.failedRuns}
                                totalRuns={stats.property.totalRuns}
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-6">
                            <div className="space-y-1">
                                <p className="text-sm text-muted-foreground">Last Run</p>
                                <p className="font-medium">{formatDate(stats.property.lastRun)}</p>
                            </div>
                            <div className="space-y-1">
                                <p className="text-sm text-muted-foreground">Next Scheduled</p>
                                <p className="font-medium">{formatDate(getNextRunTime(settings.property.schedule))}</p>
                            </div>
                            <div className="space-y-1">
                                <p className="text-sm text-muted-foreground">Average Listings/Run</p>
                                <p className="font-medium">{stats.property.averageListings}</p>
                            </div>
                        </div>

                        <div className="flex gap-3 mt-6">
                            <Button
                                onClick={() => triggerScraper('property')}
                                disabled={scraping.property || !settings.property.enabled}
                                className="flex-1"
                            >
                                {scraping.property ? (
                                    <>
                                        <RefreshCw className="w-4 h-4 mr-2 animate-spin"/>
                                        Scraping...
                                    </>
                                ) : (
                                    <>
                                        <Play className="w-4 h-4 mr-2"/>
                                        Run Now
                                    </>
                                )}
                            </Button>

                            <Button
                                variant="outline"
                                onClick={() => {
                                    setSelectedLogView('property');
                                    setLogDialogOpen(true);
                                    fetchLogs('property');
                                }}
                            >
                                <FileText className="w-4 h-4 mr-2"/>
                                View Logs
                            </Button>
                        </div>
                    </Card>

                    {/* Configuration */}
                    <Card className="p-6">
                        <h3 className="text-lg font-semibold mb-4">Configuration</h3>

                        <div className="space-y-6">
                            {/* Enable/Disable */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-0.5">
                                    <Label>Enable Property Scraper</Label>
                                    <p className="text-sm text-muted-foreground">
                                        Automatically fetch property listings on schedule
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.property.enabled}
                                    onCheckedChange={(checked) =>
                                        setSettings(prev => ( {
                                            ...prev,
                                            property: {...prev.property, enabled: checked}
                                        } ))
                                    }
                                />
                            </div>

                            {/* Schedule */}
                            <div className="space-y-3">
                                <Label>Schedule Frequency</Label>
                                <Select
                                    value={settings.property.scheduleType}
                                    onValueChange={(value) => {
                                        const day = value === 'weekly' ? '1' : '0';
                                        updateSchedule('property', value, day, settings.property.scheduleTime);
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="hourly">Every Hour</SelectItem>
                                        <SelectItem value="daily">Daily</SelectItem>
                                        <SelectItem value="weekly">Weekly</SelectItem>
                                        <SelectItem value="custom">Custom Cron</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            {settings.property.scheduleType === 'weekly' && (
                                <div className="space-y-3">
                                    <Label>Day of Week</Label>
                                    <Select
                                        value={settings.property.scheduleDay}
                                        onValueChange={(value) =>
                                            updateSchedule('property', 'weekly', value, settings.property.scheduleTime)
                                        }
                                    >
                                        <SelectTrigger>
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="0">Sunday</SelectItem>
                                            <SelectItem value="1">Monday</SelectItem>
                                            <SelectItem value="2">Tuesday</SelectItem>
                                            <SelectItem value="3">Wednesday</SelectItem>
                                            <SelectItem value="4">Thursday</SelectItem>
                                            <SelectItem value="5">Friday</SelectItem>
                                            <SelectItem value="6">Saturday</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            )}

                            {settings.property.scheduleType !== 'hourly' && (
                                <div className="space-y-3">
                                    <Label>Time of Day</Label>
                                    <Input
                                        type="time"
                                        value={settings.property.scheduleTime}
                                        onChange={(e) =>
                                            updateSchedule(
                                                'property',
                                                settings.property.scheduleType,
                                                settings.property.scheduleDay,
                                                e.target.value
                                            )
                                        }
                                    />
                                </div>
                            )}

                            {settings.property.scheduleType === 'custom' && (
                                <div className="space-y-3">
                                    <Label>Cron Expression</Label>
                                    <Input
                                        value={settings.property.schedule}
                                        onChange={(e) =>
                                            setSettings(prev => ( {
                                                ...prev,
                                                property: {...prev.property, schedule: e.target.value}
                                            } ))
                                        }
                                        placeholder="0 6 * * *"
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Format: minute hour day month weekday
                                    </p>
                                </div>
                            )}

                            {/* Advanced Settings */}
                            <div className="pt-4 border-t">
                                <h4 className="font-medium mb-4">Advanced Settings</h4>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label>Max Listings per Suburb</Label>
                                        <Input
                                            type="number"
                                            min="5"
                                            max="50"
                                            value={settings.property.maxListingsPerSuburb}
                                            onChange={(e) =>
                                                setSettings(prev => ( {
                                                    ...prev,
                                                    property: {
                                                        ...prev.property,
                                                        maxListingsPerSuburb: parseInt(e.target.value)
                                                    }
                                                } ))
                                            }
                                        />
                                    </div>

                                    <div className="space-y-2">
                                        <Label>Listing Expiry Days</Label>
                                        <Input
                                            type="number"
                                            min="7"
                                            max="90"
                                            value={settings.property.expiryDays}
                                            onChange={(e) =>
                                                setSettings(prev => ( {
                                                    ...prev,
                                                    property: {...prev.property, expiryDays: parseInt(e.target.value)}
                                                } ))
                                            }
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            Listings auto-expire after this many days
                                        </p>
                                    </div>
                                </div>

                                {/* Suburbs */}
                                <div className="space-y-2 mt-4">
                                    <Label>Target Suburbs</Label>
                                    <div className="flex flex-wrap gap-2">
                                        {settings.property.suburbs.map((suburb, index) => (
                                            <Badge key={index} variant="secondary" className="flex items-center gap-1">
                                                {suburb}
                                                {isAdmin() && (
                                                    <button
                                                        type="button"
                                                        onClick={() =>
                                                            setSettings(prev => ( {
                                                                ...prev,
                                                                property: {
                                                                    ...prev.property,
                                                                    suburbs: prev.property.suburbs.filter((_, i) => i !== index),
                                                                },
                                                            } ))
                                                        }
                                                        className="ml-1 hover:text-destructive focus:outline-none"
                                                        aria-label={`Remove ${suburb}`}
                                                    >
                                                        ×
                                                    </button>
                                                )}
                                            </Badge>
                                        ))}
                                    </div>
                                    {isAdmin() && (
                                        <div className="flex gap-2 mt-2">
                                            <Input
                                                placeholder="Add suburb (e.g. Coombs)"
                                                value={newSuburb}
                                                onChange={(e) => setNewSuburb(e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === 'Enter' && newSuburb.trim()) {
                                                        e.preventDefault();
                                                        const trimmed = newSuburb.trim();
                                                        if (!settings.property.suburbs.includes(trimmed)) {
                                                            setSettings(prev => ( {
                                                                ...prev,
                                                                property: {
                                                                    ...prev.property,
                                                                    suburbs: [...prev.property.suburbs, trimmed]
                                                                },
                                                            } ));
                                                        }
                                                        setNewSuburb('');
                                                    }
                                                }}
                                                className="max-w-xs"
                                            />
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                onClick={() => {
                                                    const trimmed = newSuburb.trim();
                                                    if (trimmed && !settings.property.suburbs.includes(trimmed)) {
                                                        setSettings(prev => ( {
                                                            ...prev,
                                                            property: {
                                                                ...prev.property,
                                                                suburbs: [...prev.property.suburbs, trimmed]
                                                            },
                                                        } ));
                                                    }
                                                    setNewSuburb('');
                                                }}
                                            >
                                                Add
                                            </Button>
                                        </div>
                                    )}
                                </div>

                                {/* Sites */}
                                <div className="space-y-2 mt-4">
                                    <Label>Property Sites</Label>
                                    <div className="flex flex-wrap gap-2">
                                        {( settings.property.sites || [] ).map((site, index) => (
                                            <Badge key={index} variant="secondary" className="flex items-center gap-1">
                                                <Globe className="w-3 h-3"/>
                                                {site}
                                                {isAdmin() && (
                                                    <button
                                                        type="button"
                                                        onClick={() =>
                                                            setSettings(prev => ( {
                                                                ...prev,
                                                                property: {
                                                                    ...prev.property,
                                                                    sites: ( prev.property.sites || [] ).filter((_, i) => i !== index),
                                                                },
                                                            } ))
                                                        }
                                                        className="ml-1 hover:text-destructive focus:outline-none"
                                                        aria-label={`Remove ${site}`}
                                                    >
                                                        ×
                                                    </button>
                                                )}
                                            </Badge>
                                        ))}
                                    </div>
                                    {isAdmin() && (
                                        <div className="flex gap-2 mt-2">
                                            <Input
                                                placeholder="Add site (e.g. realestate.com.au)"
                                                value={newSite}
                                                onChange={(e) => setNewSite(e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === 'Enter' && newSite.trim()) {
                                                        e.preventDefault();
                                                        const trimmed = newSite.trim();
                                                        if (!( settings.property.sites || [] ).includes(trimmed)) {
                                                            setSettings(prev => ( {
                                                                ...prev,
                                                                property: {
                                                                    ...prev.property,
                                                                    sites: [...( prev.property.sites || [] ), trimmed]
                                                                },
                                                            } ));
                                                        }
                                                        setNewSite('');
                                                    }
                                                }}
                                                className="max-w-xs"
                                            />
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                onClick={() => {
                                                    const trimmed = newSite.trim();
                                                    if (trimmed && !( settings.property.sites || [] ).includes(trimmed)) {
                                                        setSettings(prev => ( {
                                                            ...prev,
                                                            property: {
                                                                ...prev.property,
                                                                sites: [...( prev.property.sites || [] ), trimmed]
                                                            },
                                                        } ));
                                                    }
                                                    setNewSite('');
                                                }}
                                            >
                                                Add
                                            </Button>
                                        </div>
                                    )}
                                    <p className="text-xs text-muted-foreground">
                                        Searches across all major real estate portals
                                    </p>
                                </div>
                            </div>

                            {/* Notifications */}
                            <div className="pt-4 border-t space-y-4">
                                <h4 className="font-medium flex items-center gap-2">
                                    <Bell className="w-4 h-4"/>
                                    Notifications
                                </h4>

                                <div className="flex items-center justify-between">
                                    <Label>Notify on Error</Label>
                                    <Switch
                                        checked={settings.property.notifyOnError}
                                        onCheckedChange={(checked) =>
                                            setSettings(prev => ( {
                                                ...prev,
                                                property: {...prev.property, notifyOnError: checked}
                                            } ))
                                        }
                                    />
                                </div>

                                <div className="flex items-center justify-between">
                                    <Label>Notify on Success</Label>
                                    <Switch
                                        checked={settings.property.notifyOnSuccess}
                                        onCheckedChange={(checked) =>
                                            setSettings(prev => ( {
                                                ...prev,
                                                property: {...prev.property, notifyOnSuccess: checked}
                                            } ))
                                        }
                                    />
                                </div>
                            </div>
                        </div>
                    </Card>

                    {/* API Usage Alert */}
                    <Alert>
                        <AlertCircle className="h-4 w-4"/>
                        <AlertDescription>
                            <strong>Serper API Usage:</strong> Daily scraping uses ~32 searches per day (960/month).
                            Free tier limit is 2,500/month. Current usage: 38% of limit.
                        </AlertDescription>
                    </Alert>
                </TabsContent>
            </Tabs>

            {/* Stats Detail Dialog */}
            <Dialog open={statsDialog !== null} onOpenChange={(o) => !o && setStatsDialog(null)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            {statsDialog === 'articles' && <><Newspaper className="w-5 h-5 text-blue-500"/>News Articles
                                Detail</>}
                            {statsDialog === 'listings' && <><Home className="w-5 h-5 text-green-500"/>Property Listings
                                Detail</>}
                            {statsDialog === 'runs' && <><Activity className="w-5 h-5 text-purple-500"/>Scraper Run
                                History</>}
                            {statsDialog === 'successRate' && <><TrendingUp className="w-5 h-5 text-orange-500"/>Success
                                Rate Breakdown</>}
                        </DialogTitle>
                        <DialogDescription>
                            Detailed statistics for the selected metric
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        {statsDialog === 'articles' && (
                            <div className="space-y-3">
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Total scraped articles</span>
                                    <span className="font-bold text-lg">{stats.news.totalArticles}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Avg articles per run</span>
                                    <span className="font-bold">{stats.news.averageArticles}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Last run created</span>
                                    <span className="font-bold">{stats.news.lastRunCount} articles</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Last run</span>
                                    <span className="font-bold text-sm">{formatDate(stats.news.lastRun)}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Scraping method</span>
                                    <Badge variant="outline">{settings.news.method.toUpperCase()}</Badge>
                                </div>
                                <Alert>
                                    <AlertCircle className="h-4 w-4"/>
                                    <AlertDescription className="text-xs">
                                        Articles are created as drafts. Review and publish them from the News/Blog
                                        section.
                                    </AlertDescription>
                                </Alert>
                            </div>
                        )}

                        {statsDialog === 'listings' && (
                            <div className="space-y-3">
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Total active listings</span>
                                    <span className="font-bold text-lg">{stats.property.totalListings}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Avg listings per run</span>
                                    <span className="font-bold">{stats.property.averageListings}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Last run created</span>
                                    <span className="font-bold">{stats.property.lastRunCount} listings</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Last run</span>
                                    <span className="font-bold text-sm">{formatDate(stats.property.lastRun)}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Listing expiry</span>
                                    <span className="font-bold">{settings.property.expiryDays} days</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Target suburbs</span>
                                    <div className="flex flex-wrap gap-1 justify-end max-w-[200px]">
                                        {settings.property.suburbs.slice(0, 3).map(s => (
                                            <Badge key={s} variant="secondary" className="text-xs">{s}</Badge>
                                        ))}
                                        {settings.property.suburbs.length > 3 && (
                                            <Badge variant="secondary"
                                                   className="text-xs">+{settings.property.suburbs.length - 3}</Badge>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {statsDialog === 'runs' && (
                            <div className="space-y-3">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="p-3 bg-blue-500/10 rounded text-center">
                                        <p className="text-xs text-muted-foreground">News Runs</p>
                                        <p className="text-2xl font-bold text-blue-600">{stats.news.totalRuns}</p>
                                    </div>
                                    <div className="p-3 bg-green-500/10 rounded text-center">
                                        <p className="text-xs text-muted-foreground">Property Runs</p>
                                        <p className="text-2xl font-bold text-green-600">{stats.property.totalRuns}</p>
                                    </div>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Total runs (all scrapers)</span>
                                    <span className="font-bold">{stats.news.totalRuns + stats.property.totalRuns}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">News — successful</span>
                                    <span className="font-bold text-green-600">{stats.news.successfulRuns}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">News — failed</span>
                                    <span className="font-bold text-red-600">{stats.news.failedRuns}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Property — successful</span>
                                    <span className="font-bold text-green-600">{stats.property.successfulRuns}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Property — failed</span>
                                    <span className="font-bold text-red-600">{stats.property.failedRuns}</span>
                                </div>
                            </div>
                        )}

                        {statsDialog === 'successRate' && (
                            <div className="space-y-3">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="p-3 bg-blue-500/10 rounded text-center">
                                        <p className="text-xs text-muted-foreground">News Rate</p>
                                        <p className="text-2xl font-bold text-blue-600">{successRates.newsRate}%</p>
                                    </div>
                                    <div className="p-3 bg-green-500/10 rounded text-center">
                                        <p className="text-xs text-muted-foreground">Property Rate</p>
                                        <p className="text-2xl font-bold text-green-600">{successRates.propertyRate}%</p>
                                    </div>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Combined success rate</span>
                                    <span className="font-bold">{successRates.combined}%</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">News last run</span>
                                    <span className="font-bold text-sm">{formatDate(stats.news.lastRun)}</span>
                                </div>
                                <div className="flex justify-between items-center p-3 bg-muted rounded">
                                    <span className="text-sm font-medium">Property last run</span>
                                    <span className="font-bold text-sm">{formatDate(stats.property.lastRun)}</span>
                                </div>
                            </div>
                        )}
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setStatsDialog(null)}>Close</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Log Viewer Dialog */}
            <Dialog open={logDialogOpen} onOpenChange={setLogDialogOpen}>
                <DialogContent className="max-w-4xl max-h-[80vh]">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <FileText className="w-5 h-5"/>
                            {selectedLogView === 'news' ? 'News Scraper' : 'Property Scraper'} Logs
                        </DialogTitle>
                        <DialogDescription>
                            Recent execution logs and output
                        </DialogDescription>
                    </DialogHeader>

                    <ScrollArea className="h-[400px] w-full rounded-md border p-4">
                        {logs[ selectedLogView ].length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                                <FileText className="w-12 h-12 mb-2 opacity-50"/>
                                <p>No logs available yet</p>
                                <p className="text-sm">Logs will appear after the first scraper run</p>
                            </div>
                        ) : (
                            <div className="space-y-1 font-mono text-xs">
                                {logs[ selectedLogView ].map((log, index) => {
                                    const isStr = typeof log === 'string';
                                    const msg = isStr ? log : ( log.message || '' );
                                    const ts = isStr ? '' : ( log.timestamp || '' );
                                    const lvl = isStr
                                        ? ( msg.includes('ERROR') ? 'ERROR' : msg.includes('WARNING') ? 'WARNING' : 'INFO' )
                                        : ( log.level || 'INFO' );
                                    return (
                                        <div
                                            key={index}
                                            className={`p-1.5 rounded ${
                                                lvl === 'ERROR' ? 'bg-red-500/10 text-red-700 dark:text-red-400' :
                                                    lvl === 'WARNING' ? 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400' :
                                                        msg.includes('✓') ? 'bg-green-500/10 text-green-700 dark:text-green-400' :
                                                            'bg-muted text-foreground'
                                            }`}
                                        >
                                            {ts && <span className="text-muted-foreground mr-2 text-xs">{ts}</span>}
                                            <span>{msg}</span>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </ScrollArea>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setLogDialogOpen(false)}>
                            Close
                        </Button>
                        <Button onClick={() => fetchLogs(selectedLogView)}>
                            <RefreshCw className="w-4 h-4 mr-2"/>
                            Refresh
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default ScraperSettingsPage;
