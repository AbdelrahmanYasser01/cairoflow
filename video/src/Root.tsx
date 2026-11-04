import { Composition } from "remotion";
import { Demo, defaultDemoProps, TOTAL_FRAMES } from "./Demo";
import { FPS, H, W } from "./theme";

export const RemotionRoot = () => (
  <Composition
    id="CairoFlowDemo"
    component={Demo}
    durationInFrames={TOTAL_FRAMES}
    fps={FPS}
    width={W}
    height={H}
    defaultProps={defaultDemoProps}
  />
);
