import { motion } from "motion/react";
import { Link } from "react-router-dom";
import {
  Absent,
  Button,
  DUR,
  EASE,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  Field,
  Label,
  Ledger,
  Panel,
  SkeletonRows,
  StatusMark,
  Tooltip,
  type LedgerColumn,
} from "../design";
import { Instrument, PageHead } from "../design/Instrument";
import { analysisName } from "../domain/analyses";
import { clipSpan, coveredS, footageFor } from "../domain/footage";
import { ARM_ORDER, sitePlan } from "../domain/plan";
import { ACTIVE_SITE, SITE_NAME_NOTE, groundFrameName, siteName, siteNameStatusLabel } from "../domain/site";
import { MODE_LABEL } from "../domain/vocabulary";
import { SitePlanCanvas } from "../instruments/SitePlan";
import type { AnalysisSummary, CorridorIntersection, FootageSource } from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { useCorridor, useSite, useSources } from "../state/resources";

const NOT_RECORDED = "Name not recorded";
/** Junction metres across the shorter side of the plan. */
const PLAN_M = 92;

const CORRIDOR_COLUMNS: readonly LedgerColumn<CorridorIntersection>[] = [
  {
    key: "site",
    label: "Intersection",
    span: 6,
    rowHeader: true,
    cell: (row) =>
      row.name ? (
        <span className="font-display text-num-s leading-tight tracking-normal text-paint-1">{row.name}</span>
      ) : (
        <span className="inline-flex items-baseline gap-3 text-paint-2">
          Corridor intersection {row.int_id}
          <Absent reason={NOT_RECORDED} />
        </span>
      ),
  },
  {
    key: "vehicles",
    label: "Vehicles",
    span: 2,
    align: "end",
    cell: (row) => row.unique_vehicles.toLocaleString("en-US"),
  },
  {
    key: "speed",
    label: "Moving speed, m/s",
    span: 2,
    align: "end",
    cell: (row) => row.mean_moving_speed_m_s.toFixed(2),
  },
  {
    key: "analysed",
    label: "Video analysis",
    span: 2,
    align: "end",
    cell: (row) => (row.has_video_analysis ? "Analyzed" : "Not analyzed"),
  },
];

/** Why the corridor's figures and the reference analysis's differ. Each is right for its own rows. */
function corridorNote(row: CorridorIntersection | undefined, reference: AnalysisSummary | undefined): string | null {
  if (!row || !reference) return null;
  return (
    `${row.unique_vehicles.toLocaleString("en-US")} is distinct vehicle ids tagged intersection ${row.int_id} in the published file. ` +
    `The reference analysis counts each continuous run near the junction as a track, ${reference.n_tracks.toLocaleString("en-US")} of them, ` +
    `so its rows and mean speed differ.`
  );
}

function Corridor() {
  const corridor = useCorridor();
  const { catalog } = useAnalysis();
  if (corridor.status === "loading") return <SkeletonRows rows={4} pitch={46} />;
  if (corridor.status === "error") {
    return <ErrorState what="Couldn't load the corridor summary." onRetry={corridor.retry} />;
  }
  const reference = catalog.status === "ready" ? catalog.analyses.find((row) => row.id === "ref-ngsim-int2-full") : undefined;
  const note = corridorNote(corridor.data.intersections.find((row) => row.has_video_analysis), reference);
  return (
    <>
      <Ledger
        caption="Intersections of the published corridor"
        columns={CORRIDOR_COLUMNS}
        rows={corridor.data.intersections}
        rowKey={(row) => String(row.int_id)}
      />
      <div className="mt-2">
        <EvidenceBadge kind={corridor.data.evidence_kind} note={corridor.data.evidence_note} />
      </div>
      {note ? (
        <details className="mt-1 max-w-[60rem]">
          <summary className="mono cursor-pointer text-paint-3 underline decoration-rule-2 underline-offset-4 hover:text-paint-1">
            Why the counts differ
          </summary>
          <p className="mt-1 leading-snug text-paint-2">{note}</p>
        </details>
      ) : null}
    </>
  );
}

