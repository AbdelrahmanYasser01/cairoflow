import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

/** A section counts as being read once its top has passed this share of the scroller's height. */
const READ_LINE = 0.4;
/** How close to the bottom counts as the end of the page, in pixels. */
const BOTTOM_SLACK = 4;
/** How long a clicked index entry outranks the end-of-page rule, in milliseconds. */
const CLICK_HOLD_MS = 2000;

function scrollerOf(node: HTMLElement | null): HTMLElement {
  for (let at = node?.parentElement ?? null; at; at = at.parentElement) {
    const { overflowY } = getComputedStyle(at);
    if ((overflowY === "auto" || overflowY === "scroll") && at.scrollHeight > at.clientHeight) return at;
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement;
}

/**
 * Which section is being read, for the index: the last one whose top has passed the read line. At the
 * end of the page the last section wins, since its top may never reach the line. A clicked entry lights
 * at once.
 */
export function useInView(ids: readonly string[]): string | null {
  const [active, setActive] = useState<string | null>(null);
  const { hash } = useLocation();
  const key = ids.join(",");
  const wanted = hash.startsWith("#") ? decodeURIComponent(hash.slice(1)) : "";

  useEffect(() => {
    const list = key.split(",").filter(Boolean);
    if (wanted && list.includes(wanted)) setActive(wanted);
  }, [key, wanted]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const list = key.split(",").filter(Boolean);
    const clickedAt = wanted && list.includes(wanted) ? performance.now() : -Infinity;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const nodes = list.map((id) => [id, document.getElementById(id)] as const).filter((pair): pair is readonly [string, HTMLElement] => pair[1] !== null);
      if (nodes.length === 0) return;
      const scroller = scrollerOf(nodes[0]![1]);
      const root = scroller === document.scrollingElement || scroller === document.documentElement;
      const height = root ? window.innerHeight : scroller.clientHeight;
      const line = (root ? 0 : scroller.getBoundingClientRect().top) + height * READ_LINE;
      let current: string = nodes[0]![0];
      for (const [id, node] of nodes) if (node.getBoundingClientRect().top <= line) current = id;
      const top = root ? window.scrollY : scroller.scrollTop;
      const atBottom = top > 0 && top + height >= scroller.scrollHeight - BOTTOM_SLACK;
      if (atBottom) {
        const recent = performance.now() - clickedAt < CLICK_HOLD_MS;
        current = recent && list.includes(wanted) ? wanted : nodes[nodes.length - 1]![0];
      }
      setActive(current);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    window.addEventListener("scroll", schedule, { capture: true, passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    // Sections mount as their data arrives, so measure again for a short while after the first paint.
    const settle = window.setInterval(schedule, 400);
    const stop = window.setTimeout(() => window.clearInterval(settle), 6000);
    schedule();
    return () => {
      cancelAnimationFrame(frame);
      window.clearInterval(settle);
      window.clearTimeout(stop);
      window.removeEventListener("scroll", schedule, { capture: true });
      window.removeEventListener("resize", schedule);
    };
  }, [key, wanted]);

  return active;
}
