import React from "react";

// Manual mock for react-pdf-highlighter-extended (ESM-only package)
// Provides minimal shims so PDFAnnotatorDialog tests can render without the real library.

// ── Contexts ───────────────────────────────────────────────────────────────────

// PdfHighlighter context: exposes setTip/hideTip (used by HighlightRenderer),
// plus internal helpers so MonitoredHighlightContainer can attribute tips to the
// correct highlight without needing real hover events.
const PdfHighlighterCtx = React.createContext<{
    setTip: (entry: { position?: any; content: React.ReactNode } | null) => void;
    hideTip: () => void;
    _tips: Record<string, React.ReactNode>;
    _currentHighlightIdRef: React.MutableRefObject<string | null>;
}>({
    setTip: () => {
    },
    hideTip: () => {
    },
    _tips: {},
    _currentHighlightIdRef: {current: null},
});

// Per-highlight context: gives HighlightRenderer the current highlight + scroll state.
const HighlightContainerCtx = React.createContext<{ highlight: any; isScrolledTo: boolean }>({
    highlight: null,
    isScrolledTo: false,
});

export const usePdfHighlighterContext = () => {
    const {setTip, hideTip} = React.useContext(PdfHighlighterCtx);
    return {setTip, hideTip};
};

export const useHighlightContainerContext = <T = any>(): { highlight: T; isScrolledTo: boolean } =>
    React.useContext(HighlightContainerCtx) as { highlight: T; isScrolledTo: boolean };

// ── PdfLoader ──────────────────────────────────────────────────────────────────
export const PdfLoader: React.FC<{
    document: string;
    workerSrc?: string;
    beforeLoad?: React.ReactNode;
    children: (pdfDocument: any) => React.ReactNode;
}> = ({children, beforeLoad}) => {
    const [ready, setReady] = React.useState(false);
    React.useEffect(() => {
        setReady(true);
    }, []);
    if (!ready) return <>{beforeLoad ?? null}</>;
    return <>{children({numPages: 2})}</>;
};

// ── PdfHighlighter ────────────────────────────────────────────────────────────
// Renders children once per highlight with appropriate contexts so that both
// useHighlightContainerContext and usePdfHighlighterContext work inside HighlightRenderer.
export const PdfHighlighter: React.FC<{
    pdfDocument?: any;
    highlights: any[];
    onScrollAway?: () => void;
    enableAreaSelection?: (e: MouseEvent) => boolean;
    selectionTip?: React.ReactNode;
    onSelection?: (selection: any) => void;
    utilsRef?: (ref: any) => void;
    style?: React.CSSProperties;
    children: React.ReactNode;
}> = ({highlights, children}) => {
    const [tips, setTips] = React.useState<Record<string, React.ReactNode>>({});
    // Ref is mutated synchronously by MonitoredHighlightContainer before calling onMouseEnter,
    // so that setTip knows which highlight's popup to populate.
    const currentHighlightIdRef = React.useRef<string | null>(null);

    const ctx = React.useMemo(
        () => ({
            setTip: (entry: { position?: any; content: React.ReactNode } | null) => {
                const id = currentHighlightIdRef.current;
                if (!id) return;
                setTips((prev) => ({...prev, [id]: entry?.content ?? null}));
            },
            hideTip: () => {
                const id = currentHighlightIdRef.current;
                if (id) setTips((prev) => ({...prev, [id]: null}));
            },
            _tips: tips,
            _currentHighlightIdRef: currentHighlightIdRef,
        }),
        [tips]
    );

    return (
        <PdfHighlighterCtx.Provider value={ctx}>
            <div data-testid="pdf-highlighter">
                {highlights.map((h: any) => (
                    <HighlightContainerCtx.Provider key={h.id} value={{highlight: h, isScrolledTo: false}}>
                        {children}
                    </HighlightContainerCtx.Provider>
                ))}
            </div>
        </PdfHighlighterCtx.Provider>
    );
};

// ── MonitoredHighlightContainer ────────────────────────────────────────────────
// In tests we fire onMouseEnter immediately on mount so that HighlightRenderer's
// setTip call populates _tips[highlightId] without requiring real pointer events.
// Before firing, we set _currentHighlightIdRef so setTip attributes the content
// to the correct highlight.
export const MonitoredHighlightContainer: React.FC<{
    key?: string;
    highlightIndex?: number;
    onMouseEnter?: () => void;
    onMouseLeave?: () => void;
    onMouseOver?: (content: React.ReactNode) => void;
    onMouseOut?: () => void;
    isScrolledTo?: boolean;
    highlight?: any;
    popup?: React.ReactNode;
    children: React.ReactNode;
}> = ({highlight, children, popup, onMouseEnter, onMouseLeave}) => {
    const {highlight: ctxHighlight} = React.useContext(HighlightContainerCtx);
    const pdfCtx = React.useContext(PdfHighlighterCtx);
    const h = ctxHighlight ?? highlight;

    React.useEffect(() => {
        if (!onMouseEnter || !h?.id) return;
        // Set the current highlight id BEFORE calling onMouseEnter so that setTip
        // (called synchronously inside onMouseEnter) can attribute the tip to this highlight.
        pdfCtx._currentHighlightIdRef.current = h.id;
        onMouseEnter();
        pdfCtx._currentHighlightIdRef.current = null;
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const tipContent = pdfCtx._tips[h?.id];

    return (
        <div
            data-testid={`highlight-container-${h?.id}`}
            onMouseEnter={() => {
                if (h?.id) pdfCtx._currentHighlightIdRef.current = h.id;
                onMouseEnter?.();
                if (h?.id) pdfCtx._currentHighlightIdRef.current = null;
            }}
            onMouseLeave={onMouseLeave}
        >
            {children}
            {/* Render popup prop (library pattern) or the tip set via setTip (component pattern) */}
            <div data-testid={`highlight-popup-${h?.id}`}>
                {popup ?? tipContent ?? null}
            </div>
        </div>
    );
};

// ── Text / Area highlights ─────────────────────────────────────────────────────
export const TextHighlight: React.FC<{ highlight: any; isScrolledTo: boolean }> = ({highlight}) => (
    <mark data-testid={`text-highlight-${highlight.id}`}/>
);

export const AreaHighlight: React.FC<{
    highlight: any;
    isScrolledTo: boolean;
    onChange?: (rect: any) => void;
}> = ({highlight}) => (
    <div data-testid={`area-highlight-${highlight.id}`}/>
);
