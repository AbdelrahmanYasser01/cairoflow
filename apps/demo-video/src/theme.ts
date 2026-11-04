import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// The app's own tokens (apps/web/src/design/tokens.css).
export const C = {
  bg: "#0a0c0e",
  bg1: "#11151a",
  bg2: "#181d22",
  rule1: "#2a3138",
  rule2: "#3d4750",
  paint1: "#efeadf",
  paint2: "#b9b7af",
  paint3: "#8b8d89",
  paint4: "#5f666d",
  amber: "#f5a623",
  go: "#33c37b",
  stop: "#f0524f",
  approx: "#7fd0da",
  assumed: "#c9b48c",
  model: "#f19db1",
  sim: "#6ea2ff",
};

export const F = {
  display: "Barlow Semi Condensed",
  text: "Barlow",
  mono: "IBM Plex Mono",
};

const fonts: [string, string, string][] = [
  [F.display, "500", "barlow-semi-condensed-latin-500-normal.woff2"],
  [F.display, "600", "barlow-semi-condensed-latin-600-normal.woff2"],
  [F.display, "700", "barlow-semi-condensed-latin-700-normal.woff2"],
  [F.text, "400", "barlow-latin-400-normal.woff2"],
  [F.text, "500", "barlow-latin-500-normal.woff2"],
  [F.mono, "400", "ibm-plex-mono-latin-400-normal.woff2"],
  [F.mono, "500", "ibm-plex-mono-latin-500-normal.woff2"],
];
for (const [family, weight, file] of fonts) {
  loadFont({ family, weight, url: staticFile(`fonts/${file}`) });
}
