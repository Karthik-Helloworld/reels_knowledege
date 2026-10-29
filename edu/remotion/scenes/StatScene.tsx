import type { FC } from "react";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";

export const StatScene: FC<{ value: string; label: string }> = ({ value, label }) => {
  return (
    <SceneFrame kicker="Stat">
      <div style={{ fontSize: value.length > 18 ? 64 : 96, fontWeight: 750, lineHeight: 1.05 }}>
        {value}
      </div>
      {label ? (
        <div
          style={{
            marginTop: theme.spacing.md,
            fontSize: 40,
            color: theme.colors.muted,
            lineHeight: 1.2,
          }}
        >
          {label}
        </div>
      ) : null}
    </SceneFrame>
  );
};
