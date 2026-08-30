"use client";

import React from "react";
import {Info} from "lucide-react";
import {Popover, PopoverContent, PopoverTrigger,} from "@/components/ui/popover";

interface InfoButtonProps {
    title: string;
    description: string | React.ReactNode;
    dataSources?: string[];
    logic?: string | React.ReactNode;
    className?: string;
}
/**
 * @generated FunctionHeader
 * Function: InfoButton
 * Path: frontend/src/components/finance/premium/InfoButton.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const InfoButton = ({
                               title,
                               description,
                               dataSources,
                               logic,
                               className = "",
                           }: InfoButtonProps) => {
    return (
        <Popover>
            <PopoverTrigger asChild>
                <button
                    className={`p-1.5 rounded-full hover:bg-muted text-muted-foreground hover:text-primary transition-colors ${className}`}
                    aria-label={`Information about ${title}`}
                >
                    <Info size={16}/>
                </button>
            </PopoverTrigger>
            {/* @ts-ignore */}
            <PopoverContent className="w-80 p-6 rounded-xl border border-border bg-popover shadow-md"
                            align="end">
                <div className="space-y-4">
                    <div className="flex items-center gap-2">
                        <div className="p-2 rounded-lg bg-primary/10 text-primary">
                            <Info size={16}/>
                        </div>
                        <h4 className="font-semibold text-foreground tracking-tight uppercase text-xs">{title} Overview</h4>
                    </div>

                    <div className="space-y-2">
                        <p className="text-xs text-muted-foreground font-medium leading-relaxed">
                            {description}
                        </p>
                    </div>

                    {dataSources && dataSources.length > 0 && (
                        <div className="space-y-1.5">
                            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Data Sources</span>
                            <div className="flex flex-wrap gap-1.5">
                                {dataSources.map((source) => (
                                    <span key={source}
                                          className="text-[9px] font-bold text-primary bg-primary/10 px-2 py-0.5 rounded-md border border-primary/20">
                    {source}
                  </span>
                                ))}
                            </div>
                        </div>
                    )}

                    {logic && (
                        <div className="space-y-1.5 pt-2 border-t border-border">
                            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Intelligence Logic</span>
                            <div className="text-[10px] text-muted-foreground font-medium leading-relaxed italic">
                                {logic}
                            </div>
                        </div>
                    )}
                </div>
            </PopoverContent>
        </Popover>
    );
};

export default InfoButton;
