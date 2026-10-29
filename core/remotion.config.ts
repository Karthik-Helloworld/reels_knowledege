import path from "path";
import { Config } from "@remotion/cli/config";

// One frame at a time, JPEG intermediates: keeps a short hello-world
// render inside a small memory budget. swangle works without a GPU.
// Scenes live in edu/remotion, so module lookup also starts in core/node_modules.
Config.overrideWebpackConfig((current) => {
  const modules = current.resolve?.modules ?? ["node_modules"];
  return {
    ...current,
    resolve: {
      ...current.resolve,
      modules: [path.join(process.cwd(), "node_modules"), ...modules],
    },
  };
});
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setConcurrency(1);
Config.setChromiumOpenGlRenderer("swangle");
