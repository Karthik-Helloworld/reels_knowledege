import type { FC, ReactNode } from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { theme } from "../../core/src/theme";
import { SAFE_BOTTOM, SAFE_RIGHT } from "./safe";

export const SceneFrame: FC<{ children: ReactNode; kicker: string }> = ({
  children,
  kicker,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({
    frame,
    fps,
    config: { damping: 16, stiffness: 120, mass: 0.7 },
  });
  const opacity = interpolate(frame, [0, 8], [0, 1], {
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
      <AbsoluteFill
        style={{
          paddingTop: theme.spacing.xl,
          paddingBottom: SAFE_BOTTOM + theme.spacing.lg,
          paddingLeft: theme.spacing.lg,
          paddingRight: SAFE_RIGHT + theme.spacing.md,
          opacity,
          transform: `translateY(${(1 - enter) * 28}px)`,
          justifyContent: "center",
        }}
      >
        <div
          style={{
            color: theme.colors.accent,
            fontSize: 28,
            fontWeight: 700,
            letterSpacing: 2,
            textTransform: "uppercase",
            marginBottom: theme.spacing.md,
          }}
        >
          {kicker}
        </div>
        {children}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
