import type { FC } from "react";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";
import type { TimelineItem } from "../types";

export const TimelineScene: FC<{ items: TimelineItem[] }> = ({ items }) => {
  return (
    <SceneFrame kicker="Timeline">
      <div style={{ position: "relative", paddingLeft: 48 }}>
        <div
          style={{
            position: "absolute",
            left: 16,
            top: 8,
            bottom: 8,
            width: 6,
            backgroundColor: theme.colors.accent,
          }}
        />
        {items.map((item) => (
          <div key={`${item.label}-${item.detail}`} style={{ marginBottom: theme.spacing.lg }}>
            <div style={{ fontSize: 28, color: theme.colors.accent, fontWeight: 700 }}>{item.label}</div>
            <div style={{ fontSize: 44, fontWeight: 650, lineHeight: 1.15 }}>{item.detail}</div>
          </div>
        ))}
      </div>
    </SceneFrame>
  );
};
