import { useEffect, useState, type ReactNode } from "react";
import { ROUTES } from "../shell/routes";
import {
  Absent,
  Button,
  DUR,
  EVIDENCE,
  EVIDENCE_KINDS,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  EvidenceGlyph,
  Field,
  Label,
  Numeral,
  Panel,
  Rule,
  STATUS,
  STATUSES,
  Segmented,
  SignalHead,
  Skeleton,
  SkeletonRows,
  StatusMark,
  Tooltip,
  Transport,
} from ".";
import { Instrument, PageHead } from "./Instrument";

const SWATCHES = [
  { group: "Ground", tokens: ["--asphalt-0", "--asphalt-1", "--asphalt-2", "--asphalt-3"] },
  { group: "Rules", tokens: ["--rule-1", "--rule-2"] },
  { group: "Lane paint", tokens: ["--paint-1", "--paint-2", "--paint-3", "--paint-4"] },
  { group: "Accent", tokens: ["--amber"] },
  { group: "Meaning only", tokens: ["--go", "--stop"] },
] as const;

const TYPE_SAMPLES = [
  { token: "--text-hero", className: "font-display text-hero leading-none font-medium", sample: "Lankershim" },
  { token: "--text-display", className: "font-display text-display leading-none font-medium", sample: "No footage loaded" },
  { token: "--text-title", className: "font-display text-title leading-none font-medium", sample: "Evidence & Reports" },
  { token: "--text-head", className: "text-head leading-snug", sample: "Refuses to recommend what it cannot prove." },
  { token: "--text-body", className: "text-body", sample: "Barlow for running text." },
  { token: "--text-label", className: "mono", sample: "IBM Plex Mono for data and micro-labels." },
] as const;

function useTokens(names: readonly string[]): Record<string, string> {
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => {
    const style = getComputedStyle(document.documentElement);
    setValues(Object.fromEntries(names.map((name) => [name, style.getPropertyValue(name).trim()])));
  }, [names]);
  return values;
}

const ALL_TOKENS = [
  ...SWATCHES.flatMap((group) => group.tokens),
  ...TYPE_SAMPLES.map((sample) => sample.token),
];

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <Instrument className="grid-12 gap-y-4 border-t border-rule-2 pt-5 pb-14">
      <div className="col-span-12 lg:col-span-3">
        <h2 className="text-num-s leading-tight font-medium">{title}</h2>
        {note ? <p className="mt-2 max-w-[17rem] leading-snug text-paint-3">{note}</p> : null}
      </div>
      <div className="col-span-12 min-w-0 lg:col-span-9">{children}</div>
    </Instrument>
  );
}

