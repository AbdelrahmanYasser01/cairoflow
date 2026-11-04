import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { Suspense, useEffect, useRef, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { Button, DUR, EASE, EVIDENCE_KINDS, EvidenceBadge, Label, Select, pageVariants } from "../design";
import { analysisName } from "../domain/analyses";
import { ACTIVE_SITE, SITE_NAME_NOTE, siteName } from "../domain/site";
import { analysisLabel, useAnalysis } from "../state/analysis";
import { Mark } from "./icons";
import { ROUTES } from "./routes";

const tick = { duration: DUR.base, ease: EASE };
/** About four seconds of frames, for a section that waits on a record. */
const SEEK_FRAMES = 240;
const SETTLE_MS = 450;

function Rail() {
  return (
    <nav
      aria-label="Primary"
      className="row-span-2 hidden w-rail flex-col border-r border-rule-1 bg-asphalt-0 lg:flex"
    >
      <Link
        to="/"
        aria-label="CairoFlow, Command Center"
        className="flex h-strip shrink-0 items-center justify-center border-b border-rule-1"
      >
        <Mark size={24} />
      </Link>
      <ul className="m-0 flex list-none flex-col p-0">
        {ROUTES.map(({ path, label, short, Icon }) => (
          <li key={path}>
            <NavLink
              to={path}
              end={path === "/"}
              aria-label={label}
              className={({ isActive }) =>
                `relative flex h-[68px] flex-col items-center justify-center gap-1.5 transition-colors duration-150 focus-visible:-outline-offset-2 ${
                  isActive ? "bg-linear-to-r from-amber/10 to-transparent text-paint-1" : "text-paint-3 hover:bg-asphalt-1 hover:text-paint-1"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="nav-active"
                      transition={tick}
                      className="absolute inset-y-0 left-0 w-[3px] bg-amber"
                    />
                  )}
                  <Icon />
                  <span className="font-display text-label leading-none tracking-[0.005em]">{short}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function BottomBar() {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-rule-2 bg-asphalt-1 pb-[env(safe-area-inset-bottom)] lg:hidden"
    >
      <ul className="m-0 grid list-none grid-cols-6 p-0">
        {ROUTES.map(({ path, label, short, Icon }) => (
          <li key={path}>
            <NavLink
              to={path}
              end={path === "/"}
              aria-label={label}
              className={({ isActive }) =>
                `relative flex h-bottom-bar flex-col items-center justify-center gap-1 ${
                  isActive ? "text-paint-1" : "text-paint-3"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="nav-active-bar"
                      transition={tick}
                      className="absolute inset-x-1 top-0 h-[3px] bg-amber"
                    />
                  )}
                  <Icon />
                  <span className="font-display text-micro leading-none">{short}</span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** Which analysis the whole app is reading, and the one control that changes it. */
function AnalysisControl() {
  const { state, catalog, select, retry } = useAnalysis();
  const options =
    catalog.status === "ready"
      ? catalog.analyses.filter((row) => row.valid).map((row) => ({ value: row.id, label: analysisName(row) }))
      : [];

  if (state.status === "error") {
    return (
      <p role="status" className="mono flex min-w-0 items-center gap-2 whitespace-nowrap">
        <span aria-hidden="true" className="inline-block size-2 shrink-0 bg-stop" />
        <span className="truncate text-paint-1">{analysisLabel(state)}</span>
        <Button tone="text" className="shrink-0" onClick={retry}>
          Retry
        </Button>
        {options.length > 0 ? (
          <Select
            label="Active analysis"
            value=""
            options={[{ value: "", label: "Load another" }, ...options]}
            onChange={(id) => {
              if (id) select(id);
            }}
            className="min-w-0 max-w-[16rem]"
          />
        ) : null}
      </p>
    );
  }

  if (state.status === "ready" && options.length > 0) {
    return (
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="mono hidden shrink-0 text-paint-3 min-[1600px]:inline">Analysis</span>
        <Select
          label="Active analysis"
          value={state.analysis.id}
          options={options}
          onChange={select}
          className="min-w-0 max-w-[16rem]"
        />
      </span>
    );
  }

  return <p className="mono truncate whitespace-nowrap text-paint-2">{analysisLabel(state)}</p>;
}

/** The registered site, unless the analysis on screen came from a video with no site at all. */
function SiteLine() {
  const { state } = useAnalysis();
  if (state.status === "ready" && state.loaded.record.source.uploaded_by_user) {
    return (
      <p className="mono flex min-w-0 items-baseline gap-2">
        <span className="hidden text-paint-3 lg:inline">Site</span>
        <span className="truncate text-paint-1">Uploaded video, no registered site</span>
      </p>
    );
  }
  // The one place the app says where and when this is, and what it is for.
  const where = `${ACTIVE_SITE.city}, ${ACTIVE_SITE.year} · decision support only`;
  return (
    <p className="mono flex min-w-0 items-baseline gap-2" title={SITE_NAME_NOTE}>
      <span className="truncate text-paint-3 lg:hidden">{`${ACTIVE_SITE.street} · ${where}`}</span>
      <span className="hidden truncate text-paint-1 lg:inline">{siteName(ACTIVE_SITE)}</span>
      <span className="hidden whitespace-nowrap text-paint-3 min-[1240px]:inline">{where}</span>
    </p>
  );
}

/** Always present: which analysis is active, which site, and the evidence key. */
function ContextStrip() {
  return (
    <header className="z-30 flex h-strip min-w-0 items-center gap-6 border-b border-rule-1 bg-asphalt-0 px-margin">
      <Link to="/" aria-label="CairoFlow, Command Center" className="flex min-h-6 min-w-6 shrink-0 items-center gap-2">
        <span className="lg:hidden">
          <Mark size={22} />
        </span>
        <span className="hidden font-display text-head leading-none font-semibold tracking-[-0.01em] text-paint-1 sm:inline">
          CairoFlow
        </span>
      </Link>
      <span aria-hidden="true" className="hidden h-4 border-l border-rule-2 sm:block" />
      <div className="flex min-w-0 flex-1 flex-col gap-x-6 gap-y-0.5 lg:flex-row lg:items-center">
        <AnalysisControl />
        <span aria-hidden="true" className="hidden h-4 border-l border-rule-2 lg:block" />
        <SiteLine />
      </div>
      <div className="hidden shrink-0 items-center gap-2 md:flex">
        <Label className="hidden whitespace-nowrap min-[1600px]:inline">Key</Label>
        <ul aria-label="Evidence key" className="m-0 flex list-none items-center border border-rule-1 bg-asphalt-1 p-0">
          {EVIDENCE_KINDS.map((kind) => (
            <li key={kind} className="border-r border-rule-1 px-0.5 last:border-r-0">
              <EvidenceBadge kind={kind} compact tooltipSide="bottom" tooltipAlign="end" />
            </li>
          ))}
        </ul>
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const shown = useRef(location.pathname);

  // A route change is a page change: name it, and put keyboard and screen reader users at its top.
  // Compared by path, because StrictMode runs this twice on load and that is not a route change.
  useEffect(() => {
    const route = ROUTES.find(({ path }) => path === location.pathname);
    document.title = route ? `${route.label} | CairoFlow` : "CairoFlow";
    if (shown.current === location.pathname) return;
    shown.current = location.pathname;
    main.current?.scrollTo({ top: 0 });
    main.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  // The ring on main is for someone tabbing. A click moves focus here too, and shouldn't paint a frame.
  useEffect(() => {
    const root = document.documentElement;
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Tab") root.setAttribute("data-keys", "");
    };
    const pointer = () => root.removeAttribute("data-keys");
    window.addEventListener("keydown", keys);
    window.addEventListener("pointerdown", pointer);
    return () => {
      window.removeEventListener("keydown", keys);
      window.removeEventListener("pointerdown", pointer);
    };
  }, []);

  // A link to a part of a page. That part may be waiting on a record, so it is looked for until it
  // lands, then once more after the sections above it have settled.
  useEffect(() => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    let frame = 0;
    let settle = 0;
    let tries = 0;
    const seek = () => {
      const target = document.getElementById(id);
      if (target) {
        target.scrollIntoView({ block: "start" });
        settle = window.setTimeout(() => target.scrollIntoView({ block: "start" }), SETTLE_MS);
      } else if ((tries += 1) < SEEK_FRAMES) {
        frame = requestAnimationFrame(seek);
      }
    };
    frame = requestAnimationFrame(seek);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
    };
  }, [location.pathname, location.hash, location.key]);

  return (
    <LayoutGroup>
      <a
        href="#content"
        onClick={(event) => {
          event.preventDefault();
          main.current?.focus();
        }}
        className="absolute top-1 left-1 z-50 -translate-y-16 bg-amber px-3 py-2 font-display text-body font-medium text-amber-ink focus:translate-y-0"
      >
        Skip to content
      </a>
      <div className="grid h-dvh grid-cols-1 grid-rows-[var(--strip)_minmax(0,1fr)] lg:grid-cols-[var(--rail)_minmax(0,1fr)]">
        <Rail />
        <ContextStrip />
        <main
          ref={main}
          id="content"
          tabIndex={-1}
          className="shell-main relative min-h-0 min-w-0 overflow-x-hidden overflow-y-auto pb-bottom-bar lg:pb-0"
        >
          {/* popLayout keeps the old page in place while the new one assembles, so an instrument
              on both pages can travel between them instead of cutting. */}
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.div
              key={location.pathname}
              variants={pageVariants}
              initial="initial"
              animate="enter"
              exit="exit"
              className="h-full"
            >
              <Suspense fallback={null}>{children}</Suspense>
            </motion.div>
          </AnimatePresence>
        </main>
        <BottomBar />
      </div>
    </LayoutGroup>
  );
}
