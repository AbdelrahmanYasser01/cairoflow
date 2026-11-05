import { motion } from "motion/react";
import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ROUTES } from "../shell/routes";
import { instrumentVariants } from "./motion";

/** One instrument taking its place as a page assembles. Staggered by the route container. */
export function Instrument({ className = "", children }: { className?: string; children: ReactNode }) {
  return (
    <motion.div variants={instrumentVariants} className={className}>
      {children}
    </motion.div>
  );
}

/** "03 / Vision", from the route on screen: where this page sits in the console. */
function useStation(): string | null {
  const { pathname } = useLocation();
  const index = ROUTES.findIndex(({ path }) => path === pathname);
  const route = ROUTES[index];
  return route ? `${String(index + 1).padStart(2, "0")} / ${route.short}` : null;
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
  const station = useStation();
  return (
    <Instrument className={`flex flex-wrap items-start justify-between gap-x-6 gap-y-3 pt-7 pb-6 ${className}`}>
      <div className="min-w-0">
        {station ? <p className="eyebrow eyebrow-dot m-0 mb-2.5">{station}</p> : null}
        <h1 className="text-title leading-none font-medium tracking-[-0.012em]">{title}</h1>
        {sub ? <div className="mt-2">{sub}</div> : null}
      </div>
      {meta ? <div className="flex items-center gap-5">{meta}</div> : null}
    </Instrument>
  );
}
