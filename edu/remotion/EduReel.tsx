import type { FC } from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import { theme } from "../../core/src/theme";
import { Captions } from "./Captions";
import { ConceptScene } from "./scenes/ConceptScene";
import { CTAScene } from "./scenes/CTAScene";
import { DiagramScene } from "./scenes/DiagramScene";
import { HookScene } from "./scenes/HookScene";
import { SourceCreditScene } from "./scenes/SourceCreditScene";
import { StatScene } from "./scenes/StatScene";
import { TimelineScene } from "./scenes/TimelineScene";
import type { EduReelProps } from "./types";

const SceneView: FC<{ id: string; reel: EduReelProps }> = ({ id, reel }) => {
  if (id === "hook") {
    return <HookScene text={reel.hook} />;
  }
  if (id === "concept") {
    return <ConceptScene text={reel.concept} />;
  }
  if (id === "diagram") {
    return (
      <DiagramScene title={reel.diagramTitle} nodes={reel.diagramNodes} imageFile={reel.imageFile} />
    );
  }
  if (id === "timeline") {
    return <TimelineScene items={reel.timeline} />;
  }
  if (id === "stat") {
    return <StatScene value={reel.statValue} label={reel.statLabel} />;
  }
  if (id === "cta") {
    return <CTAScene text={reel.cta} />;
  }
  if (id === "sourceCredit") {
    return <SourceCreditScene credits={reel.credits} />;
  }
  return null;
};

export const EduReel: FC<EduReelProps> = (reel) => {
  return (
    <AbsoluteFill style={{ backgroundColor: theme.colors.background }}>
      {reel.audioFile ? <Audio src={staticFile(reel.audioFile)} /> : null}
      {reel.scenes.map((scene) => (
        <Sequence
          key={scene.id}
          from={scene.from}
          durationInFrames={Math.max(1, scene.duration)}
          name={scene.id}
        >
          <SceneView id={scene.id} reel={reel} />
        </Sequence>
      ))}
      <Captions cues={reel.captions} />
    </AbsoluteFill>
  );
};
