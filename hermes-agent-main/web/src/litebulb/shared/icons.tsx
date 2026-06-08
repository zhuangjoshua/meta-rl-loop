import type { ReactNode, SVGProps } from "react";

/* ------------------------------------------------------------------ *
 * Shared icon set. Stroke icons use currentColor so they inherit the
 * surrounding text color in both the landing (base44) and product
 * (composer) surfaces. 24-box for marketing, 16-box for product chrome.
 * ------------------------------------------------------------------ */

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

const Stroke = ({ size = 24, children, ...rest }: IconProps & { children: ReactNode }) => (
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    {...rest}
  >
    {children}
  </svg>
);

/* The Litebulb glyph — a filament bulb. Shared across both surfaces. */
export const Bulb = ({ size = 24, ...rest }: IconProps) => (
  <Stroke size={size} {...rest}>
    <path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.5.4.8.9.9 1.5l.1.7h5.2l.1-.7c.1-.6.4-1.1.9-1.5A6 6 0 0 0 12 3Z" />
  </Stroke>
);

/* Brand lockup: gradient bulb badge + wordmark. `tone` switches the badge
   fill between the warm marketing gradient and the product's inked chip. */
export const BulbMark = ({
  size = 26,
  tone = "brand",
}: {
  size?: number;
  tone?: "brand" | "ink";
}) => (
  <span
    className={`lb-mark lb-mark--${tone}`}
    style={{ width: size, height: size }}
    aria-hidden="true"
  >
    <Bulb size={Math.round(size * 0.62)} />
  </span>
);

export const ArrowRight = ({ size = 16, ...rest }: IconProps) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" aria-hidden="true" {...rest}>
    <path d="M3 8h9.5M9 4.5 12.5 8 9 11.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export const Plus = ({ size = 24, ...rest }: IconProps) => (
  <Stroke size={size} {...rest}>
    <path d="M12 5v14M5 12h14" />
  </Stroke>
);

export const Mic = ({ size = 24, ...rest }: IconProps) => (
  <Stroke size={size} {...rest}>
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
    <path d="M19 10a7 7 0 0 1-14 0M12 19v3" />
  </Stroke>
);

export const ArrowUp = ({ size = 24, ...rest }: IconProps) => (
  <Stroke size={size} {...rest}>
    <path d="M12 19V5M5 12l7-7 7 7" />
  </Stroke>
);

export const Globe = ({ size = 24, ...rest }: IconProps) => (
  <Stroke size={size} {...rest}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.5 2.5 2.5 16 0 18M12 3c-2.5 2.5-2.5 16 0 18" />
  </Stroke>
);

export const Info = ({ size = 16, ...rest }: IconProps) => (
  <Stroke size={size} strokeWidth={2} {...rest}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 16v-4M12 8h.01" />
  </Stroke>
);

export const Sparkle = ({ size = 16, ...rest }: IconProps) => (
  <Stroke size={size} {...rest}>
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z" />
  </Stroke>
);

export const Check = ({ size = 16, ...rest }: IconProps) => (
  <Stroke size={size} strokeWidth={2} {...rest}>
    <path d="M4 12.5 9 17.5 20 6.5" />
  </Stroke>
);
