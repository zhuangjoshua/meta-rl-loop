/**
 * Programmatic access to the design tokens as CSS `var()` references.
 * These stay theme-aware (light/dark) because they resolve at runtime against
 * the CSS variables in tokens.scss. Import the CSS once (`composer-ui/style.css`)
 * so the variables are defined, then use these in inline styles / CSS-in-JS.
 *
 *   import { tokens } from "composer-ui";
 *   <div style={{ color: tokens.color.brand.primary }} />
 */
const v = (name: string) => `var(--cds-${name})`;

export const tokens = {
  color: {
    brand: {
      primary: v("color-brand-primary"),
      primaryHover: v("color-brand-primary-hover"),
      secondary: v("color-brand-secondary"),
      accent: v("color-brand-accent"),
    },
    text: {
      default: v("color-text-default"),
      soft: v("color-text-soft"),
      muted: v("color-text-muted"),
      faint: v("color-text-faint"),
      inverse: v("color-text-inverse"),
      link: v("color-text-link"),
    },
    surface: {
      app: v("color-surface-app"),
      sunken: v("color-surface-sunken"),
      panel: v("color-surface-panel"),
      card: v("color-surface-card"),
      dark: v("color-surface-dark"),
    },
    border: {
      default: v("color-border-default"),
      strong: v("color-border-strong"),
      input: v("color-border-input"),
      divider: v("color-border-divider"),
      focus: v("color-border-focus"),
    },
    state: {
      success: v("color-success"),
      warning: v("color-warning"),
      error: v("color-error"),
      info: v("color-info"),
    },
    node: {
      weight: v("color-node-weight"),
      condition: v("color-node-condition"),
      filter: v("color-node-filter"),
    },
  },
  font: { sans: v("font-sans"), mono: v("font-mono") },
  text: {
    xs: v("text-xs"), sm: v("text-sm"), md: v("text-md"), lg: v("text-lg"),
    xl: v("text-xl"), "2xl": v("text-2xl"), "3xl": v("text-3xl"),
  },
  weight: { normal: v("weight-normal"), medium: v("weight-medium"), semibold: v("weight-semibold"), bold: v("weight-bold") },
  space: {
    1: v("space-1"), 2: v("space-2"), 3: v("space-3"), 4: v("space-4"),
    5: v("space-5"), 6: v("space-6"), 8: v("space-8"), 10: v("space-10"), 12: v("space-12"),
  },
  radius: { sm: v("radius-sm"), md: v("radius-md"), lg: v("radius-lg"), full: v("radius-full") },
  shadow: { sm: v("shadow-sm"), md: v("shadow-md"), lg: v("shadow-lg"), overlay: v("shadow-overlay"), innerDrop: v("shadow-inner-drop") },
} as const;

export type Tokens = typeof tokens;
