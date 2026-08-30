/**
 * Manual mock for `next/navigation` used by Jest.
 *
 * Replaces the brittle moduleNameMapper path into Next's dist/ folder,
 * which can break across minor Next.js version bumps.
 *
 * Reset all mocks between tests by calling resetNavigationMocks() in
 * your beforeEach, or import it into setup.ts.
 */

// --- Router ------------------------------------------------------------------

export const mockPush = jest.fn();
export const mockReplace = jest.fn();
export const mockBack = jest.fn();
export const mockForward = jest.fn();
export const mockRefresh = jest.fn();
export const mockPrefetch = jest.fn();

export const useRouter = jest.fn(() => ({
    push: mockPush,
    replace: mockReplace,
    back: mockBack,
    forward: mockForward,
    refresh: mockRefresh,
    prefetch: mockPrefetch,
}));

// --- Pathname / search params -----------------------------------------------

export const usePathname = jest.fn(() => "/");

export const useSearchParams = jest.fn(() => new URLSearchParams());

// --- Route params ------------------------------------------------------------

export const useParams = jest.fn(() => ({}));

// --- Selected layout segments -----------------------------------------------

export const useSelectedLayoutSegment = jest.fn(() => null);
export const useSelectedLayoutSegments = jest.fn(() => []);

// --- Navigation primitives --------------------------------------------------

// Mirror real Next.js behaviour: these throw to short-circuit rendering.
// Tests asserting a redirect happens can catch the throw; others mock to no-op.
export const redirect = jest.fn((url: string) => {
    throw new Error(`NEXT_REDIRECT: ${url}`);
});

export const permanentRedirect = jest.fn((url: string) => {
    throw new Error(`NEXT_PERMANENT_REDIRECT: ${url}`);
});

export const notFound = jest.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
});

// --- Reset helper ------------------------------------------------------------

export const resetNavigationMocks = () => {
    mockPush.mockClear();
    mockReplace.mockClear();
    mockBack.mockClear();
    mockForward.mockClear();
    mockRefresh.mockClear();
    mockPrefetch.mockClear();
    (useRouter as jest.Mock).mockClear();
    (usePathname as jest.Mock).mockReset();
    (usePathname as jest.Mock).mockReturnValue("/");
    (useSearchParams as jest.Mock).mockReset();
    (useSearchParams as jest.Mock).mockReturnValue(new URLSearchParams());
    (useParams as jest.Mock).mockReset();
    (useParams as jest.Mock).mockReturnValue({});
    (useSelectedLayoutSegment as jest.Mock).mockReset();
    (useSelectedLayoutSegment as jest.Mock).mockReturnValue(null);
    (useSelectedLayoutSegments as jest.Mock).mockReset();
    (useSelectedLayoutSegments as jest.Mock).mockReturnValue([]);
    (redirect as jest.Mock).mockClear();
    (permanentRedirect as jest.Mock).mockClear();
    (notFound as jest.Mock).mockClear();
};
