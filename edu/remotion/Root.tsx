import type { FC } from "react";
import { Composition, type CalculateMetadataFunction } from "remotion";
import { theme } from "../../core/src/theme";
import { secondsToFrames } from "../../core/src/utils";
import { EduReel } from "./EduReel";
import type { EduReelProps } from "./types";

export const defaultReelProps: EduReelProps = {
  topic: "Black hole",
  hook: "A Black hole is a region of space.",
  concept: "Gravity can be so strong that light cannot escape.",
  diagramTitle: "event horizon",
  diagramNodes: ["gravity", "event horizon"],
  timeline: [{ label: "1", detail: "event horizon" }],
  statValue: "Light",
  statLabel: "cannot leave",
  cta: "Follow for another science fact.",
  credits: [
    {
      name: "Wikipedia contributors",
      license: "CC BY-SA 4.0",
      line: "Source: Wikipedia contributors, CC BY-SA 4.0",
    },
  ],
  captions: [],
  audioFile: null,
  imageFile: null,
  spokenSeconds: 4,
  creditSeconds: 2,
  scenes: [
    { id: "hook", from: 0, duration: 20 },
    { id: "concept", from: 20, duration: 20 },
    { id: "diagram", from: 40, duration: 20 },
    { id: "timeline", from: 60, duration: 20 },
    { id: "stat", from: 80, duration: 20 },
    { id: "cta", from: 100, duration: 20 },
    { id: "sourceCredit", from: 120, duration: 60 },
  ],
};

const calculateMetadata: CalculateMetadataFunction<EduReelProps> = ({ props }) => {
  const seconds = props.spokenSeconds + props.creditSeconds;
  return {
    durationInFrames: Math.max(1, secondsToFrames(seconds)),
    fps: theme.video.fps,
    width: theme.video.width,
    height: theme.video.height,
  };
};

export const RemotionRoot: FC = () => {
  return (
    <Composition
      id="EduReel"
      component={EduReel}
      durationInFrames={secondsToFrames(defaultReelProps.spokenSeconds + defaultReelProps.creditSeconds)}
      fps={theme.video.fps}
      width={theme.video.width}
      height={theme.video.height}
      defaultProps={defaultReelProps}
      calculateMetadata={calculateMetadata}
    />
  );
};
