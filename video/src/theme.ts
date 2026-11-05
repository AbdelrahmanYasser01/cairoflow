// The app's own palette and type, copied from apps/web/src/design/tokens.css. No new colours.
export const C = {
  bg: "#0a0c0e", // asphalt-0
  s1: "#11151a",
  s2: "#181d22",
  s3: "#21272d",
  rule1: "#2a3138",
  rule2: "#3d4750",
  paint1: "#efeadf",
  paint2: "#b9b7af",
  paint3: "#8b8d89",
  paint4: "#5f666d",
  amber: "#f5a623",
  amberInk: "#171004",
  go: "#33c37b",
  stop: "#f0524f",
  observed: "#efeadf",
  approximated: "#7fd0da",
  assumed: "#c9b48c",
  model: "#f19db1",
  simulated: "#6ea2ff",
} as const;

export const F = {
  display: '"Barlow Semi Condensed", "Arial Narrow", sans-serif',
  text: '"Barlow", "Helvetica Neue", Arial, sans-serif',
  mono: '"IBM Plex Mono", ui-monospace, Menlo, monospace',
} as const;

export const FPS = 30;
export const W = 1920;
export const H = 1080;
export const EASE = [0.2, 0.8, 0.2, 1] as const;
