import { Config } from "@remotion/cli/config";

// The product shots are JPEG-grade captures; JPEG frames keep the render fast.
Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(92);
Config.setOverwriteOutput(true);
