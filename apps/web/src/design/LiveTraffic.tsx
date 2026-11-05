import { useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";
import { projector, type PlanFrame } from "../domain/plan";
import { NO_GROUND, clock, type LoadedAnalysis, type TrackEntry } from "../lib/analysis";

/** Frames of history behind each replayed vehicle on the plan. */
const TAIL_FRAMES = 14;

export const mmss = (seconds: number) => {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
};

/**
 * The stored camera tracks replayed on a plan at their ground positions, at real speed. A local
 * cursor drives it, so the shared clock and every other page stay where they are. `clockId` names
 * an element whose text is kept on the replay's moment, in analysis time.
 */
export function LiveTraffic({ loaded, frame, clockId }: { loaded: LoadedAnalysis; frame: PlanFrame; clockId?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    const surface = canvas.current;
    const context = surface?.getContext("2d");
    if (!surface || !context || frame.width === 0 || frame.height === 0) return;
    const ratio = window.devicePixelRatio || 1;
    surface.width = Math.round(frame.width * ratio);
    surface.height = Math.round(frame.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const style = getComputedStyle(document.documentElement);
    const tone = style.getPropertyValue("--ev-approximated").trim() || "#7fd0da";
    const project = projector(frame);
    const { trajectories: t, tracks, record } = loaded;
    const scale = t.header.groundScale;
    const last = Math.max(0, record.frame_count - 1);
    let cursor = Math.min(Math.max(0, Math.round(clock.get())), last);
    let before = performance.now();
    let stamped = -Infinity;
    let handle = 0;

    const draw = (at: number) => {
      context.clearRect(0, 0, frame.width, frame.height);
      context.lineCap = "round";
      context.lineJoin = "round";
      loaded.active.forEach(at, (index, row) => {
        const entry = tracks[index] as TrackEntry;
        if (t.gx[row] === NO_GROUND) return;
        const first = Math.max(entry.first_row, row - TAIL_FRAMES);
        context.beginPath();
        let started = false;
        for (let back = first; back <= row; back += 1) {
          if (t.gx[back] === NO_GROUND) continue;
          const [px, py] = project((t.gx[back] as number) * scale, (t.gy[back] as number) * scale);
          if (started) context.lineTo(px, py);
          else {
            context.moveTo(px, py);
            started = true;
          }
        }
        context.globalAlpha = 0.32;
        context.strokeStyle = tone;
        context.lineWidth = 2;
        context.stroke();
        const [x, y] = project((t.gx[row] as number) * scale, (t.gy[row] as number) * scale);
        context.globalAlpha = 1;
        context.shadowColor = tone;
        context.shadowBlur = 10;
        context.fillStyle = tone;
        context.beginPath();
        context.arc(x, y, 2.6, 0, Math.PI * 2);
        context.fill();
        context.shadowBlur = 0;
      });
      context.globalAlpha = 1;
    };

    const stamp = (at: number, now: number) => {
      if (!clockId || now - stamped < 250) return;
      stamped = now;
      const target = document.getElementById(clockId);
      if (target) target.textContent = mmss(at / record.fps);
    };

    if (reduced) {
      draw(cursor);
      stamp(cursor, performance.now());
      return;
    }
    const step = (now: number) => {
      cursor += ((now - before) / 1000) * record.fps;
      before = now;
      if (cursor > last) cursor = 0;
      const at = Math.floor(cursor);
      draw(at);
      stamp(at, now);
      handle = requestAnimationFrame(step);
    };
    handle = requestAnimationFrame(step);
    return () => cancelAnimationFrame(handle);
  }, [loaded, frame, reduced, clockId]);

  return <canvas ref={canvas} aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" />;
}
