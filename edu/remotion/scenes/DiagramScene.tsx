import type { FC } from "react";
import { Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";

export const DiagramScene: FC<{
  title: string;
  nodes: string[];
  imageFile: string | null;
}> = ({ title, nodes, imageFile }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const end = Math.max(1, durationInFrames - 1);
  const scale = interpolate(frame, [0, end], [1, 1.16], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const drift = interpolate(frame, [0, end], [0, -48], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const shown = nodes.slice(0, 4);

  return (
    <SceneFrame kicker="Diagram">
      {imageFile ? (
        <Img
          src={staticFile(imageFile)}
          style={{
            position: "absolute",
            width: "140%",
            height: "80%",
            left: "-20%",
            top: "8%",
            objectFit: "cover",
            opacity: 0.42,
            transform: `scale(${scale}) translateX(${drift}px)`,
          }}
        />
      ) : null}
      <svg width="100%" height="420" viewBox="0 0 800 420">
        {shown.map((node, index) => {
          const cx = 120 + index * (shown.length > 1 ? 180 : 0);
          const cy = 210;
          const next = shown[index + 1];
          return (
            <g key={`${node}-${index}`}>
              {next ? (
                <line
                  x1={cx + 70}
                  y1={cy}
                  x2={cx + 110}
                  y2={cy}
                  stroke={theme.colors.accent}
                  strokeWidth={8}
                />
              ) : null}
              <circle cx={cx} cy={cy} r={70} fill={theme.colors.surface} stroke={theme.colors.accent} strokeWidth={6} />
            </g>
          );
        })}
      </svg>
      <div style={{ fontSize: 48, fontWeight: 700, marginTop: theme.spacing.md }}>{title}</div>
      <div style={{ fontSize: 32, color: theme.colors.muted, marginTop: theme.spacing.sm }}>
        {shown.join("  ·  ")}
      </div>
    </SceneFrame>
  );
};
