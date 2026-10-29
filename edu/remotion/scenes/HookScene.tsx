import type { FC } from "react";
import { theme } from "../../../core/src/theme";
import { SceneFrame } from "../SceneFrame";

export const HookScene: FC<{ text: string }> = ({ text }) => {
  return (
    <SceneFrame kicker="Hook">
      <div style={{ fontSize: 84, fontWeight: 700, lineHeight: 1.05 }}>{text}</div>
    </SceneFrame>
  );
};
