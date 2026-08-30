import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Badge } from '../../components/ui/badge';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { ScrollArea } from '../../components/ui/scroll-area';
import {
    AlertCircle,
    AlertTriangle,
    CheckCircle2,
    ClipboardCopy,
    Clock,
    Download,
    FileSpreadsheet,
    FileText,
    FileType,
    Globe,
    History,
    Info,
    Loader2,
    RefreshCw,
    Upload,
} from 'lucide-react';
import { toast } from 'sonner';

const ACCEPTED_EXTENSIONS = '.pdf,.doc,.docx,.xls,.xlsx,.pptx,.html,.htm,.csv,.txt,.md';

const FILE_TYPE_ICONS = {
    '.pdf': <FileType className="h-5 w-5 text-red-500"/>,
    '.doc': <FileText className="h-5 w-5 text-blue-500"/>,
    '.docx': <FileText className="h-5 w-5 text-blue-500"/>,
    '.xls': <FileSpreadsheet className="h-5 w-5 text-green-600"/>,
    '.xlsx': <FileSpreadsheet className="h-5 w-5 text-green-600"/>,
    '.pptx': <FileType className="h-5 w-5 text-orange-500"/>,
};
/**
 * @generated FunctionHeader
 * Function: getFileIcon
 * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getFileIcon(filename) {
    const ext = ( '.' + ( filename || '' ).split('.').pop() ).toLowerCase();
    return FILE_TYPE_ICONS[ ext ] || <FileText className="h-5 w-5 text-slate-500"/>;
}
/**
 * @generated FunctionHeader
 * Function: formatBytes
 * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${( bytes / 1024 ).toFixed(1)} KB`;
    return `${( bytes / ( 1024 * 1024 ) ).toFixed(1)} MB`;
}
/**
 * @generated FunctionHeader
 * Function: formatDateTime
 * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatDateTime(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString(undefined, {
        dateStyle: 'short', timeStyle: 'short',
    });
}
// ── Markdown output panel ─────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: MarkdownOutput
 * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function MarkdownOutput({data, onDownload, onCopy, onReset}) {
    const isPdf = data.extension === '.pdf';

    return (
        <div className="space-y-3">
            {/* Header row */}
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <CheckCircle2 className="h-5 w-5 text-green-500 shrink-0"/>
                    <span className="font-medium text-sm truncate max-w-xs">
                        {data.filename || data.url}
                    </span>
                    <Badge variant="outline" className="text-xs">
                        {data.char_count?.toLocaleString()} chars · {data.line_count?.toLocaleString()} lines
                    </Badge>
                    {data.fallback_used && (
                        <Badge className="text-xs bg-amber-100 text-amber-800 border-amber-300">
                            pdfplumber fallback
                        </Badge>
                    )}
                </div>
                <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => onCopy(data.markdown)}>
                        <ClipboardCopy className="h-3.5 w-3.5 mr-1.5"/>Copy
                    </Button>
                    <Button size="sm" variant="outline"
                            onClick={() => onDownload(data.markdown, data.filename || 'converted')}>
                        <Download className="h-3.5 w-3.5 mr-1.5"/>Download .md
                    </Button>
                    {onReset && (
                        <Button size="sm" variant="ghost" onClick={onReset}>
                            <RefreshCw className="h-3.5 w-3.5 mr-1.5"/>New
                        </Button>
                    )}
                </div>
            </div>

            {/* Scanned-PDF warning */}
            {isPdf && data.is_scanned_pdf && (
                <Alert className="border-amber-300 bg-amber-50 dark:bg-amber-950">
                    <AlertTriangle className="h-4 w-4 text-amber-600"/>
                    <AlertDescription className="text-amber-800 dark:text-amber-200">
                        <strong>Scanned PDF detected.</strong> No text could be extracted — this document
                        appears to contain only images. Use OCR software (e.g. Adobe Acrobat, Google Docs,
                        or ABBYY FineReader) to create a text-searchable version first.
                    </AlertDescription>
                </Alert>
            )}

            {/* Multi-column layout notice for PDFs */}
            {isPdf && !data.is_scanned_pdf && data.char_count > 0 && (
                <Alert className="border-blue-200 bg-blue-50 dark:bg-blue-950 py-2">
                    <Info className="h-3.5 w-3.5 text-blue-500"/>
                    <AlertDescription className="text-blue-700 dark:text-blue-300 text-xs">
                        Multi-column PDF layouts are linearised (text flows top-to-bottom). Review the
                        output and re-order sections if needed.
                        {data.fallback_used && ' pdfplumber was used for better table extraction.'}
                    </AlertDescription>
                </Alert>
            )}

            {/* Markdown preview */}
            <ScrollArea className="h-96 w-full rounded-md border bg-slate-50 dark:bg-slate-900">
                <pre className="p-4 text-xs font-mono whitespace-pre-wrap break-words leading-relaxed">
                    {data.markdown || '(no content extracted)'}
                </pre>
            </ScrollArea>
        </div>
    );
}
// ── History row ───────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: HistoryRow
 * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function HistoryRow({entry}) {
    const isPdf = entry.extension === '.pdf';
    return (
        <div className="flex items-start gap-3 py-2.5 border-b last:border-0">
            <div className="shrink-0 mt-0.5">{getFileIcon(entry.filename || '')}</div>
            <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">
                    {entry.filename || entry.source_url || '—'}
                </p>
                <div className="flex flex-wrap items-center gap-2 mt-1">
                    <span className="text-xs text-slate-500">
                        <Clock className="inline h-3 w-3 mr-0.5"/>
                        {formatDateTime(entry.created_at)}
                    </span>
                    {entry.char_count > 0 && (
                        <span className="text-xs text-slate-400">
                            {entry.char_count?.toLocaleString()} chars
                        </span>
                    )}
                    {entry.conversion_type === 'url' && (
                        <Badge variant="outline" className="text-xs">URL</Badge>
                    )}
                    {isPdf && entry.fallback_used && (
                        <Badge className="text-xs bg-amber-100 text-amber-700 border-amber-300">
                            pdfplumber
                        </Badge>
                    )}
                    {isPdf && entry.is_scanned_pdf && (
                        <Badge className="text-xs bg-red-100 text-red-700 border-red-300">
                            scanned PDF
                        </Badge>
                    )}
                </div>
            </div>
            <Badge
                variant="outline"
                className={`text-xs shrink-0 ${entry.status === 'success'
                    ? 'border-green-300 text-green-700 bg-green-50'
                    : 'border-red-300 text-red-700 bg-red-50'}`}
            >
                {entry.status}
            </Badge>
        </div>
    );
}
// ── Main page ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: DocumentConverterPage
 * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function DocumentConverterPage() {
    const {api} = useAuth();

    // File upload state
    const [dragOver, setDragOver] = useState(false);
    const [selectedFile, setSelectedFile] = useState(null);
    const [converting, setConverting] = useState(false);
    const [result, setResult] = useState(null);
    const [error, setError] = useState(null);
    const fileInputRef = useRef(null);

    // URL state
    const [url, setUrl] = useState('');
    const [urlConverting, setUrlConverting] = useState(false);
    const [urlResult, setUrlResult] = useState(null);
    const [urlError, setUrlError] = useState(null);

    // History state
    const [history, setHistory] = useState([]);
    const [historyLoading, setHistoryLoading] = useState(false);

    // ── Handlers ──────────────────────────────────────────────────────────────

    const handleFileSelect = useCallback((file) => {
        if (!file) return;
        setSelectedFile(file);
        setResult(null);
        setError(null);
    }, []);

    const handleDrop = useCallback((e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files[ 0 ];
        if (file) handleFileSelect(file);
    }, [handleFileSelect]);
    /**
     * @generated FunctionHeader
     * Function: handleConvertFile
     * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleConvertFile = async () => {
        if (!selectedFile) return;
        setConverting(true);
        setError(null);
        setResult(null);

        const formData = new FormData();
        formData.append('file', selectedFile);

        try {
            const res = await api.post('/document-converter/convert', formData, {
                headers: {'Content-Type': 'multipart/form-data'},
            });
            setResult(res.data);
            toast.success('Document converted successfully');
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Conversion failed';
            setError(msg);
            toast.error(msg);
        } finally {
            setConverting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleConvertUrl
     * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleConvertUrl = async () => {
        if (!url.trim()) return;
        setUrlConverting(true);
        setUrlError(null);
        setUrlResult(null);

        try {
            const res = await api.post('/document-converter/convert-url', {url: url.trim()});
            setUrlResult(res.data);
            toast.success('URL converted successfully');
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Conversion failed';
            setUrlError(msg);
            toast.error(msg);
        } finally {
            setUrlConverting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: loadHistory
     * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const loadHistory = async () => {
        setHistoryLoading(true);
        try {
            const res = await api.get('/document-converter/history');
            setHistory(res.data.entries || []);
        } catch {
            // non-critical — silently fail
        } finally {
            setHistoryLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: copyToClipboard
     * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const copyToClipboard = (text) => {
        navigator.clipboard.writeText(text).then(() => toast.success('Copied to clipboard'));
    };
    /**
     * @generated FunctionHeader
     * Function: downloadMarkdown
     * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const downloadMarkdown = (markdown, filename) => {
        const blob = new Blob([markdown], {type: 'text/markdown'});
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = filename.replace(/\.[^.]+$/, '') + '.md';
        a.click();
        URL.revokeObjectURL(href);
    };
    /**
     * @generated FunctionHeader
     * Function: resetFile
     * Path: frontend/src/pages/dashboard/DocumentConverterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const resetFile = () => {
        setSelectedFile(null);
        setResult(null);
        setError(null);
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    // ── Render ────────────────────────────────────────────────────────────────

    return (
        <div className="p-6 max-w-4xl mx-auto space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
                    Document Converter
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    Convert PDF, Word, Excel, and other documents to Markdown using{' '}
                    <a href="https://github.com/microsoft/markitdown"
                       target="_blank" rel="noopener noreferrer"
                       className="text-blue-600 hover:underline">
                        Microsoft MarkItDown
                    </a>
                    {' '}with pdfplumber fallback for PDFs.
                </p>
            </div>

            <Tabs defaultValue="file" onValueChange={(v) => v === 'history' && loadHistory()}>
                <TabsList>
                    <TabsTrigger value="file">
                        <Upload className="h-3.5 w-3.5 mr-1.5"/>Upload File
                    </TabsTrigger>
                    <TabsTrigger value="url">
                        <Globe className="h-3.5 w-3.5 mr-1.5"/>From URL
                    </TabsTrigger>
                    <TabsTrigger value="history">
                        <History className="h-3.5 w-3.5 mr-1.5"/>History
                    </TabsTrigger>
                </TabsList>

                {/* ── FILE TAB ── */}
                <TabsContent value="file" className="space-y-4 mt-4">
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base">Upload a Document</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {!result ? (
                                <>
                                    {/* Drop zone */}
                                    <div
                                        data-testid="drop-zone"
                                        className={`relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-10 text-center transition-colors cursor-pointer
                                            ${dragOver
                                            ? 'border-blue-400 bg-blue-50 dark:bg-blue-950'
                                            : 'border-slate-300 dark:border-slate-600 hover:border-slate-400'}`}
                                        onDragOver={(e) => {
                                            e.preventDefault();
                                            setDragOver(true);
                                        }}
                                        onDragLeave={() => setDragOver(false)}
                                        onDrop={handleDrop}
                                        onClick={() => fileInputRef.current?.click()}
                                    >
                                        <Upload className="h-8 w-8 text-slate-400 mb-3"/>
                                        <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                                            Drop your file here, or{' '}
                                            <span className="text-blue-600">browse</span>
                                        </p>
                                        <p className="text-xs text-slate-400 mt-1">
                                            PDF · Word · Excel · PowerPoint · HTML · CSV · TXT · Max 20 MB
                                        </p>
                                        <input
                                            ref={fileInputRef}
                                            type="file"
                                            accept={ACCEPTED_EXTENSIONS}
                                            className="sr-only"
                                            onChange={(e) => handleFileSelect(e.target.files[ 0 ])}
                                        />
                                    </div>

                                    {/* PDF limitations notice */}
                                    <Alert className="border-slate-200 bg-slate-50 dark:bg-slate-900 py-2">
                                        <Info className="h-3.5 w-3.5 text-slate-400"/>
                                        <AlertDescription className="text-xs text-slate-500">
                                            <strong>PDF notes:</strong> Scanned (image-only) PDFs cannot be
                                            converted — no OCR is performed. Multi-column layouts are
                                            linearised. If MarkItDown extracts little content, pdfplumber
                                            is used automatically as a fallback.
                                        </AlertDescription>
                                    </Alert>

                                    {/* Selected file */}
                                    {selectedFile && (
                                        <div className="flex items-center gap-3 rounded-md border px-4 py-3
                                                        bg-slate-50 dark:bg-slate-900">
                                            {getFileIcon(selectedFile.name)}
                                            <div className="flex-1 min-w-0">
                                                <p className="text-sm font-medium truncate">
                                                    {selectedFile.name}
                                                </p>
                                                <p className="text-xs text-slate-500">
                                                    {formatBytes(selectedFile.size)}
                                                </p>
                                            </div>
                                            <Button
                                                onClick={handleConvertFile}
                                                disabled={converting}
                                                size="sm"
                                            >
                                                {converting
                                                    ? <><Loader2
                                                        className="h-3.5 w-3.5 mr-1.5 animate-spin"/>Converting…</>
                                                    : 'Convert to Markdown'}
                                            </Button>
                                        </div>
                                    )}

                                    {error && (
                                        <Alert variant="destructive">
                                            <AlertCircle className="h-4 w-4"/>
                                            <AlertDescription>{error}</AlertDescription>
                                        </Alert>
                                    )}
                                </>
                            ) : (
                                <MarkdownOutput
                                    data={result}
                                    onCopy={copyToClipboard}
                                    onDownload={downloadMarkdown}
                                    onReset={resetFile}
                                />
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── URL TAB ── */}
                <TabsContent value="url" className="space-y-4 mt-4">
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base">Convert from URL</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="url-input">Document or web page URL</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="url-input"
                                        data-testid="url-input"
                                        placeholder="https://example.com/document.pdf"
                                        value={url}
                                        onChange={(e) => {
                                            setUrl(e.target.value);
                                            setUrlResult(null);
                                            setUrlError(null);
                                        }}
                                        onKeyDown={(e) => e.key === 'Enter' && handleConvertUrl()}
                                    />
                                    <Button
                                        onClick={handleConvertUrl}
                                        disabled={urlConverting || !url.trim()}
                                    >
                                        {urlConverting
                                            ? <><Loader2 className="h-4 w-4 mr-1.5 animate-spin"/>Converting…</>
                                            : 'Convert'}
                                    </Button>
                                </div>
                                <p className="text-xs text-slate-400">
                                    Must be a publicly accessible http:// or https:// URL.
                                </p>
                            </div>

                            {urlError && (
                                <Alert variant="destructive">
                                    <AlertCircle className="h-4 w-4"/>
                                    <AlertDescription>{urlError}</AlertDescription>
                                </Alert>
                            )}

                            {urlResult && (
                                <MarkdownOutput
                                    data={urlResult}
                                    onCopy={copyToClipboard}
                                    onDownload={downloadMarkdown}
                                />
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── HISTORY TAB ── */}
                <TabsContent value="history" className="mt-4">
                    <Card>
                        <CardHeader className="pb-3 flex-row items-center justify-between">
                            <CardTitle className="text-base">Conversion History</CardTitle>
                            <Button size="sm" variant="outline" onClick={loadHistory}
                                    disabled={historyLoading}>
                                <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${historyLoading ? 'animate-spin' : ''}`}/>
                                Refresh
                            </Button>
                        </CardHeader>
                        <CardContent>
                            {historyLoading ? (
                                <div className="flex items-center justify-center py-10 text-slate-400">
                                    <Loader2 className="h-5 w-5 animate-spin mr-2"/>
                                    Loading history…
                                </div>
                            ) : history.length === 0 ? (
                                <div className="flex flex-col items-center justify-center py-10 text-slate-400">
                                    <History className="h-8 w-8 mb-2 opacity-30"/>
                                    <p className="text-sm">No conversions yet</p>
                                </div>
                            ) : (
                                <ScrollArea className="h-[480px]">
                                    {history.map((entry) => (
                                        <HistoryRow key={entry.id} entry={entry}/>
                                    ))}
                                </ScrollArea>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* Supported formats strip */}
            <Card className="bg-slate-50 dark:bg-slate-900 border-slate-200 dark:border-slate-700">
                <CardContent className="pt-4 pb-3">
                    <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">
                        Supported formats
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {[
                            {ext: 'PDF', color: 'bg-red-100 text-red-700'},
                            {ext: 'DOCX', color: 'bg-blue-100 text-blue-700'},
                            {ext: 'DOC', color: 'bg-blue-100 text-blue-700'},
                            {ext: 'XLSX', color: 'bg-green-100 text-green-700'},
                            {ext: 'XLS', color: 'bg-green-100 text-green-700'},
                            {ext: 'PPTX', color: 'bg-orange-100 text-orange-700'},
                            {ext: 'HTML', color: 'bg-purple-100 text-purple-700'},
                            {ext: 'CSV', color: 'bg-teal-100 text-teal-700'},
                            {ext: 'TXT', color: 'bg-slate-100 text-slate-700'},
                        ].map(({ext, color}) => (
                            <span key={ext}
                                  className={`px-2 py-0.5 rounded text-xs font-mono font-semibold ${color}`}>
                                {ext}
                            </span>
                        ))}
                    </div>
                    <p className="text-xs text-slate-400 mt-2">
                        Rate limit: 6 conversions/minute per building. Max 20 MB per file.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
}
