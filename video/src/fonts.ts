// The app's fonts, from the same @fontsource packages and versions apps/web uses.
import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/barlow-semi-condensed/700.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import { continueRender, delayRender } from "remotion";

const handle = delayRender("Loading the app's fonts");
const faces = [
  '500 40px "Barlow"',
  '600 40px "Barlow"',
  '600 40px "Barlow Semi Condensed"',
  '700 40px "Barlow Semi Condensed"',
  '400 40px "IBM Plex Mono"',
  '500 40px "IBM Plex Mono"',
  '600 40px "IBM Plex Mono"',
];
Promise.all(faces.map((face) => document.fonts.load(face)))
  .then(() => document.fonts.ready)
  .then(() => continueRender(handle))
  .catch(() => continueRender(handle));
