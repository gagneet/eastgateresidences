// @featuretrace:documents — PDF annotation dialog (highlight + comment layer)
// Layer: frontend
// Data flow: DocumentsPage → GET /documents/{id}/annotations + GET /documents/{id}
//            → PdfHighlighter → POST/PUT/DELETE /documents/{id}/annotations
// Related: frontend/src/pages/dashboard/DocumentsPage.tsx
//           backend/routers/document_annotations.py
//           backend/models/document_annotation.py
// Scope: (building-scoped)

"use client";

import React, {useCallback, useEffect, useRef, useState} from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Button} from "@/components/ui/button";
import {Textarea} from "@/components/ui/textarea";
import {Loader2, MessageSquare, Trash2, X} from "lucide-react";
import {useAuth} from "@/contexts/AuthContext";
import {toast} from "sonner";

// react-pdf-highlighter-extended is ESM-only; resolved via moduleNameMapper in Jest
import {
    AreaHighlight,
    MonitoredHighlightContainer,
    PdfHighlighter,
    PdfLoader,
    TextHighlight,
    useHighlightContainerContext,
    usePdfHighlighterContext,
} from "react-pdf-highlighter-extended";
import type {Highlight, PdfSelection} from "react-pdf-highlighter-extended";

// Annotator stylesheets (no barrel index.css — import each file individually)
import "react-pdf-highlighter-extended/dist/esm/style/PdfHighlighter.css";
import "react-pdf-highlighter-extended/dist/esm/style/TextHighlight.css";
import "react-pdf-highlighter-extended/dist/esm/style/AreaHighlight.css";
import "react-pdf-highlighter-extended/dist/esm/style/MouseSelection.css";

// Worker — uses a locally-served worker file (public/pdf-annotator.worker.min.mjs) copied from
// react-pdf-highlighter-extended's bundled pdfjs-dist at install time. This ensures the worker
// version matches the API version used by react-pdf-highlighter-extended (pdfjs-dist@4.x)
// and avoids the version mismatch that occurs when referencing the top-level pdfjs-dist@5.x.
const PDF_ANNOTATOR_WORKER_SRC = "/pdf-annotator.worker.min.mjs";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AnnotationComment {
    text: string;
    emoji: string
}

interface StoredHighlight extends Highlight {
    highlight_id: string;
    document_id: string;
    building_id: string;
    created_by: string;
    created_by_name: string;
    created_at: string;
    comment: AnnotationComment;
}

interface Props {
    documentId: string | null;
    fileName: string;
    isOpen: boolean;
    onClose: () => void;
    canAnnotate: boolean;
}

// ── Inline tip (comment input shown on text selection) ────────────────────────

