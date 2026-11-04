import "@fontsource/barlow/latin-400.css";
import "@fontsource/barlow/latin-500.css";
import "@fontsource/barlow-semi-condensed/latin-400.css";
import "@fontsource/barlow-semi-condensed/latin-500.css";
import "@fontsource/barlow-semi-condensed/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "./index.css";

import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AnalysisProvider } from "./state/analysis";
import { LiveProvider } from "./state/live";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <AnalysisProvider>
        <LiveProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </LiveProvider>
      </AnalysisProvider>
    </MotionConfig>
  </StrictMode>,
);
