/**
 * Shared look for Phase 0 compositions.
 * Video size and fps match core/utils.py (VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS).
 * Fonts are system stacks so a render does not download typefaces.
 */
export const theme = {
  colors: {
    background: "#0E1116",
    surface: "#1A2030",
    text: "#F5F7FA",
    muted: "#A8B3C7",
    accent: "#3DDC97",
  },
  fonts: {
    display: 'system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    body: 'system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  },
  spacing: {
    xs: 8,
    sm: 16,
    md: 32,
    lg: 48,
    xl: 80,
  },
  video: {
    width: 1080,
    height: 1920,
    fps: 30,
  },
} as const;

export type Theme = typeof theme;