interface TipProps {
    onConfirm: (comment: AnnotationComment) => void;
    onCancel: () => void;
}
/**
 * @generated FunctionHeader
 * Function: HighlightTip
 * Path: frontend/src/components/documents/PDFAnnotatorDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const HighlightTip: React.FC<TipProps> = ({onConfirm, onCancel}) => {
    const [text, setText] = useState("");
    return (
        <div
            className="bg-white border shadow-lg rounded-lg p-3 w-64 z-50"
            data-testid="highlight-tip"
        >
            <Textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Add a comment…"
                rows={2}
                className="mb-2 text-sm"
                data-testid="highlight-comment-input"
                autoFocus
            />
            <div className="flex gap-2 justify-end">
                <Button size="sm" variant="ghost" onClick={onCancel} aria-label="Cancel annotation">
                    Cancel
                </Button>
                <Button
                    size="sm"
                    onClick={() => onConfirm({text, emoji: ""})}
                    aria-label="Save annotation"
                    data-testid="highlight-save-btn"
                >
                    Save
                </Button>
            </div>
        </div>
    );
};

// ── Per-highlight renderer (uses HighlightContext injected by PdfHighlighter) ──

interface HighlightRendererProps {
    canAnnotate: boolean;
    userId: string;
    onDelete: (id: string) => void;
}
/**
 * @generated FunctionHeader
 * Function: HighlightRenderer
 * Path: frontend/src/components/documents/PDFAnnotatorDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const HighlightRenderer: React.FC<HighlightRendererProps> = ({canAnnotate, userId, onDelete}) => {
    const {highlight, isScrolledTo} = useHighlightContainerContext<StoredHighlight>();
    const {setTip} = usePdfHighlighterContext();
    const isText = !!(highlight.content?.text);

    const tipContent = (
        <div
            className="bg-white border shadow rounded p-2 max-w-xs text-sm z-50"
            data-testid={`annotation-popup-${highlight.id}`}
        >
            {(highlight as any).comment?.text && (
                <p className="mb-1">{(highlight as any).comment.text}</p>
            )}
            <p className="text-xs text-muted-foreground">{(highlight as any).created_by_name}</p>
            {(canAnnotate || (highlight as any).created_by === userId) && (
                <Button
                    size="sm"
                    variant="ghost"
                    className="mt-1 text-destructive h-6 px-2"
                    onClick={() => onDelete(highlight.id)}
                    aria-label="Delete annotation"
                    data-testid={`delete-annotation-${highlight.id}`}
                >
                    <Trash2 className="h-3 w-3 mr-1" aria-hidden="true"/>
                    Remove
                </Button>
            )}
        </div>
    );

    return (
        <MonitoredHighlightContainer
            onMouseEnter={() =>
                setTip({position: highlight.position, content: tipContent})
            }
            onMouseLeave={() => setTip(null)}
        >
            {isText ? (
                <TextHighlight highlight={highlight} isScrolledTo={isScrolledTo}/>
            ) : (
                <AreaHighlight highlight={highlight} isScrolledTo={isScrolledTo} onChange={() => {
                }}/>
            )}
        </MonitoredHighlightContainer>
    );
};
// ── Main component ────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: PDFAnnotatorDialog
 * Path: frontend/src/components/documents/PDFAnnotatorDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PDFAnnotatorDialog: React.FC<Props> = ({
                                                 documentId,
                                                 fileName,
                                                 isOpen,
                                                 onClose,
                                                 canAnnotate,
                                             }) => {
    const {api, user} = useAuth() as any;

    const [fileData, setFileData] = useState<string | null>(null);
    const [loadingFile, setLoadingFile] = useState(false);
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [highlights, setHighlights] = useState<StoredHighlight[]>([]);
    const [pendingSelection, setPendingSelection] = useState<PdfSelection | null>(null);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    // Fetch document + annotations in parallel on open
    const loadContent = useCallback(async () => {
        if (!documentId) return;
        setLoadingFile(true);
        setFetchError(null);
        try {
            const [docResp, annResp] = await Promise.all([
                api.get(`/documents/${documentId}`),
                api.get(`/documents/${documentId}/annotations`),
            ]);
            if (!docResp.data.file_data) throw new Error("No file data");
            setFileData(docResp.data.file_data);
            setHighlights(
                (annResp.data as any[]).map((a: any) => ({
                    id: a.id,
                    highlight_id: a.highlight_id,
                    document_id: a.document_id,
                    building_id: a.building_id,
                    position: a.position,
                    content: a.content,
                    comment: a.comment,
                    created_by: a.created_by,
                    created_by_name: a.created_by_name,
                    created_at: a.created_at,
                }))
            );
        } catch {
            setFetchError("Could not load document.");
        } finally {
            setLoadingFile(false);
        }
    }, [api, documentId]);

    useEffect(() => {
        if (isOpen && documentId) loadContent();
        if (!isOpen) {
            setFileData(null);
            setHighlights([]);
            setFetchError(null);
            setPendingSelection(null);
        }
    }, [isOpen, documentId, loadContent]);
    // Save highlight + comment to backend
    /**
     * @generated FunctionHeader
     * Function: handleSaveHighlight
     * Path: frontend/src/components/documents/PDFAnnotatorDialog.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveHighlight = async (comment: AnnotationComment) => {
        if (!pendingSelection || !documentId) return;
        const highlight_id = `h-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const body = {
            highlight_id,
            position: pendingSelection.position,
            content: pendingSelection.content,
            comment,
        };
        try {
            const resp = await api.post(`/documents/${documentId}/annotations`, body);
            setHighlights((prev) => [...prev, resp.data]);
            toast.success("Annotation saved");
        } catch {
            toast.error("Failed to save annotation");
        } finally {
            setPendingSelection(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDeleteHighlight
     * Path: frontend/src/components/documents/PDFAnnotatorDialog.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteHighlight = async (annId: string) => {
        if (!documentId) return;
        try {
            await api.delete(`/documents/${documentId}/annotations/${annId}`);
            setHighlights((prev) => prev.filter((h) => h.id !== annId));
            toast.success("Annotation removed");
        } catch {
            toast.error("Failed to remove annotation");
        }
    };

    // ── Render ──────────────────────────────────────────────────────────────────
    return (
        <Dialog open={isOpen} onOpenChange={onClose}>
            <DialogContent className="sm:max-w-6xl max-h-[92vh] flex flex-col p-0">
                <DialogHeader className="px-6 pt-5 pb-3 border-b flex-shrink-0">
                    <DialogTitle className="truncate pr-8 flex items-center gap-2">
                        <MessageSquare className="h-4 w-4 flex-shrink-0" aria-hidden="true"/>
                        {fileName}
                    </DialogTitle>
                    {canAnnotate && (
                        <p className="text-xs text-muted-foreground mt-1">
                            Select text or drag to create an annotation
                        </p>
                    )}
                </DialogHeader>

                <div className="flex flex-1 overflow-hidden min-h-0">
                    {/* ── PDF + annotation layer ───────────────────────────────────────── */}
                    <div
                        className="flex-1 overflow-auto bg-gray-100 relative"
                        data-testid="annotator-pdf-area"
                    >
                        {(loadingFile || !mounted) && (
                            <div
                                className="flex items-center justify-center h-full"
                                data-testid="annotator-loading"
                            >
                                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
                            </div>
                        )}

                        {fetchError && !loadingFile && (
                            <div
                                className="flex flex-col items-center justify-center h-full gap-3"
                                data-testid="annotator-error"
                                role="alert"
                            >
                                <p className="text-muted-foreground">{fetchError}</p>
                                <Button variant="outline" size="sm" onClick={loadContent} aria-label="Retry loading">
                                    Retry
                                </Button>
                            </div>
                        )}

                        {mounted && fileData && !loadingFile && !fetchError && (
                            <PdfLoader
                                document={`data:application/pdf;base64,${fileData}`}
                                workerSrc={PDF_ANNOTATOR_WORKER_SRC}
                                beforeLoad={() => (
                                    <div className="flex items-center justify-center h-64">
                                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
                                    </div>
                                )}
                            >
                                {(pdfDocument) => (
                                    <PdfHighlighter
                                        pdfDocument={pdfDocument}
                                        highlights={highlights}
                                        onScrollAway={() => {
                                        }}
                                        enableAreaSelection={(e) => e.altKey}
                                        selectionTip={
                                            canAnnotate ? (
                                                <HighlightTip
                                                    onConfirm={handleSaveHighlight}
                                                    onCancel={() => setPendingSelection(null)}
                                                />
                                            ) : undefined
                                        }
                                        onSelection={canAnnotate ? setPendingSelection : undefined}
                                        utilsRef={() => {
                                        }}
                                        style={{height: "100%", minHeight: "600px"}}
                                    >
                                        <HighlightRenderer
                                            canAnnotate={canAnnotate}
                                            userId={user?.id ?? ""}
                                            onDelete={handleDeleteHighlight}
                                        />
                                    </PdfHighlighter>
                                )}
                            </PdfLoader>
                        )}
                    </div>

                    {/* ── Sidebar: annotation list ───────────────────────────────────── */}
                    <div
                        className="w-72 border-l flex flex-col overflow-hidden bg-white flex-shrink-0"
                        data-testid="annotations-sidebar"
                        role="complementary"
                        aria-label="Annotations list"
                    >
                        <div className="px-4 py-3 border-b">
                            <h3 className="text-sm font-semibold">
                                Annotations
                                {highlights.length > 0 && (
                                    <span className="ml-2 text-xs text-muted-foreground">
                    ({highlights.length})
                  </span>
                                )}
                            </h3>
                        </div>

                        <div className="flex-1 overflow-y-auto divide-y" data-testid="annotation-list">
                            {highlights.length === 0 && (
                                <div className="p-4 text-sm text-muted-foreground text-center"
                                     data-testid="no-annotations">
                                    {canAnnotate
                                        ? "Select text or hold Alt + drag to annotate"
                                        : "No annotations yet"}
                                </div>
                            )}
                            {highlights.map((h) => (
                                <div
                                    key={h.id}
                                    className="px-4 py-3 hover:bg-muted/30 transition-colors group"
                                    data-testid={`annotation-item-${h.id}`}
                                >
                                    {h.content?.text && (
                                        <p className="text-xs text-muted-foreground mb-1 italic line-clamp-2">
                                            &ldquo;{h.content.text}&rdquo;
                                        </p>
                                    )}
                                    {h.comment?.text && (
                                        <p className="text-sm mb-1">{h.comment.text}</p>
                                    )}
                                    <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">
                      {h.created_by_name} · p.{h.position?.boundingRect?.pageNumber}
                    </span>
                                        {(canAnnotate || h.created_by === user?.id) && (
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                className="h-6 px-2 opacity-0 group-hover:opacity-100 transition-opacity text-destructive"
                                                onClick={() => handleDeleteHighlight(h.id)}
                                                aria-label="Remove annotation"
                                                data-testid={`sidebar-delete-${h.id}`}
                                            >
                                                <Trash2 className="h-3 w-3" aria-hidden="true"/>
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
};

export default PDFAnnotatorDialog;