export function DesignSheet() {
  const tokens = useTokens(ALL_TOKENS);
  const [segment, setSegment] = useState<"masks" | "boxes" | "off">("masks");

  return (
    <div className="page pb-16">
      <PageHead title="Design sheet" meta={<Label>Every primitive, in its real states</Label>} />

      <Section
        title="Evidence grammar"
        note="One glyph, colour and label per kind. The shape differs for every kind, so the grammar survives without colour."
      >
        {/* Below 640px the table becomes records, so no column falls off the screen. */}
        <ul className="m-0 list-none border-t border-rule-2 p-0 sm:hidden">
          {EVIDENCE_KINDS.map((kind) => (
            <li key={kind} className="border-b border-rule-1 py-3">
              <div className="flex items-center justify-between gap-3">
                <EvidenceBadge kind={kind} />
                <span className="flex items-center gap-3">
                  <EvidenceGlyph kind={kind} size={12} />
                  <EvidenceGlyph kind={kind} size={20} />
                  <EvidenceGlyph kind={kind} size={32} />
                </span>
              </div>
              <p className="mono mt-2 text-paint-3">{EVIDENCE[kind].shape}</p>
              <p className="mt-1 leading-snug text-paint-2">{EVIDENCE[kind].meaning}</p>
            </li>
          ))}
        </ul>
        <table className="ledger hidden sm:table">
          <thead>
            <tr>
              <th scope="col">Badge</th>
              <th scope="col">Glyph</th>
              <th scope="col">Shape</th>
              <th scope="col">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {EVIDENCE_KINDS.map((kind) => (
              <tr key={kind}>
                <td className="whitespace-nowrap">
                  <EvidenceBadge kind={kind} />
                </td>
                <td>
                  <span className="flex items-center gap-3">
                    <EvidenceGlyph kind={kind} size={12} />
                    <EvidenceGlyph kind={kind} size={20} />
                    <EvidenceGlyph kind={kind} size={32} />
                  </span>
                </td>
                <td className="whitespace-nowrap text-paint-2">{EVIDENCE[kind].shape}</td>
                <td className="font-text !text-body leading-snug !tracking-normal text-paint-2">
                  {EVIDENCE[kind].meaning}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section
        title="Status"
        note="A separate axis from evidence. A horizontal signal head: lamp position repeats the meaning, so colour is never alone."
      >
        <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
          {STATUSES.map((status) => (
            <div key={status} className="border-b border-rule-1 py-3">
              <StatusMark status={status} />
              <p className="mt-1 leading-snug text-paint-3">{STATUS[status].meaning}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Unknown values"
        note="Deliberately absent, with a reason. Never a fake zero, never a dash at value size."
      >
        <div className="max-w-[28rem]">
          <Field label="Stop-line flow" reason="Needs tracks from an analysis" />
          <Field label="Signal plan" reason="Not recorded" />
          <Field label="Routes in the rail" value={ROUTES.length} />
        </div>
        <div className="mt-4">
          <Absent reason="No analysis loaded" />
        </div>
      </Section>

      <Section
        title="Numeral"
        note="Digits settle into place. They never count up, because a counter would show values nothing measured. Values here are counted from this app's own tables."
      >
        <div className="flex flex-wrap items-baseline gap-x-14 gap-y-6">
          <span>
            <Numeral value={EVIDENCE_KINDS.length} size="l" />
            <Label className="mt-1 block">Evidence kinds</Label>
          </span>
          <span>
            <Numeral value={STATUSES.length} size="m" />
            <Label className="mt-1 block">Statuses</Label>
          </span>
          <span>
            <Numeral value={ROUTES.length} size="s" unit="routes" />
            <Label className="mt-1 block">In the rail</Label>
          </span>
          <span>
            <Numeral value={null} reason="No analysis loaded" />
            <Label className="mt-1 block">With no value</Label>
          </span>
        </div>
      </Section>

      <Section title="Colour" note="Read live from the token sheet.">
        <div className="grid grid-cols-2 gap-x-8 gap-y-6 md:grid-cols-3">
          {SWATCHES.map((group) => (
            <div key={group.group}>
              <Label>{group.group}</Label>
              <ul className="m-0 mt-2 list-none p-0">
                {group.tokens.map((token) => (
                  <li key={token} className="flex items-center gap-3 border-t border-rule-1 py-1.5">
                    <span
                      aria-hidden="true"
                      className="size-5 border border-rule-2"
                      style={{ background: `var(${token})` }}
                    />
                    <span className="mono min-w-0 flex-1 truncate text-paint-2">{token}</span>
                    <span className="mono text-paint-3">{tokens[token]}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type" note="Barlow Semi Condensed derives from California highway signs and plates.">
        <div className="space-y-5">
          {TYPE_SAMPLES.map((sample) => (
            <div key={sample.token} className="flex flex-col gap-1">
              <Label>
                {sample.token} {tokens[sample.token]}
              </Label>
              <span className={sample.className}>{sample.sample}</span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Rule" note="Hairlines and alignment instead of boxes. The double yellow is kept for one divide per page.">
        <div className="max-w-[32rem] space-y-5">
          <div>
            <Label>Hair</Label>
            <Rule className="mt-2" />
          </div>
          <div>
            <Label>Strong</Label>
            <Rule variant="strong" className="mt-2" />
          </div>
          <div>
            <Label>Lane</Label>
            <Rule variant="lane" className="mt-2" />
          </div>
          <div>
            <Label>Centre</Label>
            <Rule variant="centre" className="mt-2" />
          </div>
        </div>
      </Section>

      <Section title="Panel" note="An unboxed instrument group. Overlay is the one boxed case, for sitting on a canvas.">
        <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
          <Panel title="Unboxed" meta={<Label>Meta</Label>}>
            <Field label="Field" reason="Not recorded" />
          </Panel>
          <Panel overlay title="Overlay" meta={<Label>Meta</Label>}>
            <Field label="Field" reason="Not recorded" />
          </Panel>
        </div>
      </Section>

      <Section title="Controls" note="A disabled control always says why.">
        <div className="flex flex-wrap items-center gap-5">
          <Button tone="primary">Primary</Button>
          <Button>Quiet</Button>
          <Button tone="text">Text</Button>
          <Button disabledReason="A run needs a clip">Disabled</Button>
        </div>
        <div className="mt-6 flex flex-wrap items-center gap-5">
          <Segmented
            label="Overlay"
            options={[
              { value: "masks", label: "Masks" },
              { value: "boxes", label: "Boxes" },
              { value: "off", label: "Off" },
            ]}
            value={segment}
            onChange={setSegment}
          />
          <Segmented
            label="Overlay, disabled"
            options={[
              { value: "masks", label: "Masks" },
              { value: "boxes", label: "Boxes" },
            ]}
            value="masks"
            onChange={() => undefined}
            disabledReason="Overlays need a completed run"
          />
          <Tooltip content="A tooltip names and explains, and never holds the only copy of a fact.">
            <span tabIndex={0} className="mono text-paint-2 underline decoration-rule-2 underline-offset-4">
              Tooltip
            </span>
          </Tooltip>
        </div>
      </Section>

      <Section title="Transport" note="Shared by video and twin. With no media there is no position, so none is drawn.">
        <div className="border-y border-rule-2">
          <Transport reason="Transport idle: no clip" />
        </div>
      </Section>

      <Section title="States" note="Empty, loading and failed, inside the block they belong to.">
        <div className="grid grid-cols-1 gap-8 md:grid-cols-2">
          <EmptyState
            title="No detections"
            reason="Each tracked object is listed here after a run."
          />
          <ErrorState what="Couldn't load the detections." onRetry={() => undefined} />
          <div>
            <Label className="mb-2 block">Skeleton</Label>
            <Skeleton height={34} width="42%" className="mb-3" />
            <SkeletonRows rows={3} />
          </div>
          <div>
            <EmptyState size="line" title="One sentence for a slot that stays small." />
            <div className="mt-8">
              <EmptyState size="stage" title="No footage loaded" reason="The stage size, for a canvas." />
            </div>
          </div>
        </div>
      </Section>

      <Section title="Motion" note="Quick and purposeful. Everything respects prefers-reduced-motion.">
        <div className="max-w-[28rem]">
          {Object.entries(DUR).map(([name, seconds]) => (
            <Field key={name} label={name} value={`${Math.round(seconds * 1000)} ms`} />
          ))}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <SignalHead status="ungated" />
          <Label>Signal head, the shared status mark</Label>
        </div>
      </Section>
    </div>
  );
}
