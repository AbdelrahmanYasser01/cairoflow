import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(92);
Config.setCodec("h264");
Config.setPixelFormat("yuv420p");
Config.setCrf(18);
Config.setOverwriteOutput(true);
// Software WebGL is not needed here; the edit is DOM, SVG and canvas only.
Config.setChromiumOpenGlRenderer("swangle");
