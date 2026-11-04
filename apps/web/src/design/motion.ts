import type { Variants } from "motion/react";

/** Durations in seconds. These mirror --dur-* in tokens.css. */
export const DUR = { fast: 0.12, base: 0.2, slow: 0.36, draw: 1.15 } as const;

export const EASE = [0.2, 0.8, 0.2, 1] as const;

/** Stagger between instruments as a page assembles, in seconds. */
export const STAGGER = 0.055;

/** Route container: holds its children back, then releases them in order. */
export const pageVariants: Variants = {
  initial: { opacity: 0 },
  enter: {
    opacity: 1,
    transition: { duration: DUR.fast, staggerChildren: STAGGER, delayChildren: 0.02 },
  },
  exit: { opacity: 0, transition: { duration: DUR.fast } },
};

/** One instrument taking its place. */
export const instrumentVariants: Variants = {
  initial: { opacity: 0, y: 10 },
  enter: { opacity: 1, y: 0, transition: { duration: DUR.slow, ease: EASE } },
};
