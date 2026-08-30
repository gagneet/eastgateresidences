import {TextDecoder, TextEncoder} from 'util';

global.TextEncoder = TextEncoder;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
global.TextDecoder = TextDecoder as any;

// Mock ResizeObserver for Recharts/Tremor
global.ResizeObserver = jest.fn().mockImplementation(() => ({
    observe: jest.fn(),
    unobserve: jest.fn(),
    disconnect: jest.fn(),
}));

Object.defineProperty(window, 'scrollTo', {
    value: jest.fn(),
    writable: true,
});

// jsdom's File/Blob has no .text() (confirmed missing as of jest-environment-jsdom
// 30.2.0 — real browsers implement it fully). Any component reading an uploaded
// file via `await file.text()` (CSV/document upload flows are common in this app)
// needs this polyfill or the call throws "f.text is not a function" in tests.
if (typeof Blob !== 'undefined' && !Blob.prototype.text) {
    Blob.prototype.text = function (this: Blob): Promise<string> {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = () => reject(reader.error);
            reader.readAsText(this);
        });
    };
}
