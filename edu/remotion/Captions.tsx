import type { FC } from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { theme } from "../../core/src/theme";
import { SAFE_BOTTOM, SAFE_RIGHT } from "./safe";
import type { WordCue } from "./types";

export const Captions: FC<{ cues: WordCue[] }> = ({ cues }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const time = frame / fps;
  const current = cues.find((cue) => time >= cue.start && time < cue.end);
  if (!current) {
    return null;
  }
  return (
    <div
      style={{
        position: "absolute",
        left: theme.spacing.lg,
        right: SAFE_RIGHT,
        bottom: SAFE_BOTTOM,
        color: theme.colors.text,
        backgroundColor: theme.colors.surface,
        fontFamily: theme.fonts.body,
        fontSize: 42,
        fontWeight: 700,
        lineHeight: 1.2,
        padding: `${theme.spacing.sm}px ${theme.spacing.md}px`,
        borderRadius: 16,
        border: `3px solid ${theme.colors.accent}`,
      }}
    >
      {current.text}
    </div>
  );
};
