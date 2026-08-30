// @featuretrace:letters — WeasyPrint letter composer page (manager/admin only)
// Layer: frontend
// Data flow: LettersPage → GET /letters/templates → dynamic fields (building-scoped)
//            → POST /letters/generate → PDF preview dialog
//            → POST /letters/send → emails to unit owners → letters_log
//            → GET /letters/history → LetterLogEntry[]
// Related: backend/routers/letters.py
//           backend/utils/letter_generator.py
//           frontend/src/app/(dashboard)/admin/letters/page.tsx
// Scope: (building-scoped)

"use client";

import React, {useCallback, useEffect, useState} from "react";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {Textarea} from "@/components/ui/textarea";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Loader2, FileText, Send, History, Eye} from "lucide-react";
import {useAuth} from "@/contexts/AuthContext";
import {toast} from "sonner";
import {format} from "date-fns";

// ── Types ──────────────────────────────────────────────────────────────────────

interface FieldSchema {
    name: string;
    type: "string" | "number" | "textarea";
    required: boolean;
    label: string;
}

interface TemplateInfo {
    name: string;
    label: string;
    description: string;
    fields: FieldSchema[];
}

interface LetterLogEntry {
    id: string;
    template: string;
    unit_numbers: string[];
    sent_by_name: string;
    sent_at: string;
    recipient_count: number;
    subject: string;
}
// ── Main component ─────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LettersPage
 * Path: frontend/src/pages/dashboard/admin/LettersPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LettersPage: React.FC = () => {
    const {api, isManager, loading: authLoading} = useAuth() as any;

    const [templates, setTemplates] = useState<TemplateInfo[]>([]);
    const [selectedTemplate, setSelectedTemplate] = useState<string>("");
    const [formData, setFormData] = useState<Record<string, string>>({});
    const [unitNumbers, setUnitNumbers] = useState<string>("");

    const [loadingTemplates, setLoadingTemplates] = useState(true);
    const [generating, setGenerating] = useState(false);
    const [sending, setSending] = useState(false);

    const [previewUrl, setPreviewUrl] = useState<string | null>(null);
    const [previewOpen, setPreviewOpen] = useState(false);

    const [history, setHistory] = useState<LetterLogEntry[]>([]);
    const [loadingHistory, setLoadingHistory] = useState(false);

    const currentTemplate = templates.find((t) => t.name === selectedTemplate) ?? null;

    // Load templates on mount
    useEffect(() => {
        if (authLoading) return;
        api
            .get("/letters/templates")
            .then((r: any) => {
                setTemplates(r.data);
                if (r.data.length > 0) setSelectedTemplate(r.data[0].name);
            })
            .catch(() => toast.error("Could not load letter templates"))
            .finally(() => setLoadingTemplates(false));
    }, [api, authLoading]);

    // Reset form when template changes
    useEffect(() => {
        setFormData({});
    }, [selectedTemplate]);

    const loadHistory = useCallback(async () => {
        setLoadingHistory(true);
        try {
            const r = await api.get("/letters/history");
            setHistory(r.data);
        } catch {
            toast.error("Could not load letter history");
        } finally {
            setLoadingHistory(false);
        }
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handlePreview
     * Path: frontend/src/pages/dashboard/admin/LettersPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePreview = async () => {
        if (!selectedTemplate) return;
        setGenerating(true);
        try {
            const resp = await api.post(
                "/letters/generate",
                {template: selectedTemplate, data: formData},
                {responseType: "blob"}
            );
            const url = URL.createObjectURL(new Blob([resp.data], {type: "application/pdf"}));
            if (previewUrl) URL.revokeObjectURL(previewUrl);
            setPreviewUrl(url);
            setPreviewOpen(true);
        } catch {
            toast.error("Failed to generate letter preview");
        } finally {
            setGenerating(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSend
     * Path: frontend/src/pages/dashboard/admin/LettersPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSend = async () => {
        if (!selectedTemplate) return;
        const units = unitNumbers
            .split(/[\s,]+/)
            .map((u) => u.trim())
            .filter(Boolean);
        if (units.length === 0) {
            toast.error("Enter at least one unit number");
            return;
        }
        setSending(true);
        try {
            const resp = await api.post("/letters/send", {
                template: selectedTemplate,
                data: formData,
                unit_numbers: units,
            });
            toast.success(`Letter sent to ${resp.data.sent} recipient(s)`);
            setUnitNumbers("");
            await loadHistory();
        } catch (err: any) {
            if (err?.response?.status === 429) {
                toast.error("Rate limit reached — please wait a minute before sending more letters");
            } else {
                toast.error("Failed to send letters");
            }
        } finally {
            setSending(false);
        }
    };

    if (authLoading || loadingTemplates) {
        return (
            <div className="flex items-center justify-center h-64" data-testid="letters-loading">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
            </div>
        );
    }

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                    <FileText className="h-6 w-6" aria-hidden="true"/>
                    Owner Letters
                </h1>
                <p className="text-muted-foreground text-sm mt-1">
                    Generate and send branded PDF letters to unit owners
                </p>
            </div>

            <Tabs defaultValue="compose" onValueChange={(v) => v === "history" && loadHistory()}>
                <TabsList className="mb-6" data-testid="letters-tabs">
                    <TabsTrigger value="compose" data-testid="tab-compose">Compose</TabsTrigger>
                    <TabsTrigger value="history" data-testid="tab-history">History</TabsTrigger>
                </TabsList>

                {/* ── Compose tab ──────────────────────────────────────────────────── */}
                <TabsContent value="compose">
                    <div className="space-y-6">
                        {/* Template selector */}
                        <div className="space-y-2">
                            <Label htmlFor="template-select">Letter Template</Label>
                            <Select
                                value={selectedTemplate}
                                onValueChange={setSelectedTemplate}
                            >
                                <SelectTrigger
                                    id="template-select"
                                    className="w-full"
                                    data-testid="template-select"
                                >
                                    <SelectValue placeholder="Select a template"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {templates.map((t) => (
                                        <SelectItem key={t.name} value={t.name}
                                                    data-testid={`template-option-${t.name}`}>
                                            {t.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {currentTemplate && (
                                <p className="text-xs text-muted-foreground" data-testid="template-description">
                                    {currentTemplate.description}
                                </p>
                            )}
                        </div>

                        {/* Dynamic fields */}
                        {currentTemplate && (
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2" data-testid="template-fields">
                                {currentTemplate.fields.map((field) => (
                                    <div key={field.name} className={field.type === "textarea" ? "sm:col-span-2" : ""}>
                                        <Label htmlFor={`field-${field.name}`}>
                                            {field.label}
                                            {field.required &&
                                                <span className="text-destructive ml-1" aria-hidden>*</span>}
                                        </Label>
                                        {field.type === "textarea" ? (
                                            <Textarea
                                                id={`field-${field.name}`}
                                                value={formData[field.name] ?? ""}
                                                onChange={(e) =>
                                                    setFormData((prev) => ({...prev, [field.name]: e.target.value}))
                                                }
                                                rows={3}
                                                className="mt-1"
                                                data-testid={`field-${field.name}`}
                                            />
                                        ) : (
                                            <Input
                                                id={`field-${field.name}`}
                                                type={field.type === "number" ? "number" : "text"}
                                                value={formData[field.name] ?? ""}
                                                onChange={(e) =>
                                                    setFormData((prev) => ({...prev, [field.name]: e.target.value}))
                                                }
                                                className="mt-1"
                                                data-testid={`field-${field.name}`}
                                            />
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Recipients */}
                        <div className="space-y-2">
                            <Label htmlFor="unit-numbers">
                                Unit Numbers <span className="text-muted-foreground font-normal">(comma or space separated)</span>
                            </Label>
                            <Input
                                id="unit-numbers"
                                value={unitNumbers}
                                onChange={(e) => setUnitNumbers(e.target.value)}
                                placeholder="e.g. 1, 2, 3 or leave blank to preview only"
                                data-testid="unit-numbers-input"
                            />
                        </div>

                        {/* Action buttons */}
                        <div className="flex gap-3 flex-wrap">
                            <Button
                                variant="outline"
                                onClick={handlePreview}
                                disabled={!selectedTemplate || generating}
                                data-testid="preview-btn"
                                aria-label="Preview letter as PDF"
                            >
                                {generating ? (
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden/>
                                ) : (
                                    <Eye className="h-4 w-4 mr-2" aria-hidden/>
                                )}
                                Preview PDF
                            </Button>
                            <Button
                                onClick={handleSend}
                                disabled={!selectedTemplate || sending}
                                data-testid="send-btn"
                                aria-label="Send letter to unit owners"
                            >
                                {sending ? (
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" aria-hidden/>
                                ) : (
                                    <Send className="h-4 w-4 mr-2" aria-hidden/>
                                )}
                                Send Letter
                            </Button>
                        </div>
                    </div>
                </TabsContent>

                {/* ── History tab ───────────────────────────────────────────────────── */}
                <TabsContent value="history">
                    {loadingHistory ? (
                        <div className="flex justify-center py-12" data-testid="history-loading">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                        </div>
                    ) : history.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground" data-testid="history-empty">
                            <History className="h-10 w-10 mx-auto mb-3 opacity-40" aria-hidden/>
                            <p>No letters sent yet</p>
                        </div>
                    ) : (
                        <div className="divide-y border rounded-lg overflow-hidden" data-testid="history-list">
                            {history.map((entry) => (
                                <div
                                    key={entry.id}
                                    className="px-4 py-3 flex items-center justify-between hover:bg-muted/30"
                                    data-testid={`history-item-${entry.id}`}
                                >
                                    <div>
                                        <p className="text-sm font-medium">{entry.subject}</p>
                                        <p className="text-xs text-muted-foreground">
                                            Template: {entry.template} · Units: {entry.unit_numbers.join(", ")} ·{" "}
                                            {entry.recipient_count} recipient{entry.recipient_count !== 1 ? "s" : ""}
                                        </p>
                                    </div>
                                    <div className="text-right text-xs text-muted-foreground">
                                        <p>{entry.sent_by_name}</p>
                                        <p>{format(new Date(entry.sent_at), "d MMM yyyy HH:mm")}</p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </TabsContent>
            </Tabs>

            {/* ── PDF Preview dialog ─────────────────────────────────────────────── */}
            <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
                <DialogContent className="sm:max-w-4xl h-[90vh] flex flex-col p-0">
                    <DialogHeader className="px-6 pt-5 pb-3 border-b flex-shrink-0">
                        <DialogTitle className="flex items-center gap-2">
                            <FileText className="h-4 w-4" aria-hidden/>
                            Letter Preview — {currentTemplate?.label}
                        </DialogTitle>
                        <DialogDescription className="sr-only">
                            Preview the generated letter PDF before sending it to owners.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="flex-1 overflow-hidden" data-testid="letter-preview-area">
                        {previewUrl && (
                            <iframe
                                src={previewUrl}
                                className="w-full h-full border-0"
                                title="Letter PDF preview"
                                data-testid="letter-preview-iframe"
                            />
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default LettersPage;
