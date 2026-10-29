import type { FC } from "react";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";

export const ConceptScene: FC<{ text: string }> = ({ text }) => {
  return (
    <SceneFrame kicker="Concept">
      <div
        style={{
          fontSize: 64,
          fontWeight: 650,
          lineHeight: 1.15,
          backgroundColor: theme.colors.surface,
          padding: theme.spacing.lg,
          borderLeft: `12px solid ${theme.colors.accent}`,
        }}
      >
        {text}
      </div>
    </SceneFrame>
  );
};
