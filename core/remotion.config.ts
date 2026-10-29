import { Config } from "@remotion/cli/config";

// One frame at a time, JPEG intermediates: keeps a short hello-world
// render inside a small memory budget. swangle works without a GPU.
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setConcurrency(1);
Config.setChromiumOpenGlRenderer("swangle");
