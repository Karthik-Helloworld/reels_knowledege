import { Composition } from "remotion";
import { HelloWorld } from "./HelloWorld";
import { theme } from "./theme";
import { HELLO_WORLD_FRAMES } from "./utils";

export const RemotionRoot = () => {
  return (
    <Composition
      id="HelloWorld"
      component={HelloWorld}
      durationInFrames={HELLO_WORLD_FRAMES}
      fps={theme.video.fps}
      width={theme.video.width}
      height={theme.video.height}
    />
  );
};
