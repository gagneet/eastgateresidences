// @ts-nocheck
"use client";
import React, {useState} from 'react';
import {NavItem, NavMode, useNavigation} from '../../contexts/NavigationContext';
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle} from '../ui/sheet';
import {Button} from '../ui/button';
import {Switch} from '../ui/switch';
import {Label} from '../ui/label';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger
} from '../ui/alert-dialog';
import * as LucideIcons from 'lucide-react';
import {ArrowDown, ArrowUp, LayoutDashboard, X} from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: ItemIcon
 * Path: frontend/src/components/layout/NavCustomiseDrawer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ItemIcon({name}: { name: string }) {
    const Icon = (LucideIcons as Record<string, React.ComponentType<{ className?: string }>>)[name];
    if (!Icon) {
        const {LayoutDashboard} = LucideIcons;
        return <LayoutDashboard className="h-4 w-4 text-slate-600"/>;
    }
    return <Icon className="h-4 w-4 text-slate-600"/>;
}

interface NavCustomiseDrawerProps {
    open: boolean;
    onClose: () => void;
}
/**
 * @generated FunctionHeader
 * Function: NavCustomiseDrawer
 * Path: frontend/src/components/layout/NavCustomiseDrawer.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function NavCustomiseDrawer({open, onClose}: NavCustomiseDrawerProps) {
    const {
        mode, setMode, simpleItems, pinnedItems,
        unpinItem, reorderItems, hideItem,
        setNudgeCooldown, restoreItem
    } = useNavigation();

    // Local reorder state
    const [localOrder, setLocalOrder] = useState<NavItem[]>([]);
    const [orderChanged, setOrderChanged] = useState(false);

    // Sync local order when drawer opens
    React.useEffect(() => {
        if (open) {
            setLocalOrder([...simpleItems]);
            setOrderChanged(false);
        }
    }, [open, simpleItems]);
    /**
     * @generated FunctionHeader
     * Function: moveUp
     * Path: frontend/src/components/layout/NavCustomiseDrawer.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const moveUp = (idx: number) => {
        if (idx === 0) return;
        const newOrder = [...localOrder];
        [newOrder[idx - 1], newOrder[idx]] = [newOrder[idx], newOrder[idx - 1]];
        setLocalOrder(newOrder);
        setOrderChanged(true);
    };
    /**
     * @generated FunctionHeader
     * Function: moveDown
     * Path: frontend/src/components/layout/NavCustomiseDrawer.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const moveDown = (idx: number) => {
        if (idx === localOrder.length - 1) return;
        const newOrder = [...localOrder];
        [newOrder[idx], newOrder[idx + 1]] = [newOrder[idx + 1], newOrder[idx]];
        setLocalOrder(newOrder);
        setOrderChanged(true);
    };
    /**
     * @generated FunctionHeader
     * Function: saveOrder
     * Path: frontend/src/components/layout/NavCustomiseDrawer.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const saveOrder = () => {
        reorderItems(localOrder.map(i => i.id));
        setOrderChanged(false);
    };
    /**
     * @generated FunctionHeader
     * Function: handleResetDefaults
     * Path: frontend/src/components/layout/NavCustomiseDrawer.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleResetDefaults = () => {
        reorderItems([]);
        onClose();
    };

    return (
        <Sheet open={open} onOpenChange={v => !v && onClose()}>
            <SheetContent side="left" className="w-80 p-0 flex flex-col bg-white text-slate-900">
                <SheetHeader className="p-4 border-b border-slate-200">
                    <SheetTitle className="text-slate-900 text-base font-bold">Customise navigation</SheetTitle>
                    <SheetDescription className="text-slate-500 text-sm">
                        Rearrange, pin, or hide menu items.
                    </SheetDescription>
                </SheetHeader>

                <div className="flex-1 overflow-y-auto p-4 space-y-6 bg-white">

                    {/* Mode selector — 3 options */}
                    <div className="space-y-2">
                        <h3 className="text-sm font-semibold text-slate-800">Menu style</h3>
                        <div className="grid grid-cols-3 gap-1.5">
                            {([
                                {
                                    value: "simple" as NavMode,
                                    label: "Simple",
                                    desc: "Essential items + collapsible Advanced section"
                                },
                                {
                                    value: "advanced" as NavMode,
                                    label: "Advanced",
                                    desc: "All items in one flat list, sorted by usage"
                                },
                                {
                                    value: "classic" as NavMode,
                                    label: "Classic",
                                    desc: "All items always visible, no sections (legacy view)"
                                },
                            ]).map(opt => (
                                <button
                                    key={opt.value}
                                    onClick={() => setMode(opt.value)}
                                    title={opt.desc}
                                    className={`rounded-lg border px-2 py-2 text-xs font-medium transition-colors ${
                                        mode === opt.value
                                            ? "border-blue-500 bg-blue-50 text-blue-700"
                                            : "border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100"
                                    }`}
                                >
                                    {opt.label}
                                </button>
                            ))}
                        </div>
                        <p className="text-xs text-slate-400">
                            {mode === "advanced"
                                ? "All items in one flat list — no Simple/Advanced separation."
                                : mode === "classic"
                                    ? "Every menu item always visible — same as the original navigation style."
                                    : "Shows your key items, with less-used features tucked under Advanced."}
                        </p>
                    </div>

                    {/* Simple menu reorder */}
                    <div className="space-y-2">
                        <h3 className="text-sm font-semibold text-slate-800">Simple menu order</h3>
                        <p className="text-xs text-slate-500">Use the arrows to reorder your main menu items.</p>
                        <div className="space-y-1.5">
                            {localOrder.length === 0 && (
                                <p className="text-xs text-slate-400 italic py-2">No simple items configured.</p>
                            )}
                            {localOrder.map((item, idx) => (
                                <div
                                    key={item.id}
                                    className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 shadow-sm"
                                >
                                    <ItemIcon name={item.icon}/>
                                    <span className="flex-1 text-sm font-medium text-slate-800">{item.label}</span>
                                    <div className="flex gap-0.5">
                                        <button
                                            onClick={() => moveUp(idx)}
                                            disabled={idx === 0}
                                            className="p-1 rounded hover:bg-slate-200 disabled:opacity-30 text-slate-600"
                                            aria-label={`Move ${item.label} up`}
                                        >
                                            <ArrowUp className="h-3 w-3"/>
                                        </button>
                                        <button
                                            onClick={() => moveDown(idx)}
                                            disabled={idx === localOrder.length - 1}
                                            className="p-1 rounded hover:bg-slate-200 disabled:opacity-30 text-slate-600"
                                            aria-label={`Move ${item.label} down`}
                                        >
                                            <ArrowDown className="h-3 w-3"/>
                                        </button>
                                        <button
                                            onClick={() => hideItem(item.id)}
                                            className="p-1 rounded hover:bg-red-50 text-slate-400 hover:text-red-500"
                                            title="Hide this item"
                                            aria-label={`Hide ${item.label}`}
                                        >
                                            <X className="h-3 w-3"/>
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                        {orderChanged && (
                            <Button size="sm" onClick={saveOrder} className="w-full mt-1">
                                Save order
                            </Button>
                        )}
                    </div>

                    {/* Pinned from advanced */}
                    {pinnedItems.length > 0 && (
                        <div className="space-y-2">
                            <h3 className="text-sm font-semibold text-slate-800">
                                Pinned from advanced ({pinnedItems.length}/2)
                            </h3>
                            <div className="space-y-1.5">
                                {pinnedItems.map(item => (
                                    <div
                                        key={item.id}
                                        className="flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2"
                                    >
                                        <ItemIcon name={item.icon}/>
                                        <span className="flex-1 text-sm font-medium text-violet-800">{item.label}</span>
                                        <button
                                            onClick={() => unpinItem(item.id)}
                                            className="p-1 rounded hover:bg-violet-100 text-slate-400 hover:text-red-500"
                                            aria-label={`Unpin ${item.label}`}
                                        >
                                            <X className="h-3 w-3"/>
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Suggestions setting */}
                    <div className="space-y-2">
                        <h3 className="text-sm font-semibold text-slate-800">Feature suggestions</h3>
                        <div className="flex items-center gap-3">
                            <Switch
                                id="nudge-opt"
                                defaultChecked={true}
                                onCheckedChange={checked => setNudgeCooldown(checked ? 30 : 36500)}
                            />
                            <Label htmlFor="nudge-opt" className="text-sm text-slate-700 cursor-pointer">
                                Receive feature suggestions
                            </Label>
                        </div>
                    </div>

                    {/* Reset to defaults */}
                    <div className="pt-4 border-t border-slate-200">
                        <AlertDialog>
                            <AlertDialogTrigger asChild>
                                <Button variant="outline" size="sm"
                                        className="w-full text-red-600 border-red-200 hover:bg-red-50">
                                    Reset to defaults
                                </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                                <AlertDialogHeader>
                                    <AlertDialogTitle>Reset navigation?</AlertDialogTitle>
                                    <AlertDialogDescription>
                                        This will clear all your customisations (order, pins, hidden items) and restore
                                        defaults.
                                    </AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                                    <AlertDialogAction onClick={handleResetDefaults}
                                                       className="bg-red-600 hover:bg-red-700">
                                        Reset
                                    </AlertDialogAction>
                                </AlertDialogFooter>
                            </AlertDialogContent>
                        </AlertDialog>
                    </div>
                </div>
            </SheetContent>
        </Sheet>
    );
}
