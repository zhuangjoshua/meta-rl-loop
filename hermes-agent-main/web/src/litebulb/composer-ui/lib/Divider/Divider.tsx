import type { HTMLAttributes } from "react";
import clsx from "clsx";
import "./Divider.scss";

export interface DividerProps extends HTMLAttributes<HTMLDivElement> {
  vertical?: boolean;
}

/** Divider — 1px hairline (rgba(0,0,0,0.16)). */
export function Divider({ vertical, className, ...rest }: DividerProps) {
  return <div role="separator" className={clsx("Divider", vertical && "Divider--vertical", className)} {...rest} />;
}