function Analyses() {
  const { catalog, state, select, retry } = useAnalysis();
  const active = state.status === "ready" ? state.analysis.id : null;

  const columns: readonly LedgerColumn<AnalysisSummary>[] = [
    {
      key: "title",
      label: "Analysis",
      span: 3,
      rowHeader: true,
      cell: (row) => (
        <>
          {analysisName(row)}
          <span className="block text-paint-3">{MODE_LABEL[row.mode]}</span>
        </>
      ),
    },
    {
      key: "origin",
      label: "From",
      span: 1,
      cell: (row) => (row.data_origin === "reference_dataset" ? "Reference" : "Camera"),
    },
    {
      key: "tracks",
      label: "Tracks",
      span: 1,
      align: "end",
      cell: (row) => row.n_tracks.toLocaleString("en-US"),
    },
    {
      key: "action",
      label: "State",
      span: 2,
      align: "end",
      cell: (row) =>
        !row.valid ? (
          <Absent reason={row.invalid_reason ?? "Not valid"} />
        ) : row.id === active ? (
          <span className="text-amber">Loaded</span>
        ) : (
          <Button tone="text" onClick={() => select(row.id)} aria-label={`Load ${analysisName(row)}`}>
            Load
          </Button>
        ),
    },
  ];

  if (catalog.status === "loading") return <SkeletonRows rows={2} />;
  if (catalog.status === "error") {
    return <ErrorState what="Couldn't read the analyses." onRetry={retry} />;
  }
  if (catalog.analyses.length === 0) {
    return <EmptyState title="No analysis on this server" reason="An analysis is written by a completed run." />;
  }
  return (
    <Ledger
      className="mt-3"
      caption="Analyses on this server"
      columns={columns}
      rows={catalog.analyses}
      rowKey={(row) => row.id}
    />
  );
}

function Footage() {
  const sources = useSources();
  const { state } = useAnalysis();
  const record = state.status === "ready" ? state.loaded.record : null;

  const columns: readonly LedgerColumn<FootageSource>[] = [
    {
      key: "window",
      label: "Source window, s",
      span: 3,
      rowHeader: true,
      cell: (row) => {
        if (row.source_start_s === null) return `Upload, ${row.title}`;
        const [from, to] = clipSpan(row);
        return row.kind === "camera_source" ? `Whole camera file, ${from} to ${Math.round(to)}` : `${from} to ${to}`;
      },
    },
    {
      key: "covered",
      label: "Analyzed",
      span: 2,
      align: "end",
      cell: (row) => {
        if (!record) return <Absent reason="No analysis" quiet />;
        const seconds = footageFor(record, [row]).length > 0 ? coveredS(row, record) : 0;
        return seconds > 0 ? `${Math.round(seconds)} s` : <Absent reason="Not in this analysis" quiet />;
      },
    },
    {
      key: "open",
      label: "Footage",
      span: 2,
      align: "end",
      cell: (row) =>
        row.playable ? (
          <Link
            to={`/vision?clip=${encodeURIComponent(row.source_id)}`}
            className="inline-flex min-h-6 items-center text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1 hover:decoration-amber"
          >
            Open in Vision Lab
          </Link>
        ) : (
          <span className="text-paint-3">{row.problem ?? "Source file, not streamed"}</span>
        ),
    },
  ];

  if (sources.status === "loading") return <SkeletonRows rows={3} />;
  if (sources.status === "error") {
    return <ErrorState what="Couldn't read the footage catalog." onRetry={sources.retry} />;
  }
  if (sources.data.length === 0) {
    return <EmptyState title="No footage on this server" reason="Clips are not distributed with the repository." />;
  }
  return (
    <Ledger
      className="mt-3"
      caption="Footage on this server"
      columns={columns}
      rows={sources.data}
      rowKey={(row) => row.source_id}
    />
  );
}

