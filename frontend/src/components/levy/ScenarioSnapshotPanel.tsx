// @ts-nocheck
"use client";
import React, {useCallback, useEffect, useState} from "react";
import {Card, CardContent} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Badge} from "@/components/ui/badge";
import {Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,} from "@/components/ui/dialog";
import {Clock, Plus, RotateCcw, Save, Trash2} from "lucide-react";
import axios from "axios";

const API = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

interface Snapshot {
    snapshot_id: string;
    name: string;
    description: string;
    created_by: string;
    created_at: string;
}

interface Props {
    canAdmin: boolean;
    canDelete?: boolean;  // delete is more destructive — defaults to canAdmin if not provided
    onRestored?: () => void;
}
/**
 * @generated FunctionHeader
 * Function: ScenarioSnapshotPanel
 * Path: frontend/src/components/levy/ScenarioSnapshotPanel.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ScenarioSnapshotPanel: React.FC<Props> = ({canAdmin, canDelete, onRestored}) => {
    const effectiveCanDelete = canDelete !== undefined ? canDelete : canAdmin;
    const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
    const [loading, setLoading] = useState(false);
    const [saveOpen, setSaveOpen] = useState(false);
    const [newName, setNewName] = useState("");
    const [newDesc, setNewDesc] = useState("");
    const [saving, setSaving] = useState(false);
    const [restoring, setRestoring] = useState<string | null>(null);
    const [deleting, setDeleting] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const token = localStorage.getItem("token");
            const {data} = await axios.get(
                `${API}/api/intelligence/levy-fairness/snapshots`,
                {headers: {Authorization: `Bearer ${token}`}}
            );
            setSnapshots(data || []);
        } catch {
            // silent
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/components/levy/ScenarioSnapshotPanel.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        if (!newName.trim()) return;
        setSaving(true);
        try {
            const token = localStorage.getItem("token");
            await axios.post(
                `${API}/api/intelligence/levy-fairness/snapshots`,
                {name: newName.trim(), description: newDesc.trim()},
                {headers: {Authorization: `Bearer ${token}`}}
            );
            setSaveOpen(false);
            setNewName("");
            setNewDesc("");
            await load();
        } catch (e: any) {
            alert(e.response?.data?.detail || "Failed to save snapshot");
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRestore
     * Path: frontend/src/components/levy/ScenarioSnapshotPanel.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRestore = async (id: string) => {
        if (
            !window.confirm(
                "Restore this snapshot as the active model? The current model will be overwritten."
            )
        )
            return;
        setRestoring(id);
        try {
            const token = localStorage.getItem("token");
            await axios.post(
                `${API}/api/intelligence/levy-fairness/snapshots/${id}/restore`,
                {},
                {headers: {Authorization: `Bearer ${token}`}}
            );
            onRestored?.();
        } catch (e: any) {
            alert(e.response?.data?.detail || "Failed to restore");
        } finally {
            setRestoring(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/components/levy/ScenarioSnapshotPanel.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id: string) => {
        if (!window.confirm("Delete this snapshot? This cannot be undone.")) return;
        setDeleting(id);
        try {
            const token = localStorage.getItem("token");
            await axios.delete(`${API}/api/intelligence/levy-fairness/snapshots/${id}`, {
                headers: {Authorization: `Bearer ${token}`},
            });
            await load();
        } catch (e: any) {
            alert(e.response?.data?.detail || "Failed to delete");
        } finally {
            setDeleting(null);
        }
    };

    return (
        <div className="space-y-4">
            {canAdmin && (
                <div className="flex justify-end">
                    <Button size="sm" onClick={() => setSaveOpen(true)}>
                        <Plus className="w-4 h-4 mr-1"/> Save Current Model as Snapshot
                    </Button>
                </div>
            )}

            {loading ? (
                <p className="text-sm text-muted-foreground text-center py-8">Loading snapshots…</p>
            ) : snapshots.length === 0 ? (
                <Card>
                    <CardContent className="py-12 text-center text-muted-foreground">
                        <Save className="w-8 h-8 mx-auto mb-3 opacity-30"/>
                        <p className="text-sm">No snapshots saved yet.</p>
                        {canAdmin && (
                            <p className="text-xs mt-1">
                                Use "Save Current Model" to create a reference point you can restore later.
                            </p>
                        )}
                    </CardContent>
                </Card>
            ) : (
                <div className="grid gap-3">
                    {snapshots.map((s) => (
                        <Card key={s.snapshot_id}>
                            <CardContent className="pt-4 pb-3">
                                <div className="flex items-start justify-between gap-2">
                                    <div className="flex-1 min-w-0">
                                        <p className="font-semibold text-sm truncate">{s.name}</p>
                                        {s.description && (
                                            <p className="text-xs text-muted-foreground mt-0.5">{s.description}</p>
                                        )}
                                        <div className="flex items-center gap-2 mt-2 flex-wrap">
                                            <Badge variant="outline" className="text-xs">
                                                <Clock className="w-3 h-3 mr-1"/>
                                                {new Date(s.created_at).toLocaleDateString("en-AU", {
                                                    day: "numeric",
                                                    month: "short",
                                                    year: "numeric",
                                                })}
                                            </Badge>
                                            <span className="text-xs text-muted-foreground">by {s.created_by}</span>
                                        </div>
                                    </div>
                                    {canAdmin && (
                                        <div className="flex gap-1 shrink-0">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                onClick={() => handleRestore(s.snapshot_id)}
                                                disabled={restoring === s.snapshot_id}
                                            >
                                                <RotateCcw className="w-3 h-3 mr-1"/>
                                                {restoring === s.snapshot_id ? "Restoring…" : "Restore"}
                                            </Button>
                                            {effectiveCanDelete && (
                                                <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    className="text-rose-600 hover:text-rose-700"
                                                    onClick={() => handleDelete(s.snapshot_id)}
                                                    disabled={deleting === s.snapshot_id}
                                                >
                                                    <Trash2 className="w-3 h-3"/>
                                                </Button>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Save Model Snapshot</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-3 py-2">
                        <div>
                            <label className="text-sm font-medium">Snapshot Name</label>
                            <Input
                                className="mt-1"
                                placeholder="e.g. Pre-AGM 2026 Baseline"
                                value={newName}
                                onChange={(e) => setNewName(e.target.value)}
                            />
                        </div>
                        <div>
                            <label className="text-sm font-medium">Description (optional)</label>
                            <textarea
                                className="mt-1 flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                                placeholder="Describe what makes this scenario significant…"
                                value={newDesc}
                                onChange={(e) => setNewDesc(e.target.value)}
                                rows={2}
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setSaveOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleSave} disabled={saving || !newName.trim()}>
                            {saving ? "Saving…" : "Save Snapshot"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};
