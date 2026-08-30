"use client";
import {HelpCircle} from "lucide-react";
import {Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,} from "@/components/ui/tooltip";

interface MetricHelpProps {
    text: string;
    side?: "top" | "bottom" | "left" | "right";
}
/**
 * @generated FunctionHeader
 * Function: MetricHelp
 * Path: frontend/src/components/shared/MetricHelp.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function MetricHelp({text, side = "top"}: MetricHelpProps) {
    return (
        <TooltipProvider>
            <Tooltip>
                <TooltipTrigger asChild>
          <span
              tabIndex={0}
              role="button"
              aria-label="Help information"
              className="ml-1 shrink-0 inline-flex items-center focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-full outline-none"
          >
            <HelpCircle className="h-3.5 w-3.5 text-muted-foreground cursor-help"/>
          </span>
                </TooltipTrigger>
                <TooltipContent side={side} className="max-w-xs text-xs leading-relaxed">
                    {text}
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
