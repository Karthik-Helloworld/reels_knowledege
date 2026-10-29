import type { FC } from "react";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";
import type { CreditLine } from "../types";

export const SourceCreditScene: FC<{ credits: CreditLine[] }> = ({ credits }) => {
  return (
    <SceneFrame kicker="Sources">
      {credits.map((credit) => (
        <div
          key={credit.line}
          style={{
            fontSize: credits.length > 2 ? 36 : 48,
            fontWeight: 650,
            lineHeight: 1.25,
            marginBottom: theme.spacing.md,
          }}
        >
          {credit.line}
        </div>
      ))}
    </SceneFrame>
  );
};
