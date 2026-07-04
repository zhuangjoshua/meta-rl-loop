// Placeholder design tokens — deliberately plain (the RN analog of the web scaffold's tokens.css).
// The CEO worker + branding replace these before publish; a shipped app that still looks like this
// is a greenlight/quality finding, by design.
import { surface } from "./takyon";

export const theme = {
  accent: surface.branding?.accent || "#4f46e5",
  bg: "#0b0b12",
  card: "#15151f",
  text: "#f5f5f7",
  muted: "#9ca3af",
  danger: "#ef4444",
  radius: 14,
  space: 16,
};
