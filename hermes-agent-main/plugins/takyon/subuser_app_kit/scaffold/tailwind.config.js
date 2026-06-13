/**
 * Theme is driven exclusively by the CSS variables in src/tokens.css.
 * Products restyle by replacing token values from their design brief —
 * not by editing component classes or this mapping.
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "var(--tk-background)",
        foreground: "var(--tk-foreground)",
        primary: {
          DEFAULT: "var(--tk-primary)",
          foreground: "var(--tk-primary-foreground)",
        },
        accent: {
          DEFAULT: "var(--tk-accent)",
          foreground: "var(--tk-accent-foreground)",
        },
        muted: {
          DEFAULT: "var(--tk-muted)",
          foreground: "var(--tk-muted-foreground)",
        },
        card: {
          DEFAULT: "var(--tk-card)",
          foreground: "var(--tk-card-foreground)",
        },
        destructive: {
          DEFAULT: "var(--tk-destructive)",
          foreground: "var(--tk-destructive-foreground)",
        },
        border: "var(--tk-border)",
        input: "var(--tk-input)",
        ring: "var(--tk-ring)",
      },
      fontFamily: {
        sans: "var(--tk-font-sans)",
        heading: "var(--tk-font-heading)",
        mono: "var(--tk-font-mono)",
      },
      borderRadius: {
        DEFAULT: "var(--tk-radius)",
        sm: "calc(var(--tk-radius) - 2px)",
        md: "var(--tk-radius)",
        lg: "calc(var(--tk-radius) + 4px)",
        xl: "calc(var(--tk-radius) + 8px)",
      },
    },
  },
  plugins: [],
};