function SiteRecord() {
  const site = useSite();
  const data = site.status === "ready" ? site.data : null;
  const arms = data ? sitePlan(data) : null;
  const lanes = arms ? (
    <span className="grid grid-cols-[auto_auto] justify-end gap-x-4">
      {ARM_ORDER.map((arm) => (
        <span key={arm}>{`${arm} ${arms[arm]?.approach.length ?? 0} in, ${arms[arm]?.exit.length ?? 0} out`}</span>
      ))}
    </span>
  ) : undefined;
  const pending = site.status === "loading" ? "Reading the site record" : "Site record not loaded";
  return (
    <Panel id="site-record" title="Site record">
      {site.status === "error" ? (
        <ErrorState what="Couldn't load this site's record." className="mt-3" onRetry={site.retry} />
      ) : (
        <>
          <Field label="Location" value={ACTIVE_SITE.city} />
          <Field label="Source" value={`${ACTIVE_SITE.dataset}, ${ACTIVE_SITE.year}`} />
          <Field label="Lanes tracked" value={lanes} reason={pending} evidence={lanes ? "approximated" : undefined} />
          <Field
            label="Reference lines"
            value={data ? String(data.gates.length) : undefined}
            reason={pending}
            quiet
            evidence={data ? "approximated" : undefined}
          />
          <Field
            label="Ground frame"
            value={
              data ? (
                <Tooltip content={data.ground_frame} align="end">
                  <span tabIndex={0}>{groundFrameName(data.ground_frame)}</span>
                </Tooltip>
              ) : undefined
            }
            reason={pending}
            quiet
          />
          <Field
            label="Site name"
            value={data?.site_name_status ? siteNameStatusLabel(data.site_name_status) : undefined}
            reason={pending}
            quiet
          />
          <p className="mono mt-2 text-paint-3" title="Camera counts classify a track by where it first appeared.">
            {`${SITE_NAME_NOTE}. Counts use track origin, not these lines.`}
          </p>
        </>
      )}
    </Panel>
  );
}

export function Intersections() {
  const site = useSite();
  const corridor = useCorridor();
  const siteData = site.status === "ready" ? site.data : null;
  const analysed =
    corridor.status === "ready" ? corridor.data.intersections.filter((row) => row.has_video_analysis).length : null;

  return (
    <div className="page min-h-full pb-12">
      <PageHead
        title="Intersections"
        sub={<Label>{siteName(ACTIVE_SITE)}</Label>}
        meta={
          corridor.status === "ready" ? (
            <Label tone="body">{`${analysed} of ${corridor.data.intersections.length} corridor intersections analyzed`}</Label>
          ) : undefined
        }
      />

      <Instrument>
        <Corridor />
      </Instrument>

      <div className="grid-12 mt-10 gap-y-10">
        <Instrument className="col-span-12 flex flex-col gap-10 lg:col-span-7">
          <Panel title="Analyses" meta={<Label>Loads app-wide</Label>}>
            <Analyses />
          </Panel>
          <Panel title="Footage">
            <Footage />
          </Panel>
          <p
            className="mono text-paint-3"
            title="An uploaded video runs uncalibrated, in image pixels, and its record says its licence was not verified."
          >
            One site registered. Uploads run uncalibrated.
          </p>
        </Instrument>

        <Instrument className="col-span-12 flex min-h-0 flex-col gap-10 lg:col-span-5">
          <Panel
            title="Plan"
            meta={<StatusMark status="ungated" />}
            className="flex flex-col"
          >
            <motion.div
              layoutId="site-plan"
              transition={{ duration: DUR.slow, ease: EASE }}
              className="mt-3"
            >
              <SitePlanCanvas site={siteData} metres={PLAN_M} className="h-[340px]" />
            </motion.div>
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
              {siteData ? (
                <>
                  <EvidenceBadge kind="approximated" note="Lanes from tracks" />
                  <EvidenceBadge kind="assumed" note="Road beyond" />
                </>
              ) : (
                <EvidenceBadge kind="assumed" note="Schematic, not surveyed" />
              )}
            </div>
          </Panel>
          <SiteRecord />
        </Instrument>
      </div>
    </div>
  );
}
