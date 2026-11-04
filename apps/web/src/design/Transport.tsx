import type { ReactNode } from "react";

export interface TransportState {
  /** Seconds from the first frame of the clip, matching `*_s` in the contracts. */
  currentS: number | null;
  durationS: number | null;
  frame: number | null;
  playing: boolean;
}

export const NO_MEDIA: TransportState = {
  currentS: null,
  durationS: null,
  frame: null,
  playing: false,
};

function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  const milliseconds = Math.round((seconds - whole) * 1000);
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function TransportButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick?: (() => void) | undefined;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      className={`flex size-9 items-center justify-center transition-colors duration-150 ${
        disabled ? "cursor-not-allowed text-paint-4" : "text-paint-1 hover:bg-asphalt-3"
      }`}
    >
      {children}
    </button>
  );
}

/**
 * The transport shell shared by video and twin. With no media the rail stays a dashed lane and
 * the clock stays empty: there is no position to show, so none is drawn.
 */
export function Transport({
  state = NO_MEDIA,
  reason,
  onToggle,
  onStep,
  onSeek,
  className = "",
}: {
  state?: TransportState;
  /** Why the transport is idle. Required reading when there is no media. */
  reason?: string;
  onToggle?: () => void;
  onStep?: (frames: number) => void;
  onSeek?: (seconds: number) => void;
  className?: string;
}) {
  const hasMedia = state.durationS !== null && state.durationS > 0;
  const progress = hasMedia && state.currentS !== null ? state.currentS / (state.durationS as number) : 0;
  const stepBack = onStep ? () => onStep(-1) : undefined;
  const stepForward = onStep ? () => onStep(1) : undefined;

  return (
    <div className={`flex min-h-12 items-center gap-3 ${className}`}>
      <div className="flex items-center">
        <TransportButton label="Previous frame" disabled={!hasMedia} onClick={stepBack}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
            <path d="M3 2h1.5v10H3zM12 2v10L5.5 7z" />
          </svg>
        </TransportButton>
        <TransportButton label={state.playing ? "Pause" : "Play"} disabled={!hasMedia} onClick={onToggle}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            {state.playing ? <path d="M4 2h3v12H4zM9 2h3v12H9z" /> : <path d="M4 2v12l10-6z" />}
          </svg>
        </TransportButton>
        <TransportButton label="Next frame" disabled={!hasMedia} onClick={stepForward}>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
            <path d="M11 2H9.5v10H11zM2 2v10l6.5-5z" />
          </svg>
        </TransportButton>
      </div>

      <span className="mono w-[5.5rem] shrink-0 text-paint-1">
        {state.currentS !== null ? clock(state.currentS) : <span className="absent">--:--.---</span>}
      </span>

      <div className="relative flex h-6 min-w-0 flex-1 items-center">
        {hasMedia ? (
          <input
            type="range"
            aria-label="Position in clip"
            min={0}
            max={state.durationS as number}
            step={0.001}
            value={state.currentS ?? 0}
            onChange={(event) => onSeek?.(Number(event.target.value))}
            className="h-6 w-full accent-[var(--amber)]"
            style={{ ["--progress" as string]: progress }}
          />
        ) : (
          <div role="presentation" className="w-full border-t border-dashed border-paint-4" />
        )}
      </div>

      <span className="mono hidden w-[5.5rem] shrink-0 text-right text-paint-3 sm:block">
        {state.durationS !== null ? clock(state.durationS) : <span className="absent">--:--.---</span>}
      </span>

      {!hasMedia && reason ? (
          <span className="mono hidden shrink-0 text-paint-3 lg:inline">
            {reason}
          </span>
      ) : null}
    </div>
  );
}
