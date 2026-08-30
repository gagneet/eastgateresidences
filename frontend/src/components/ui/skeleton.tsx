import {cn} from "@/lib/utils"
/**
 * @generated FunctionHeader
 * Function: Skeleton
 * Path: frontend/src/components/ui/skeleton.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Skeleton({
                      className,
                      ...props
                  }: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <div
            className={cn("animate-pulse rounded-md bg-primary/10", className)}
            {...props}
        />
    )
}

export {Skeleton}
