"use client";
import React from "react";
import {AlertTriangle, RefreshCw} from "lucide-react";

// ─────────────────────────────────────────────────────────────────────────────
// FinanceErrorBoundary — catches render errors in finance chart components
// Wraps: ForecastChart, CashflowChart, HealthScoreCard,
//        LevyProjectionChart, LevySimulator
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
    children: React.ReactNode;
    fallback?: React.ReactNode;
    componentName?: string;
}

interface State {
    hasError: boolean;
    error?: Error;
}

export class FinanceErrorBoundary extends React.Component<Props, State> {
    /**
     * @generated FunctionHeader
     * Function: FinanceErrorBoundary.constructor
     * Path: frontend/src/components/finance/FinanceErrorBoundary.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    constructor(props: Props) {
        super(props);
        this.state = {hasError: false};
    }
    /**
     * @generated FunctionHeader
     * Function: FinanceErrorBoundary.getDerivedStateFromError
     * Path: frontend/src/components/finance/FinanceErrorBoundary.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    static getDerivedStateFromError(error: Error): State {
        return {hasError: true, error};
    }
    /**
     * @generated FunctionHeader
     * Function: FinanceErrorBoundary.componentDidCatch
     * Path: frontend/src/components/finance/FinanceErrorBoundary.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    componentDidCatch(error: Error, info: React.ErrorInfo): void {
        console.error(
            `[FinanceErrorBoundary] ${this.props.componentName ?? "chart"} error:`,
            error,
            info.componentStack,
        );
    }

    handleRetry = (): void => {
        this.setState({hasError: false, error: undefined});
    };
    /**
     * @generated FunctionHeader
     * Function: FinanceErrorBoundary.render
     * Path: frontend/src/components/finance/FinanceErrorBoundary.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    render(): React.ReactNode {
        if (this.state.hasError) {
            if (this.props.fallback) {
                return this.props.fallback;
            }
            return (
                <div
                    className="flex flex-col items-center justify-center gap-3 py-8 px-4
                     bg-amber-50 border border-amber-200 rounded-xl text-center"
                    data-testid={`error-boundary-${this.props.componentName ?? "chart"}`}
                >
                    <AlertTriangle className="w-6 h-6 text-amber-500"/>
                    <div>
                        <p className="text-sm font-semibold text-amber-800">
                            {this.props.componentName
                                ? `${this.props.componentName} temporarily unavailable`
                                : "Chart temporarily unavailable"}
                        </p>
                        <p className="text-xs text-amber-600 mt-0.5">
                            Refresh to retry or contact support if the issue persists.
                        </p>
                    </div>
                    <button
                        onClick={this.handleRetry}
                        className="flex items-center gap-1.5 text-xs font-medium text-amber-700
                       hover:text-amber-900 underline underline-offset-2"
                    >
                        <RefreshCw className="w-3 h-3"/>
                        Retry
                    </button>
                </div>
            );
        }

        return this.props.children;
    }
}
// ─────────────────────────────────────────────────────────────────────────────
// withFinanceErrorBoundary — HOC convenience wrapper
// Usage: export default withFinanceErrorBoundary(MyChart, "MyChart")
// ─────────────────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: withFinanceErrorBoundary
 * Path: frontend/src/components/finance/FinanceErrorBoundary.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function withFinanceErrorBoundary<P extends object>(
    Component: React.ComponentType<P>,
    componentName?: string,
): React.FC<P> {
    /**
     * @generated FunctionHeader
     * Function: Wrapped
     * Path: frontend/src/components/finance/FinanceErrorBoundary.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const Wrapped: React.FC<P> = (props) => (
        <FinanceErrorBoundary componentName={componentName ?? Component.displayName ?? Component.name}>
            <Component {...props} />
        </FinanceErrorBoundary>
    );
    Wrapped.displayName = `WithErrorBoundary(${componentName ?? Component.name})`;
    return Wrapped;
}

export default FinanceErrorBoundary;
