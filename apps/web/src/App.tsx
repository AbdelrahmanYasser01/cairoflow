import { lazy } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { Analytics } from "./pages/Analytics";
import { CommandCenter } from "./pages/CommandCenter";
import { EvidenceReports } from "./pages/EvidenceReports";
import { Intersections } from "./pages/Intersections";
import { NotFound } from "./pages/NotFound";
import { VisionLab } from "./pages/VisionLab";
import { AppShell } from "./shell/AppShell";

// Split out so three.js lands in its own chunk once the twin page mounts it.
const DigitalTwin = lazy(() =>
  import("./pages/DigitalTwin").then((module) => ({ default: module.DigitalTwin })),
);
// A working reference for this team. Behind the DEV check the import itself is dropped from a build.
const DesignSheet = import.meta.env.DEV
  ? lazy(() => import("./design/DesignSheet").then((module) => ({ default: module.DesignSheet })))
  : null;

export function App() {
  const location = useLocation();
  return (
    <AppShell>
      <Routes location={location}>
        <Route path="/" element={<CommandCenter />} />
        <Route path="/intersections" element={<Intersections />} />
        <Route path="/vision" element={<VisionLab />} />
        <Route path="/twin" element={<DigitalTwin />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/evidence" element={<EvidenceReports />} />
        {DesignSheet ? <Route path="/_design" element={<DesignSheet />} /> : null}
        <Route path="*" element={<NotFound />} />
      </Routes>
    </AppShell>
  );
}
