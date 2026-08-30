// @ts-nocheck
"use client";
import React, {useState} from "react";
import CouncilRatesCard from "@/components/dashboard/CouncilRatesCard";
import LandTaxCard from "@/components/dashboard/LandTaxCard";
import WaterBillCard from "@/components/dashboard/WaterBillCard";
import ElectricityCard from "@/components/dashboard/ElectricityCard";
import GasCard from "@/components/dashboard/GasCard";
import NBNConnectionCard from "@/components/dashboard/NBNConnectionCard";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger
} from "@/components/ui/dialog";
import {useAuth} from "@/contexts/AuthContext";
import {useActiveUnit} from "@/hooks/useActiveUnit";
import {toast} from "sonner";
import {Droplets, Flame, Plus, Zap} from "lucide-react";
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/council-rates/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    const {api} = useAuth();
    // Every card and every reading on this page is per-unit; scope them to the
    // sidebar's active unit rather than the account default (GAP-IDENTITY-UNIT-SWITCH-001).
    const {activeUnit} = useActiveUnit();
    const [loading, setLoading] = useState(false);
    const [open, setOpen] = useState(false);

    // Per-card dialog state: null | 'electricity' | 'gas' | 'water'
    const [cardDialog, setCardDialog] = useState<string | null>(null);
    const [cardLoading, setCardLoading] = useState(false);

    const [formData, setFormData] = useState({
        month: new Date().toISOString().slice(0, 7),
        electricity_kwh: "",
        water_kl: "",
        gas_mj: ""
    });

    const [cardFormData, setCardFormData] = useState({
        month: new Date().toISOString().slice(0, 7),
        value: "",
    });
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/app/(app)/financials/council-rates/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!activeUnit) return;

        setLoading(true);
        try {
            await api.post("/analytics/utility-usage", {
                unit_number: activeUnit,
                month: formData.month,
                electricity_kwh: parseFloat(formData.electricity_kwh) || 0,
                water_kl: parseFloat(formData.water_kl) || 0,
                gas_mj: parseFloat(formData.gas_mj) || 0
            });
            toast.success("Usage data recorded successfully");
            setOpen(false);
        } catch (err) {
            console.error(err);
            toast.error("Failed to save usage data");
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCardSubmit
     * Path: frontend/src/app/(app)/financials/council-rates/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCardSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!activeUnit || !cardDialog) return;
        setCardLoading(true);
        try {
            const payload: any = {
                unit_number: activeUnit,
                month: cardFormData.month,
                electricity_kwh: 0,
                water_kl: 0,
                gas_mj: 0,
            };
            if (cardDialog === 'electricity') payload.electricity_kwh = parseFloat(cardFormData.value) || 0;
            else if (cardDialog === 'gas') payload.gas_mj = parseFloat(cardFormData.value) || 0;
            else if (cardDialog === 'water') payload.water_kl = parseFloat(cardFormData.value) || 0;

            await api.post("/analytics/utility-usage", payload);
            toast.success("Usage data saved successfully");
            setCardDialog(null);
            setCardFormData({month: new Date().toISOString().slice(0, 7), value: ""});
        } catch (err) {
            console.error(err);
            toast.error("Failed to save usage data");
        } finally {
            setCardLoading(false);
        }
    };

    const cardDialogConfig: Record<string, {
        title: string;
        label: string;
        unit: string;
        placeholder: string;
        icon: React.ReactNode
    }> = {
        electricity: {
            title: "Record Electricity Usage",
            label: "Electricity Consumed",
            unit: "kWh",
            placeholder: "e.g. 450.5",
            icon: <Zap size={14} className="text-amber-500"/>
        },
        gas: {
            title: "Record Gas Usage",
            label: "Gas Consumed",
            unit: "MJ",
            placeholder: "e.g. 350",
            icon: <Flame size={14} className="text-orange-500"/>
        },
        water: {
            title: "Record Water Usage",
            label: "Water Consumed",
            unit: "kL",
            placeholder: "e.g. 12.5",
            icon: <Droplets size={14} className="text-blue-500"/>
        },
    };

    return (
        <div className="p-6 space-y-6">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900">Utilities &amp; Property Services</h1>
                    <p className="text-sm text-slate-500 mt-1">
                        Council rates, water bills, electricity, gas, and NBN connection details for your unit.
                    </p>
                </div>

                <Dialog open={open} onOpenChange={setOpen}>
                    <DialogTrigger asChild>
                        <Button className="rounded-xl shadow-lg shadow-indigo-200 gap-2">
                            <Plus size={16}/> Record Monthly Usage
                        </Button>
                    </DialogTrigger>
                    <DialogContent className="sm:max-w-[425px] rounded-[1.5rem]">
                        <DialogHeader>
                            <DialogTitle>Record Utility Usage</DialogTitle>
                            <DialogDescription>
                                Enter your meter readings for the current month to track efficiency.
                            </DialogDescription>
                        </DialogHeader>
                        <form onSubmit={handleSubmit} className="space-y-4 py-4">
                            <div className="space-y-2">
                                <Label htmlFor="month">Month</Label>
                                <Input
                                    id="month"
                                    type="month"
                                    value={formData.month}
                                    onChange={(e) => setFormData({...formData, month: e.target.value})}
                                    required
                                />
                            </div>
                            <div className="grid grid-cols-1 gap-4">
                                <div className="space-y-2">
                                    <Label htmlFor="elec" className="flex items-center gap-2">
                                        <Zap size={14} className="text-amber-500"/> Electricity (kWh)
                                    </Label>
                                    <Input
                                        id="elec"
                                        type="number"
                                        step="0.01"
                                        placeholder="e.g. 450.5"
                                        value={formData.electricity_kwh}
                                        onChange={(e) => setFormData({...formData, electricity_kwh: e.target.value})}
                                        required
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="water" className="flex items-center gap-2">
                                        <Droplets size={14} className="text-blue-500"/> Water (kL)
                                    </Label>
                                    <Input
                                        id="water"
                                        type="number"
                                        step="0.01"
                                        placeholder="e.g. 12.5"
                                        value={formData.water_kl}
                                        onChange={(e) => setFormData({...formData, water_kl: e.target.value})}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="gas" className="flex items-center gap-2">
                                        <Flame size={14} className="text-orange-500"/> Gas (MJ)
                                    </Label>
                                    <Input
                                        id="gas"
                                        type="number"
                                        step="0.01"
                                        placeholder="e.g. 350"
                                        value={formData.gas_mj}
                                        onChange={(e) => setFormData({...formData, gas_mj: e.target.value})}
                                    />
                                </div>
                            </div>
                            <DialogFooter className="pt-4">
                                <Button type="submit" className="w-full" disabled={loading}>
                                    {loading ? "Saving..." : "Save Usage Record"}
                                </Button>
                            </DialogFooter>
                        </form>
                    </DialogContent>
                </Dialog>
            </div>

            {/* Rates & Taxes */}
            <div className="space-y-6">
                <div>
                    <h2 className="text-base font-semibold text-slate-700 mb-3">Rates &amp; Property Taxes</h2>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <CouncilRatesCard unitNumber={activeUnit}/>
                        <LandTaxCard unitNumber={activeUnit}/>
                    </div>
                </div>

                <div>
                    <h2 className="text-base font-semibold text-slate-700 mb-3">Water Services</h2>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <div className="space-y-2">
                            <WaterBillCard unitNumber={activeUnit}/>
                            <Button size="sm" variant="outline" className="w-full gap-2"
                                    onClick={() => setCardDialog('water')}>
                                <Droplets size={14} className="text-blue-500"/> Enter Water Reading
                            </Button>
                        </div>
                    </div>
                </div>
            </div>

            {/* Utilities */}
            <div>
                <h2 className="text-base font-semibold text-slate-700 mb-3">Utilities</h2>
                <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
                    <div className="space-y-2">
                        <ElectricityCard unitNumber={activeUnit}/>
                        <Button size="sm" variant="outline" className="w-full gap-2"
                                onClick={() => setCardDialog('electricity')}>
                            <Zap size={14} className="text-amber-500"/> Enter Electricity Reading
                        </Button>
                    </div>
                    <div className="space-y-2">
                        <GasCard unitNumber={activeUnit}/>
                        <Button size="sm" variant="outline" className="w-full gap-2"
                                onClick={() => setCardDialog('gas')}>
                            <Flame size={14} className="text-orange-500"/> Enter Gas Reading
                        </Button>
                    </div>
                    <div className="space-y-2">
                        <NBNConnectionCard unitNumber={activeUnit}/>
                    </div>
                </div>
            </div>

            {/* Per-card utility entry dialog */}
            {cardDialog && cardDialogConfig[cardDialog] && (
                <Dialog open={!!cardDialog} onOpenChange={(o) => !o && setCardDialog(null)}>
                    <DialogContent className="sm:max-w-[425px] rounded-[1.5rem]">
                        <DialogHeader>
                            <DialogTitle>{cardDialogConfig[cardDialog].title}</DialogTitle>
                            <DialogDescription>
                                Enter your meter reading for Unit {activeUnit} to track efficiency and compare
                                with building averages.
                            </DialogDescription>
                        </DialogHeader>
                        <form onSubmit={handleCardSubmit} className="space-y-4 py-4">
                            <div className="space-y-2">
                                <Label htmlFor="card-month">Month</Label>
                                <Input
                                    id="card-month"
                                    type="month"
                                    value={cardFormData.month}
                                    onChange={(e) => setCardFormData({...cardFormData, month: e.target.value})}
                                    required
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="card-value" className="flex items-center gap-2">
                                    {cardDialogConfig[cardDialog].icon}
                                    {cardDialogConfig[cardDialog].label} ({cardDialogConfig[cardDialog].unit})
                                </Label>
                                <Input
                                    id="card-value"
                                    type="number"
                                    step="0.01"
                                    min="0"
                                    placeholder={cardDialogConfig[cardDialog].placeholder}
                                    value={cardFormData.value}
                                    onChange={(e) => setCardFormData({...cardFormData, value: e.target.value})}
                                    required
                                />
                            </div>
                            <DialogFooter className="pt-4">
                                <Button type="button" variant="outline"
                                        onClick={() => setCardDialog(null)}>Cancel</Button>
                                <Button type="submit" disabled={cardLoading}>
                                    {cardLoading ? "Saving..." : "Save Reading"}
                                </Button>
                            </DialogFooter>
                        </form>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
}
