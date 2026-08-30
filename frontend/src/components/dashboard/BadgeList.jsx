"use client";
import React from 'react';
import { motion } from 'framer-motion';
import { Award, CheckCircle, Medal, ShoppingBag, Star } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from "../ui/tooltip";
/**
 * @generated FunctionHeader
 * Function: BadgeIcon
 * Path: frontend/src/components/dashboard/BadgeList.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BadgeIcon = ({icon, size = 20}) => {
    switch (icon) {
        case 'medal':
            return <Medal size={size} className="text-amber-500"/>;
        case 'check-circle':
            return <CheckCircle size={size} className="text-emerald-500"/>;
        case 'shopping-bag':
            return <ShoppingBag size={size} className="text-blue-500"/>;
        case 'award':
            return <Award size={size} className="text-purple-500"/>;
        case 'star':
            return <Star size={size} className="text-yellow-500"/>;
        default:
            return <Award size={size} className="text-slate-400"/>;
    }
};
/**
 * @generated FunctionHeader
 * Function: BadgeList
 * Path: frontend/src/components/dashboard/BadgeList.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BadgeList = ({badges = [], delay = 0}) => {
    if (!badges || badges.length === 0) return null;

    return (
        <TooltipProvider>
            <div className="flex flex-wrap gap-2">
                {badges.map((badge, index) => (
                    <motion.div
                        key={badge.id}
                        initial={{opacity: 0, scale: 0.8}}
                        animate={{opacity: 1, scale: 1}}
                        transition={{delay: delay + ( index * 0.1 )}}
                    >
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <div
                                    className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-slate-100 shadow-sm cursor-help hover:border-primary/30 transition-colors">
                                    <BadgeIcon icon={badge.icon} size={16}/>
                                    <span className="text-xs font-bold text-slate-700">{badge.label}</span>
                                </div>
                            </TooltipTrigger>
                            <TooltipContent>
                                <div className="text-center p-1">
                                    <p className="font-bold mb-0.5">{badge.label}</p>
                                    <p className="text-xs text-muted-foreground">{badge.description}</p>
                                </div>
                            </TooltipContent>
                        </Tooltip>
                    </motion.div>
                ))}
            </div>
        </TooltipProvider>
    );
};

export default BadgeList;
