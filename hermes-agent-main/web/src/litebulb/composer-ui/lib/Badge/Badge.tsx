import type { HTMLAttributes } from "react";
import clsx from "clsx";
import "./Badge.scss";

export type BadgeStatus = "neutral" | "success" | "warning" | "error" | "info";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  status?: BadgeStatus;
  /** Show a leading status dot. */
  dot?: boolean;
}

/** Badge / tag — small status pill. */
export function Badge({ status = "neutral", dot, className, children, ...rest }: BadgeProps) {
  return (
    <span className={clsx("Badge", `Badge--${status}`, className)} {...rest}>
      {dot && <span className="Badge__dot" />}
      {children}
    </span>
  );
}
