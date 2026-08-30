/**
 * Manual mock for react-pdf (pure ESM — cannot be transformed by ts-jest/CommonJS).
 * Tests that need custom behaviour override with jest.mock('react-pdf', () => ...).
 */
import React from "react";

export const pdfjs = {
    GlobalWorkerOptions: {workerSrc: ""},
    version: "5.0.0",
};

export const Document = ({
                             children,
                             onLoadSuccess,
                             file,
                         }: {
    children?: React.ReactNode;
    onLoadSuccess?: (data: { numPages: number }) => void;
    file?: string;
    onLoadError?: (err: Error) => void;
    loading?: React.ReactNode;
}) => {
    React.useEffect(() => {
        if (file && onLoadSuccess) onLoadSuccess({numPages: 3});
    }, [file, onLoadSuccess]);
    return <div data-testid="pdf-document">{children}</div>;
};

export const Page = ({
                         pageNumber,
                         scale,
                     }: {
    pageNumber?: number;
    scale?: number;
    renderTextLayer?: boolean;
    renderAnnotationLayer?: boolean;
}) => (
    <div
        data-testid="pdf-page"
        data-page={pageNumber}
        data-scale={scale}
    />
);
