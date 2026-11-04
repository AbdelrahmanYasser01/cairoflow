import type { ReactNode } from "react";

/**
 * An unboxed instrument group: a title on a hairline. `overlay` is the one boxed case, for an
 * instrument sitting on a canvas, where it needs an opaque ground to stay legible.
 */
export function Panel({
  id,
  title,
  meta,
  overlay = false,
  className = "",
  children,
}: {
  /** For an in-page link to land on. */
  id?: string;
  title: ReactNode;
  meta?: ReactNode;
  overlay?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <section {...(id ? { id } : {})} className={`${overlay ? "border border-rule-2 bg-asphalt-1 p-4" : ""} ${className}`}>
      {/* A fixed floor to the header: rules of neighbouring panels land on the same pixel. */}
      <header className="flex min-h-8 flex-wrap items-end justify-between gap-x-4 gap-y-1 border-b border-rule-2 pb-2">
        <h2 className="text-head leading-6 font-medium tracking-[-0.005em]">{title}</h2>
        {meta ? <div className="min-w-0">{meta}</div> : null}
      </header>
      {children}
    </section>
  );
}
