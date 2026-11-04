import type { ReactNode } from "react";
import { Button } from "./Controls";

/**
 * A deliberate absence. `stage` fills a canvas; `block` keeps a section's frame;
 * `line` is one sentence for a slot that stays small.
 */
export function EmptyState({
  title,
  reason,
  action,
  size = "block",
  className = "",
}: {
  title: string;
  reason?: string;
  action?: ReactNode;
  size?: "stage" | "block" | "line";
  className?: string;
}) {
  if (size === "line") {
    return <p className={`border-t border-rule-1 pt-3 text-body text-paint-2 ${className}`}>{title}</p>;
  }
  if (size === "stage") {
    return (
      <div className={`max-w-[34rem] ${className}`}>
        <p className="font-display text-display leading-none font-medium tracking-[-0.015em] text-paint-1">
          {title}
        </p>
        {reason ? <p className="mt-3 text-paint-2">{reason}</p> : null}
        {action ? <div className="mt-5 flex flex-wrap items-center gap-5">{action}</div> : null}
      </div>
    );
  }
  return (
    <div className={className}>
      <p className="font-display text-head leading-6 font-medium text-paint-1">{title}</p>
      {reason ? <p className="mt-1 leading-snug text-paint-2">{reason}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

/** A failure stays inside the block that failed and says when the data was last good. */
export function ErrorState({
  what,
  lastGood,
  onRetry,
  size = "block",
  className = "",
}: {
  what: string;
  lastGood?: string | undefined;
  onRetry?: (() => void) | undefined;
  size?: "stage" | "block";
  className?: string;
}) {
  const stage = size === "stage";
  return (
    <div role="alert" className={`${stage ? "max-w-[34rem]" : ""} ${className}`}>
      <p
        className={`flex items-baseline gap-2.5 font-display font-medium ${
          stage ? "text-display leading-none tracking-[-0.015em]" : "text-head leading-6"
        }`}
      >
        <span aria-hidden="true" className={`inline-block shrink-0 bg-stop ${stage ? "size-3" : "size-2"}`} />
        {what}
      </p>
      <p className="mt-2 leading-snug text-paint-3">
        {lastGood ? `Last good: ${lastGood}` : "Nothing has loaded in this session."}
      </p>
      {onRetry ? (
        <Button tone={stage ? "quiet" : "text"} className={stage ? "mt-5" : "mt-2"} onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

/** A flat bar at the real size of what it stands for. No shimmer, no layout shift. */
export function Skeleton({
  width = "100%",
  height = 12,
  className = "",
}: {
  width?: number | string;
  height?: number | string;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`block animate-pulse bg-asphalt-3 ${className}`}
      style={{ width, height }}
    />
  );
}

/** Rows at the pitch of the table they stand for. */
export function SkeletonRows({ rows = 4, pitch = 40 }: { rows?: number; pitch?: number }) {
  return (
    <div role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, row) => (
        <div
          key={row}
          className="flex items-center justify-between gap-6 border-b border-rule-1"
          style={{ height: pitch }}
        >
          <Skeleton width={`${46 - (row % 3) * 8}%`} />
          <Skeleton width="18%" />
        </div>
      ))}
    </div>
  );
}
