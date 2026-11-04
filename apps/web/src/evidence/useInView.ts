import { useEffect, useState } from "react";

/** The band of the viewport that counts as "reading here": below the index, above the lower half. */
const BAND = "-15% 0px -55% 0px";

/** Which section is in view, for the index. */
export function useInView(ids: readonly string[]): string | null {
  const [active, setActive] = useState<string | null>(null);
  const key = ids.join(",");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.isIntersecting ? entry.boundingClientRect.top : Infinity);
        const visible = [...seen.entries()].filter(([, top]) => top !== Infinity).sort((a, b) => a[1] - b[1]);
        if (visible[0]) setActive(visible[0][0]);
      },
      { rootMargin: BAND },
    );
    let frame = 0;
    let tries = 0;
    const attach = () => {
      const found = key.split(",").map((id) => document.getElementById(id)).filter((node): node is HTMLElement => node !== null);
      found.forEach((node) => observer.observe(node));
      if (found.length < key.split(",").length && (tries += 1) < 120) frame = requestAnimationFrame(attach);
    };
    attach();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [key]);
  return active;
}
