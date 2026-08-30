// @ts-nocheck
"use client";

import {Suspense, useEffect, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from "@/components/ui/select";
import {PageHeader} from "@/components/shared/PageHeader";
import {Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {AlertTriangle, Building2, DollarSign, Map, RefreshCw, Search, TrendingUp} from "lucide-react";

const RISK_COLORS: Record<string, string> = {
    High: "bg-rose-100 text-rose-700",
    Moderate: "bg-amber-100 text-amber-700",
    "Low-Moderate": "bg-yellow-100 text-yellow-700",
    "Low\u2013Moderate": "bg-yellow-100 text-yellow-700",
    Low: "bg-emerald-100 text-emerald-700",
    "Very Low": "bg-muted text-muted-foreground",
    Stable: "bg-emerald-100 text-emerald-700",
    Critical: "bg-red-200 text-red-800",
    "Very High": "bg-rose-200 text-rose-800",
};

const DISTRICT_COLORS = ["#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626", "#0891b2", "#6d28d9", "#c026d3"];
/**
 * @generated FunctionHeader
 * Function: RiskBadge
 * Path: frontend/src/app/(app)/intelligence/market/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function RiskBadge({value}: { value: string }) {
    const cls = RISK_COLORS[value] || "bg-muted text-foreground";
    return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{value}</span>;
}
/**
 * @generated FunctionHeader
 * Function: MarketIntelligenceDashboard
 * Path: frontend/src/app/(app)/intelligence/market/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function MarketIntelligenceDashboard() {
    const {api} = useAuth() as any;

    const [districts, setDistricts] = useState<any[]>([]);
    const [suburbs, setSuburbs] = useState<any[]>([]);
    const [clusters, setClusters] = useState<any[]>([]);
    const [tam, setTam] = useState<any>(null);
    const [trueCost, setTrueCost] = useState<any[]>([]);
    const [stress, setStress] = useState<any[]>([]);
    const [growthMap, setGrowthMap] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState("");
    const [filterDistrict, setFilterDistrict] = useState("all");
    const [activeTab, setActiveTab] = useState("overview");

    useEffect(() => {
        fetchAll();
    }, []);
    /**
     * @generated FunctionHeader
     * Function: fetchAll
     * Path: frontend/src/app/(app)/intelligence/market/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchAll = async () => {
        setLoading(true);
        try {
            const [distRes, subRes, clusterRes, tamRes, tcRes, stressRes, growthRes] =
                await Promise.allSettled([
                    api.get("/market-intelligence/districts"),
                    api.get("/market-intelligence/suburbs?limit=120"),
                    api.get("/market-intelligence/clusters?top_n=25"),
                    api.get("/market-intelligence/tam"),
                    api.get("/market-intelligence/true-cost"),
                    api.get("/market-intelligence/stress-predictor"),
                    api.get("/market-intelligence/growth-map"),
                ]);
            if (distRes.status === "fulfilled") setDistricts(distRes.value.data);
            if (subRes.status === "fulfilled") setSuburbs(subRes.value.data);
            if (clusterRes.status === "fulfilled") setClusters(clusterRes.value.data);
            if (tamRes.status === "fulfilled") setTam(tamRes.value.data);
            if (tcRes.status === "fulfilled") setTrueCost(tcRes.value.data);
            if (stressRes.status === "fulfilled") setStress(stressRes.value.data);
            if (growthRes.status === "fulfilled") setGrowthMap(growthRes.value.data);
        } finally {
            setLoading(false);
        }
    };

    const uniqueDistricts = Array.from(new Set(suburbs.map((s: any) => s.district))).filter(Boolean).sort() as string[];

    const filteredSuburbs = suburbs.filter((s: any) => {
        const matchSearch = !searchTerm || s.suburb.toLowerCase().includes(searchTerm.toLowerCase());
        const matchDistrict = filterDistrict === "all" || s.district === filterDistrict;
        return matchSearch && matchDistrict;
    });

    if (loading) return (
        <div className="p-8 text-center text-muted-foreground">
            Loading ACT Market Intelligence...
        </div>
    );

    const totalBuildings = districts.reduce((a: number, d: any) => a + (d.total_buildings || 0), 0);
    const totalStrata = districts.reduce((a: number, d: any) => a + (d.total_strata || 0), 0);
    const tamTotal = tam?.totals?.tam_gross || 0;
    const platformTam = tam?.totals?.platform_tam || 0;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex justify-between items-start">
                <PageHeader
                    className="flex-1 border-b-0 pb-0"
                    title="ACT Strata Market Intelligence"
                    icon={<Map className="h-5 w-5"/>}
                    description={
                        <>
                            Suburb-level analytics across {suburbs.length} ACT localities
                            &nbsp;&middot;&nbsp;{totalBuildings.toLocaleString()} strata buildings
                            &nbsp;&middot;&nbsp;{totalStrata.toLocaleString()} strata dwellings
                        </>
                    }
                />
                <Button variant="outline" size="sm" onClick={fetchAll} className="flex items-center gap-2">
                    <RefreshCw className="h-4 w-4"/> Refresh
                </Button>
            </div>

            {/* KPI Cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Total Strata Dwellings</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">{totalStrata.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">ACT-wide strata stock</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">Strata Buildings</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">{totalBuildings.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">Schemes across ACT</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center gap-1">
                            <DollarSign className="h-3 w-3"/>Management TAM
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">${(tamTotal / 1e6).toFixed(0)}M</div>
                        <p className="text-xs text-muted-foreground">@ $80k/bldg/yr</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center gap-1">
                            <TrendingUp className="h-3 w-3"/>Platform TAM
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">${(platformTam / 1e6).toFixed(1)}M</div>
                        <p className="text-xs text-muted-foreground">@ $120/unit/yr</p>
                    </CardContent>
                </Card>
            </div>

            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="flex flex-wrap gap-1 h-auto">
                    <TabsTrigger value="overview">Density Overview</TabsTrigger>
                    <TabsTrigger value="clusters">Largest Clusters</TabsTrigger>
                    <TabsTrigger value="tam">TAM Analysis</TabsTrigger>
                    <TabsTrigger value="truecost">True Cost</TabsTrigger>
                    <TabsTrigger value="stress">Stress Predictor</TabsTrigger>
                    <TabsTrigger value="growth">Growth Map</TabsTrigger>
                </TabsList>

                {/* ── TAB 1: Density Overview ─────────────────────── */}
                <TabsContent value="overview" className="space-y-6 pt-4">
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <Card>
                            <CardHeader>
                                <CardTitle>Strata Dwellings by District</CardTitle>
                                <CardDescription>Total strata stock per ACT district</CardDescription>
                            </CardHeader>
                            <CardContent className="h-[300px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={districts} layout="vertical">
                                        <CartesianGrid strokeDasharray="3 3" horizontal={false}/>
                                        <XAxis type="number"
                                               tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)}/>
                                        <YAxis type="category" dataKey="district" width={140} tick={{fontSize: 11}}/>
                                        <Tooltip formatter={(v: any) => [v.toLocaleString(), "Strata Dwellings"]}/>
                                        <Bar dataKey="total_strata" fill="#2563eb" radius={[0, 4, 4, 0]}/>
                                    </BarChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>District TAM Share</CardTitle>
                                <CardDescription>Total Addressable Market split</CardDescription>
                            </CardHeader>
                            <CardContent className="h-[300px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie
                                            data={districts}
                                            dataKey="tam_total"
                                            nameKey="district"
                                            cx="50%"
                                            cy="50%"
                                            outerRadius={100}
                                            label={({
                                                        district,
                                                        percent
                                                    }: any) => `${(district || '').split(' ')[0]} ${(percent * 100).toFixed(0)}%`}
                                            labelLine={false}
                                        >
                                            {districts.map((_: any, i: number) => (
                                                <Cell key={i} fill={DISTRICT_COLORS[i % DISTRICT_COLORS.length]}/>
                                            ))}
                                        </Pie>
                                        <Tooltip formatter={(v: any) => [`$${(v / 1e6).toFixed(1)}M`, "TAM"]}/>
                                    </PieChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Suburb density table */}
                    <Card>
                        <CardHeader>
                            <div
                                className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
                                <div>
                                    <CardTitle>Suburb Strata Density</CardTitle>
                                    <CardDescription>All {suburbs.length} ACT localities ranked by strata
                                        density</CardDescription>
                                </div>
                                <div className="flex gap-2">
                                    <div className="relative">
                                        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"/>
                                        <Input
                                            placeholder="Search suburb..."
                                            className="pl-8 w-40"
                                            value={searchTerm}
                                            onChange={e => setSearchTerm(e.target.value)}
                                        />
                                    </div>
                                    <Select value={filterDistrict} onValueChange={setFilterDistrict}>
                                        <SelectTrigger className="w-40">
                                            <SelectValue placeholder="All districts"/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">All Districts</SelectItem>
                                            {uniqueDistricts.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <div className="max-h-[450px] overflow-y-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Suburb</TableHead>
                                            <TableHead>District</TableHead>
                                            <TableHead className="text-right">Strata Dwellings</TableHead>
                                            <TableHead className="text-right">Buildings</TableHead>
                                            <TableHead className="text-right">Strata %</TableHead>
                                            <TableHead>Concentration</TableHead>
                                            <TableHead>Growth</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {filteredSuburbs.map((s: any) => (
                                            <TableRow key={s.suburb_id || s.suburb}>
                                                <TableCell className="font-medium">{s.suburb}</TableCell>
                                                <TableCell
                                                    className="text-muted-foreground text-sm">{s.district}</TableCell>
                                                <TableCell
                                                    className="text-right font-mono">{(s.strata_dwellings || 0).toLocaleString()}</TableCell>
                                                <TableCell className="text-right">{s.strata_buildings || 0}</TableCell>
                                                <TableCell
                                                    className="text-right">{s.strata_pct ? `${(s.strata_pct * 100).toFixed(0)}%` : '—'}</TableCell>
                                                <TableCell><RiskBadge value={s.units_concentration || '—'}/></TableCell>
                                                <TableCell>
                          <span
                              className={`text-xs font-medium ${s.growth_pressure === 'High' ? 'text-rose-600' : s.growth_pressure === 'Moderate' ? 'text-amber-600' : 'text-muted-foreground'}`}>
                            {s.growth_pressure || '—'}
                          </span>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── TAB 2: Largest Clusters ────────────────────── */}
                <TabsContent value="clusters" className="space-y-4 pt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Building2 className="h-5 w-5 text-primary"/>
                                Top 25 Strata Clusters
                            </CardTitle>
                            <CardDescription>Suburbs with the highest concentration of strata buildings and
                                dwellings</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="mb-4 h-[250px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={clusters.slice(0, 12)}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                        <XAxis dataKey="suburb" tick={{fontSize: 10}} interval={0} angle={-35}
                                               textAnchor="end" height={60}/>
                                        <YAxis
                                            tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)}/>
                                        <Tooltip
                                            formatter={(v: any, name: string) => [v.toLocaleString(), name === 'strata_dwellings' ? 'Strata Dwellings' : 'Buildings']}/>
                                        <Bar dataKey="strata_dwellings" fill="#2563eb" name="strata_dwellings"
                                             radius={[4, 4, 0, 0]}/>
                                        <Bar dataKey="strata_buildings" fill="#7c3aed" name="strata_buildings"
                                             radius={[4, 4, 0, 0]}/>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>#</TableHead>
                                        <TableHead>Suburb</TableHead>
                                        <TableHead>District</TableHead>
                                        <TableHead className="text-right">Strata Dwellings</TableHead>
                                        <TableHead className="text-right">Buildings</TableHead>
                                        <TableHead className="text-right">Avg Size</TableHead>
                                        <TableHead>Concentration</TableHead>
                                        <TableHead className="text-right">TAM Est.</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {clusters.map((s: any, i: number) => (
                                        <TableRow key={s.suburb}>
                                            <TableCell className="font-bold text-muted-foreground">{i + 1}</TableCell>
                                            <TableCell className="font-medium">{s.suburb}</TableCell>
                                            <TableCell
                                                className="text-sm text-muted-foreground">{s.district}</TableCell>
                                            <TableCell
                                                className="text-right font-mono">{(s.strata_dwellings || 0).toLocaleString()}</TableCell>
                                            <TableCell className="text-right">{s.strata_buildings || 0}</TableCell>
                                            <TableCell
                                                className="text-right">{s.avg_strata_building_size || '—'}</TableCell>
                                            <TableCell><RiskBadge value={s.units_concentration || '—'}/></TableCell>
                                            <TableCell
                                                className="text-right text-emerald-700 font-mono">${((s.tam_estimate || 0) / 1000).toFixed(0)}k</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── TAB 3: TAM Analysis ───────────────────────── */}
                <TabsContent value="tam" className="space-y-4 pt-4">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <Card className="border-primary/20 bg-primary/10/30">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-primary">Management TAM</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="text-3xl font-bold text-primary">${(tamTotal / 1e6).toFixed(0)}M</div>
                                <p className="text-xs text-primary mt-1">{(tam?.totals?.total_strata_buildings || 0).toLocaleString()} buildings &times; $80k/yr</p>
                            </CardContent>
                        </Card>
                        <Card className="border-primary/20 bg-primary/10/30">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-primary">Platform TAM</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="text-3xl font-bold text-primary">${(platformTam / 1e6).toFixed(1)}M
                                </div>
                                <p className="text-xs text-primary mt-1">{(tam?.totals?.total_strata_dwellings || 0).toLocaleString()} units &times; $120/yr</p>
                            </CardContent>
                        </Card>
                        <Card className="border-emerald-200 bg-emerald-50/30">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-emerald-800">ACT Strata Universe</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div
                                    className="text-3xl font-bold text-emerald-900">{(tam?.totals?.total_strata_dwellings || 0).toLocaleString()}</div>
                                <p className="text-xs text-emerald-700 mt-1">Addressable strata dwellings</p>
                            </CardContent>
                        </Card>
                    </div>

                    <Card>
                        <CardHeader>
                            <CardTitle>TAM by District</CardTitle>
                            <CardDescription>Management fee TAM and platform subscription TAM per
                                district</CardDescription>
                        </CardHeader>
                        <CardContent className="h-[300px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={(tam?.by_district || []).slice(0, 8)}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                    <XAxis dataKey="district" tick={{fontSize: 10}} interval={0} angle={-20}
                                           textAnchor="end" height={50}/>
                                    <YAxis tickFormatter={(v: number) => `$${(v / 1e6).toFixed(0)}M`}/>
                                    <Tooltip formatter={(v: any) => [`$${(v / 1e6).toFixed(2)}M`, "Management TAM"]}/>
                                    <Bar dataKey="tam_estimate" fill="#2563eb" radius={[4, 4, 0, 0]}
                                         name="tam_estimate"/>
                                </BarChart>
                            </ResponsiveContainer>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>Top 10 High-Impact Suburbs</CardTitle>
                            <CardDescription>Capturing these suburbs would add the most value to the
                                platform</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>#</TableHead>
                                        <TableHead>Suburb</TableHead>
                                        <TableHead>District</TableHead>
                                        <TableHead className="text-right">Buildings</TableHead>
                                        <TableHead className="text-right">Dwellings</TableHead>
                                        <TableHead className="text-right">TAM</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {(tam?.top_suburbs || []).map((s: any, i: number) => (
                                        <TableRow key={s.suburb}>
                                            <TableCell className="font-bold text-muted-foreground">{i + 1}</TableCell>
                                            <TableCell className="font-medium">{s.suburb}</TableCell>
                                            <TableCell
                                                className="text-sm text-muted-foreground">{s.district}</TableCell>
                                            <TableCell className="text-right">{s.strata_buildings}</TableCell>
                                            <TableCell
                                                className="text-right font-mono">{(s.strata_dwellings || 0).toLocaleString()}</TableCell>
                                            <TableCell
                                                className="text-right text-emerald-700 font-semibold font-mono">${((s.tam_estimate || 0) / 1e3).toFixed(0)}k</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── TAB 4: True Cost ──────────────────────────── */}
                <TabsContent value="truecost" className="space-y-4 pt-4">
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
                        <strong>True Cost of Ownership by Suburb</strong> — Composite risk assessment of levy exposure,
                        maintenance costs and capital shock vulnerability. Owners rarely see this type of insight
                        anywhere.
                    </div>
                    <Card>
                        <CardHeader>
                            <CardTitle>Suburb True Cost Matrix</CardTitle>
                            <CardDescription>Levy risk, maintenance risk and capital shock risk by
                                suburb</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="max-h-[500px] overflow-y-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Suburb</TableHead>
                                            <TableHead>District</TableHead>
                                            <TableHead>Avg Levies</TableHead>
                                            <TableHead>Maintenance Risk</TableHead>
                                            <TableHead>Capital Shock</TableHead>
                                            <TableHead>Unit Price</TableHead>
                                            <TableHead>Yield</TableHead>
                                            <TableHead>Vacancy</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {trueCost.map((s: any) => (
                                            <TableRow key={s.suburb}>
                                                <TableCell className="font-medium">{s.suburb}</TableCell>
                                                <TableCell
                                                    className="text-xs text-muted-foreground">{s.district}</TableCell>
                                                <TableCell><RiskBadge value={s.avg_levies}/></TableCell>
                                                <TableCell><RiskBadge value={s.maintenance_risk}/></TableCell>
                                                <TableCell><RiskBadge value={s.capital_shock_risk}/></TableCell>
                                                <TableCell
                                                    className="text-xs">{s.indicative_unit_price || '—'}</TableCell>
                                                <TableCell className="text-xs">{s.gross_yield || '—'}</TableCell>
                                                <TableCell>
                          <span
                              className={`text-xs ${s.vacancy_risk === 'High' ? 'text-rose-600' : s.vacancy_risk === 'Moderate' ? 'text-amber-600' : 'text-muted-foreground'}`}>
                            {s.vacancy_risk || '—'}
                          </span>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── TAB 5: Stress Predictor ───────────────────── */}
                <TabsContent value="stress" className="space-y-4 pt-4">
                    <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-sm text-rose-800">
                        <strong>Strata Financial Stress Predictor</strong> — Identifies suburbs most likely to face
                        special levies, underfunded sinking funds, and high capital expenditure risk. Extremely powerful
                        for committees considering expansion or investment.
                    </div>
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <AlertTriangle className="h-5 w-5 text-rose-500"/>
                                Financial Stress Ranking
                            </CardTitle>
                            <CardDescription>Suburbs sorted by overall financial stress level</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="max-h-[500px] overflow-y-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Suburb</TableHead>
                                            <TableHead>District</TableHead>
                                            <TableHead>Overall Stress</TableHead>
                                            <TableHead>Special Levy Risk</TableHead>
                                            <TableHead>Sinking Fund Risk</TableHead>
                                            <TableHead>CapEx Risk</TableHead>
                                            <TableHead className="text-right">Buildings</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {stress.map((s: any) => (
                                            <TableRow key={s.suburb}>
                                                <TableCell className="font-medium">{s.suburb}</TableCell>
                                                <TableCell
                                                    className="text-xs text-muted-foreground">{s.district}</TableCell>
                                                <TableCell><RiskBadge value={s.overall_stress}/></TableCell>
                                                <TableCell><RiskBadge value={s.special_levy_risk}/></TableCell>
                                                <TableCell><RiskBadge value={s.sinking_fund_risk}/></TableCell>
                                                <TableCell><RiskBadge value={s.capex_risk}/></TableCell>
                                                <TableCell className="text-right">{s.strata_buildings || 0}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── TAB 6: Growth Map ─────────────────────────── */}
                <TabsContent value="growth" className="space-y-4 pt-4">
                    <div className="bg-primary/10 border border-primary/20 rounded-lg p-4 text-sm text-primary">
                        <strong>Platform Growth Strategy</strong> — High-growth ACT suburbs where strata platform
                        adoption would spread fastest. Capturing a handful of large buildings in these areas could bring
                        thousands of users.
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <Card>
                            <CardHeader>
                                <CardTitle>Top Districts for Expansion</CardTitle>
                                <CardDescription>Districts with most high-growth strata suburbs</CardDescription>
                            </CardHeader>
                            <CardContent className="h-[280px]">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={growthMap?.top_districts || []}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                        <XAxis dataKey="district" tick={{fontSize: 10}} interval={0} angle={-20}
                                               textAnchor="end" height={50}/>
                                        <YAxis/>
                                        <Tooltip/>
                                        <Bar dataKey="strata_buildings" fill="#059669" radius={[4, 4, 0, 0]}
                                             name="Buildings"/>
                                    </BarChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>High-Growth Suburbs Ranked</CardTitle>
                                <CardDescription>By strata dwelling count where growth pressure =
                                    "High"</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-2 max-h-[260px] overflow-y-auto">
                                    {(growthMap?.high_growth_suburbs || []).map((s: any, i: number) => (
                                        <div key={s.suburb}
                                             className="flex items-center justify-between border-b pb-2 last:border-0">
                                            <div className="flex items-center gap-2">
                                                <span className="text-sm font-bold text-primary w-5">{i + 1}</span>
                                                <div>
                                                    <div className="font-medium text-sm">{s.suburb}</div>
                                                    <div className="text-xs text-muted-foreground">{s.district}</div>
                                                </div>
                                            </div>
                                            <div className="text-right">
                                                <div
                                                    className="text-sm font-mono font-bold">{(s.strata_dwellings || 0).toLocaleString()}</div>
                                                <div
                                                    className="text-xs text-muted-foreground">{s.strata_buildings} buildings
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </CardContent>
                        </Card>
                    </div>

                    <Card>
                        <CardHeader><CardTitle>High-Growth Suburb Detail</CardTitle></CardHeader>
                        <CardContent>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>#</TableHead>
                                        <TableHead>Suburb</TableHead>
                                        <TableHead>District</TableHead>
                                        <TableHead className="text-right">Dwellings</TableHead>
                                        <TableHead className="text-right">Buildings</TableHead>
                                        <TableHead>Concentration</TableHead>
                                        <TableHead className="text-right">TAM Est.</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {(growthMap?.high_growth_suburbs || []).map((s: any, i: number) => (
                                        <TableRow key={s.suburb}>
                                            <TableCell className="font-bold text-muted-foreground">{i + 1}</TableCell>
                                            <TableCell className="font-medium">{s.suburb}</TableCell>
                                            <TableCell
                                                className="text-sm text-muted-foreground">{s.district}</TableCell>
                                            <TableCell
                                                className="text-right font-mono">{(s.strata_dwellings || 0).toLocaleString()}</TableCell>
                                            <TableCell className="text-right">{s.strata_buildings || 0}</TableCell>
                                            <TableCell><RiskBadge value={s.units_concentration || '—'}/></TableCell>
                                            <TableCell
                                                className="text-right text-emerald-700 font-mono font-semibold">${((s.tam_estimate || 0) / 1e3).toFixed(0)}k</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/intelligence/market/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <Suspense>
            <MarketIntelligenceDashboard/>
        </Suspense>
    );
}
