import type { ReactNode } from "react";
import type { LoadedAnalysis } from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { ErrorState } from "./States";

/**
 * One data block's states in one place: the ready view, or a deliberate empty, loading or failed
 * state. A page with several slots passes `retry={false}` on all but one, so one failure offers
 * one Retry.
 */
export function DataSlot({
  empty,
  skeleton,
  failed,
  retry = true,
  children,
}: {
  empty: ReactNode;
  skeleton: ReactNode;
  /** What this block failed to load, in one sentence. */
  failed: string;
  retry?: boolean;
  /** The ready view. Without one, the block stays honestly empty even with an analysis loaded. */
  children?: ReactNode | ((loaded: LoadedAnalysis) => ReactNode);
}) {
  const { state, retry: again } = useAnalysis();
  if (state.status === "loading") return <>{skeleton}</>;
  if (state.status === "error") {
    return <ErrorState what={failed} lastGood={state.lastGood} onRetry={retry ? again : undefined} />;
  }
  if (state.status === "ready" && children) {
    return <>{typeof children === "function" ? children(state.loaded) : children}</>;
  }
  return <>{empty}</>;
}
