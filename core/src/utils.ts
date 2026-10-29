import { theme } from "./theme";

/** A few seconds is enough for the Phase 0 render check. */
export const HELLO_WORLD_SECONDS = 4;

export function secondsToFrames(
  seconds: number,
  fps: number = theme.video.fps,
): number {
  if (!Number.isFinite(seconds) || seconds < 0) {
    throw new Error("seconds must be a non-negative finite number");
  }
  return Math.round(seconds * fps);
}

export const HELLO_WORLD_FRAMES = secondsToFrames(HELLO_WORLD_SECONDS);
