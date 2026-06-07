import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import "./IconButton.scss";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  /** "default" = white CRUD circle; "add" = dark-green filled add button. */
  status?: "default" | "add";
  size?: "small" | "medium";
  label?: string;
  children?: ReactNode;
}

/** IconButton — Composer's circular `crud-action` control. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { status = "default", size = "medium", label, className, children, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      className={clsx("IconButton", `IconButton--${status}`, `IconButton--${size}`, className)}
      {...rest}
    >
      {children}
    </button>
  );
});
