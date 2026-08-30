"use client";

import React, {useEffect, useRef} from "react";
import {animate, motion, useInView, useMotionValue, useTransform} from "framer-motion";

interface CountUpProps {
    to: number;
    duration?: number;
    delay?: number;
    prefix?: string;
    suffix?: string;
    decimals?: number;
}
/**
 * @generated FunctionHeader
 * Function: CountUp
 * Path: frontend/src/components/dashboard/premium/CountUp.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const CountUp = ({
                            to,
                            duration = 1.5,
                            delay = 0,
                            prefix = "",
                            suffix = "",
                            decimals = 0
                        }: CountUpProps) => {
    const count = useMotionValue(0);
    const rounded = useTransform(count, (latest) => {
        return prefix + latest.toLocaleString(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        }) + suffix;
    });

    const ref = useRef(null);
    const isInView = useInView(ref, {once: true});

    useEffect(() => {
        if (isInView) {
            animate(count, to, {
                duration,
                delay,
                ease: "easeOut",
            });
        }
    }, [isInView, to, duration, delay, count]);

    return <motion.span ref={ref}>{rounded}</motion.span>;
};

export default CountUp;
