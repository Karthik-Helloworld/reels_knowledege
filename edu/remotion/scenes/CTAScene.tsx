import type { FC } from "react";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";

export const CTAScene: FC<{ text: string }> = ({ text }) => {
  return (
    <SceneFrame kicker="Call to action">
      <div
        style={{
          alignSelf: "flex-start",
          backgroundColor: theme.colors.accent,
          color: theme.colors.background,
          fontSize: 58,
          fontWeight: 750,
          lineHeight: 1.15,
          padding: theme.spacing.lg,
        }}
      >
        {text}
      </div>
    </SceneFrame>
  );
};
