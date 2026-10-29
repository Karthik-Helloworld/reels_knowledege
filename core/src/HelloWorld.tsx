import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { theme } from "./theme";

export const HelloWorld = () => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 16, stiffness: 120, mass: 0.7 },
  });
  const opacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const progress = interpolate(frame, [0, Math.max(1, durationInFrames - 1)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: theme.colors.background,
        color: theme.colors.text,
        fontFamily: theme.fonts.display,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          height: theme.spacing.sm,
          width: `${progress * 100}%`,
          backgroundColor: theme.colors.accent,
        }}
      />
      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "center",
          padding: theme.spacing.xl,
          opacity,
          transform: `translateY(${(1 - enter) * theme.spacing.lg}px)`,
        }}
      >
        <div
          style={{
            fontSize: 120,
            fontWeight: 700,
            letterSpacing: -2,
            lineHeight: 1,
          }}
        >
          Hello
        </div>
        <div
          style={{
            marginTop: theme.spacing.md,
            fontFamily: theme.fonts.body,
            fontSize: 42,
            color: theme.colors.muted,
          }}
        >
          Phase 0
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
