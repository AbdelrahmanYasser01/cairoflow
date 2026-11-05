import React from "react";
import { Composition } from "remotion";
import "./theme";
import { CairoFlowDemo } from "./CairoFlowDemo";
import { FPS, TOTAL_FRAMES } from "./timeline";

export const RemotionRoot: React.FC = () => (
  <Composition id="CairoFlowDemo" component={CairoFlowDemo} durationInFrames={TOTAL_FRAMES} fps={FPS} width={1920} height={1080} />
);
