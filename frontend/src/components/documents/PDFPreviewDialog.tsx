// @featuretrace:documents — PDF inline preview dialog component
// Layer: frontend
// Data flow: DocumentsPage → GET /documents/{id} → file_data (base64) → react-pdf renderer
// Related: frontend/src/pages/dashboard/DocumentsPage.tsx
//           backend/routers/documents.py
// Scope: (building-scoped)

"use client";

import React, {useCallback, useEffect, useState} from "react";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Button} from "@/components/ui/button";
import {
    ChevronLeft,
    ChevronRight,
    Download,
    Loader2,
    ZoomIn,
    ZoomOut,
} from "lucide-react";
import {useAuth} from "@/contexts/AuthContext";
import {toast} from "sonner";

// react-pdf is ESM-only; import lazily so SSR builds never evaluate it.
// The `mounted` guard below ensures these are only called in the browser.
// In Jest, moduleNameMapper points these to manual mocks.
import {Document, Page, pdfjs} from "react-pdf";

// Worker setup — runs once when this module is first imported in a browser context.
// Uses a locally-served worker file (public/pdf-preview.worker.min.mjs) copied from
// react-pdf's bundled pdfjs-dist at install time, ensuring the worker version always
// matches the API version react-pdf uses (avoids CDN dependency and version mismatches).
if (typeof window !== "undefined") {
    pdfjs.GlobalWorkerOptions.workerSrc = "/pdf-preview.worker.min.mjs";
}
/**
 * @generated FunctionHeader
 * Function: PdfLoadingSpinner
 * Path: frontend/src/components/documents/PDFPreviewDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function PdfLoadingSpinner() {
    return (
        <div
            className="flex items-center justify-center h-64"
            data-testid="pdf-loading-spinner"
        >
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
        </div>
    );
}

interface Props {
    documentId: string | null;
    fileName: string;
    fileType: string;
    isOpen: boolean;
    onClose: () => void;
}

const SCALE_MIN = 0.5;
const SCALE_MAX = 3.0;
const SCALE_STEP = 0.25;
/**
 * @generated FunctionHeader
 * Function: PDFPreviewDialog
 * Path: frontend/src/components/documents/PDFPreviewDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PDFPreviewDialog: React.FC<Props> = ({
                                               documentId,
                                               fileName,
                                               fileType,
                                               isOpen,
                                               onClose,
                                           }) => {
    const {api} = useAuth() as any;

    // Prevent react-pdf from running during SSR
    const [mounted, setMounted] = useState(false);
    const [fileData, setFileData] = useState<string | null>(null);
    const [loadingFile, setLoadingFile] = useState(false);
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [numPages, setNumPages] = useState<number | null>(null);
    const [pageNumber, setPageNumber] = useState(1);
    const [scale, setScale] = useState(1.0);

    useEffect(() => {
        setMounted(true);
    }, []);

    const fetchDocument = useCallback(async () => {
        if (!documentId) return;
        setLoadingFile(true);
        setFetchError(null);
        setFileData(null);
        setPageNumber(1);
        try {
            const response = await api.get(`/documents/${documentId}`);
            const data = response.data.file_data ?? null;
            if (!data) {
                setFetchError("Document content not available.");
            } else {
                setFileData(data);
            }
        } catch {
            setFetchError("Could not load PDF. Please try downloading instead.");
        } finally {
            setLoadingFile(false);
        }
    }, [api, documentId]);

    useEffect(() => {
        if (isOpen && documentId) {
            fetchDocument();
        }
        if (!isOpen) {
            setFileData(null);
            setFetchError(null);
            setNumPages(null);
            setPageNumber(1);
            setScale(1.0);
        }
    }, [isOpen, documentId, fetchDocument]);
    /**
     * @generated FunctionHeader
     * Function: handleDownload
     * Path: frontend/src/components/documents/PDFPreviewDialog.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownload = () => {
        if (!fileData) return;
        const link = document.createElement("a");
        link.href = `data:${fileType};base64,${fileData}`;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        toast.success("Download started");
    };

    const canGoBack = pageNumber > 1;
    const canGoForward = numPages !== null && pageNumber < numPages;

    return (
        <Dialog open={isOpen} onOpenChange={onClose}>
            <DialogContent className="sm:max-w-4xl max-h-[90vh] flex flex-col p-0">
                {/* Header */}
                <DialogHeader className="px-6 pt-6 pb-3 border-b flex-shrink-0">
                    <DialogTitle className="truncate pr-8" title={fileName}>
                        {fileName}
                    </DialogTitle>
                </DialogHeader>

                {/* Controls bar */}
                <div
                    className="flex items-center justify-between px-4 py-2 bg-muted/40 border-b flex-shrink-0"
                    role="toolbar"
                    aria-label="PDF viewer controls"
                >
                    {/* Page navigation */}
                    <div className="flex items-center gap-2" role="group" aria-label="Page navigation">
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
                            disabled={!canGoBack}
                            data-testid="page-prev-btn"
                            aria-label="Previous page"
                        >
                            <ChevronLeft className="h-4 w-4" aria-hidden="true"/>
                        </Button>
                        <span
                            className="text-sm tabular-nums"
                            data-testid="page-indicator"
                            aria-live="polite"
                            aria-atomic="true"
                        >
              {numPages ? `${pageNumber} / ${numPages}` : "—"}
            </span>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                                setPageNumber((p) => Math.min(numPages ?? p, p + 1))
                            }
                            disabled={!canGoForward}
                            data-testid="page-next-btn"
                            aria-label="Next page"
                        >
                            <ChevronRight className="h-4 w-4" aria-hidden="true"/>
                        </Button>
                    </div>

                    {/* Zoom controls */}
                    <div className="flex items-center gap-2" role="group" aria-label="Zoom controls">
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                                setScale((s) => Math.max(SCALE_MIN, +(s - SCALE_STEP).toFixed(2)))
                            }
                            disabled={scale <= SCALE_MIN}
                            data-testid="zoom-out-btn"
                            aria-label="Zoom out"
                        >
                            <ZoomOut className="h-4 w-4" aria-hidden="true"/>
                        </Button>
                        <span
                            className="text-sm w-14 text-center tabular-nums"
                            data-testid="zoom-level"
                            aria-live="polite"
                        >
              {Math.round(scale * 100)}%
            </span>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                                setScale((s) => Math.min(SCALE_MAX, +(s + SCALE_STEP).toFixed(2)))
                            }
                            disabled={scale >= SCALE_MAX}
                            data-testid="zoom-in-btn"
                            aria-label="Zoom in"
                        >
                            <ZoomIn className="h-4 w-4" aria-hidden="true"/>
                        </Button>
                    </div>

                    {/* Download */}
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleDownload}
                        disabled={!fileData}
                        data-testid="preview-download-btn"
                        aria-label={`Download ${fileName}`}
                    >
                        <Download className="mr-2 h-4 w-4" aria-hidden="true"/>
                        Download
                    </Button>
                </div>

                {/* PDF content area */}
                <div
                    className="flex-1 overflow-auto flex items-start justify-center bg-gray-100 p-4 min-h-[400px]"
                    data-testid="pdf-content-area"
                    role="region"
                    aria-label="PDF document viewer"
                >
                    {(loadingFile || !mounted) && <PdfLoadingSpinner/>}

                    {fetchError && !loadingFile && (
                        <div
                            className="flex flex-col items-center justify-center h-64 text-center gap-3"
                            data-testid="pdf-error"
                            role="alert"
                        >
                            <p className="text-muted-foreground">{fetchError}</p>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={fetchDocument}
                                aria-label="Retry loading document"
                            >
                                Retry
                            </Button>
                        </div>
                    )}

                    {mounted && fileData && !loadingFile && !fetchError && (
                        <Document
                            file={`data:application/pdf;base64,${fileData}`}
                            onLoadSuccess={({numPages: n}) => setNumPages(n)}
                            onLoadError={() => setFetchError("Could not render PDF.")}
                            loading={<PdfLoadingSpinner/>}
                        >
                            <Page
                                pageNumber={pageNumber}
                                scale={scale}
                                renderTextLayer={false}
                                renderAnnotationLayer={false}
                            />
                        </Document>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
};

export default PDFPreviewDialog;