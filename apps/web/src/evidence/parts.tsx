import type { ReactNode } from "react";
import { EVIDENCE, EvidenceGlyph } from "../design";

/** A small uppercase mono label: the console's voice for a field name. */
export function Kicker({ children, tone = "dim" }: { children: ReactNode; tone?: "dim" | "stop" | "go" | "amber" | "model" }) {
  const colour =
    tone === "stop"
      ? "text-stop"
      : tone === "go"
        ? "text-go"
        : tone === "amber"
          ? "text-amber"
          : tone === "model"
            ? "text-ev-model-generated"
            : "text-paint-3";
  return <span className={`mono block text-micro tracking-[0.12em] uppercase ${colour}`}>{children}</span>;
}

/** A section's heading: an index number, a title and a hairline. */
export function SectionHead({ index, title, meta }: { index: string; title: string; meta?: ReactNode }) {
  return (
    <header className="flex items-end justify-between gap-4 border-b border-rule-2 pb-2.5">
      <h2 className="flex items-baseline gap-3 font-display text-[22px] leading-none font-medium tracking-[-0.005em] text-paint-1">
        <span className="mono text-label font-normal text-amber">{index}</span>
        {title}
      </h2>
      {meta ? <div className="flex min-w-0 items-center gap-3">{meta}</div> : null}
    </header>
  );
}

/** Model output is always marked as such. */
export function ModelTag({ label = "Model output" }: { label?: string }) {
  return (
    <span
      className="mono inline-flex items-center gap-1.5 border px-1.5 py-0.5 text-micro tracking-[0.08em] uppercase"
      style={{ borderColor: "color-mix(in srgb, var(--ev-model-generated) 45%, transparent)", color: EVIDENCE.model_generated.colour }}
      title={EVIDENCE.model_generated.meaning}
    >
      <EvidenceGlyph kind="model_generated" size={9} />
      {label}
    </span>
  );
}

const LEVELS = ["low", "medium", "high"] as const;

/** A model's own confidence word as three cells. It is the model's word, not a measure. */
export function ConfidenceMeter({ level }: { level: string }) {
  const lit = LEVELS.indexOf(level.toLowerCase() as (typeof LEVELS)[number]) + 1;
  return (
    <span className="inline-flex items-center gap-2" title={`The model's own confidence: ${level}`}>
      <span aria-hidden="true" className="inline-flex gap-[3px]">
        {LEVELS.map((name, index) => (
          <span
            key={name}
            className="h-2 w-3.5"
            style={{
              background: index < lit ? EVIDENCE.model_generated.colour : "transparent",
              border: `1px solid ${index < lit ? EVIDENCE.model_generated.colour : "var(--paint-4)"}`,
              opacity: index < lit ? 0.85 : 1,
            }}
          />
        ))}
      </span>
      <span className="mono text-micro tracking-[0.08em] text-paint-3 uppercase">{`Conf. ${level}`}</span>
    </span>
  );
}

/** A deterministic check that passed or failed, as a lamp and a few words. */
export function CheckLamp({ ok, children, title }: { ok: boolean; children: ReactNode; title?: string }) {
  return (
    <span className={`mono inline-flex items-center gap-2 text-label ${ok ? "text-go" : "text-stop"}`} title={title}>
      <span
        aria-hidden="true"
        className={`size-2 ${ok ? "bg-go shadow-[0_0_8px_rgba(51,195,123,0.8)]" : "bg-stop shadow-[0_0_8px_rgba(240,82,79,0.8)]"}`}
      />
      {children}
    </span>
  );
}
