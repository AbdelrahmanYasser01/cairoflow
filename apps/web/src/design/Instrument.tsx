import { motion } from "motion/react";
import type { ReactNode } from "react";
import { instrumentVariants } from "./motion";

/** One instrument taking its place as a page assembles. Staggered by the route container. */
export function Instrument({ className = "", children }: { className?: string; children: ReactNode }) {
  return (
    <motion.div variants={instrumentVariants} className={className}>
      {children}
    </motion.div>
  );
}

/** The heading row shared by every page but the Command Center: the title lands on one spot. */
export function PageHead({
  title,
  sub,
  meta,
  className = "",
}: {
  title: string;
  /** One line under the title. */
  sub?: ReactNode;
  meta?: ReactNode;
  className?: string;
}) {
  return (
    <Instrument className={`flex flex-wrap items-start justify-between gap-x-6 gap-y-3 pt-8 pb-6 ${className}`}>
      <div className="min-w-0">
        <h1 className="text-title leading-none font-medium">{title}</h1>
        {sub ? <div className="mt-2">{sub}</div> : null}
      </div>
      {meta ? <div className="flex items-center gap-5">{meta}</div> : null}
    </Instrument>
  );
}
