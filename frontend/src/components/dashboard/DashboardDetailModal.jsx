"use client";
import React from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Button } from '../ui/button';
import { ArrowRight, Info } from 'lucide-react';
import { motion } from 'framer-motion';
/**
 * @generated FunctionHeader
 * Function: DashboardDetailModal
 * Path: frontend/src/components/dashboard/DashboardDetailModal.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const DashboardDetailModal = ({isOpen, onClose, title, description, children, actionLabel, onAction}) => {
    return (
        <Dialog open={isOpen} onOpenChange={onClose}>
            <DialogContent
                className="sm:max-w-[600px] rounded-[2rem] border-none shadow-2xl p-0 overflow-hidden bg-white/95 backdrop-blur-xl">
                <div
                    className="absolute top-0 left-0 w-full h-2 bg-gradient-to-r from-primary via-secondary to-primary"/>

                <div className="p-8">
                    <DialogHeader className="mb-6">
                        <div className="flex items-center gap-4 mb-2">
                            <div className="p-3 rounded-2xl bg-slate-900 text-white shadow-lg">
                                <Info size={24} strokeWidth={2.5}/>
                            </div>
                            <div>
                                <DialogTitle
                                    className="text-2xl font-black text-slate-900 tracking-tight">{title}</DialogTitle>
                                {description && <DialogDescription
                                    className="text-sm font-bold text-slate-400 uppercase tracking-widest mt-1">{description}</DialogDescription>}
                            </div>
                        </div>
                    </DialogHeader>

                    <motion.div
                        initial={{opacity: 0, y: 20}}
                        animate={{opacity: 1, y: 0}}
                        transition={{delay: 0.1}}
                        className="space-y-6"
                    >
                        <div className="p-6 rounded-3xl bg-slate-50 border border-slate-100 shadow-inner">
                            {children}
                        </div>

                        <div className="flex gap-4">
                            <Button
                                variant="outline"
                                onClick={onClose}
                                className="flex-1 rounded-2xl font-black uppercase tracking-widest text-[11px] h-12 border-slate-200 hover:bg-slate-50 transition-all"
                            >
                                Close
                            </Button>
                            {actionLabel && (
                                <Button
                                    onClick={onAction}
                                    className="flex-1 rounded-2xl font-black uppercase tracking-widest text-[11px] h-12 shadow-xl hover:shadow-2xl transition-all group bg-slate-900 text-white"
                                >
                                    {actionLabel}
                                    <ArrowRight size={14}
                                                className="ml-2 group-hover:translate-x-1 transition-transform"/>
                                </Button>
                            )}
                        </div>
                    </motion.div>
                </div>
            </DialogContent>
        </Dialog>
    );
};

export default DashboardDetailModal;
