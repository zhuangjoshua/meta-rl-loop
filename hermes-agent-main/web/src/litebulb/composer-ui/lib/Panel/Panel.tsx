import type { HTMLAttributes } from "react";
import clsx from "clsx";
import "./Panel.scss";

export interface PanelProps extends HTMLAttributes<HTMLDivElement> {}

/** Panel — Composer's `primitive-panel`: 1px #878a8d border, 4px radius, no shadow. */
export function Panel({ className, ...rest }: PanelProps) {
  return <div className={clsx("Panel", className)} {...rest} />;
}
