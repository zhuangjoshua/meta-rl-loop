import type { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import "./Banner.scss";

export type BannerStatus = "info" | "success" | "warning" | "error";

export interface BannerProps extends HTMLAttributes<HTMLDivElement> {
  status?: BannerStatus;
  icon?: ReactNode;
}

/** Banner / inline alert. */
export function Banner({ status = "info", icon, className, children, ...rest }: BannerProps) {
  return (
    <div role="status" className={clsx("Banner", `Banner--${status}`, className)} {...rest}>
      {icon && <span className="Banner__icon">{icon}</span>}
      <div className="Banner__content">{children}</div>
    </div>
  );
}
