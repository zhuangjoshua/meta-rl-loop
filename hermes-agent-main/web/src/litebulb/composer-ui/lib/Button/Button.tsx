import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import "./Button.scss";

export type ButtonVariant = "primary" | "secondary" | "outline" | "tertiary";
export type ButtonStatus = "default" | "buy" | "danger";
export type ButtonSize = "small" | "medium" | "large";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  /** Visual emphasis. */
  variant?: ButtonVariant;
  /** Color intent layered on top of the variant. */
  status?: ButtonStatus;
  size?: ButtonSize;
  icon?: ReactNode;
  sideIcon?: ReactNode;
  fullWidth?: boolean;
  /** Native button type. */
  htmlType?: "button" | "submit" | "reset";
}

/**
 * Button — primary interactive control.
 * Mirrors lemon-ui's LemonButton (variant × status × size) anatomy.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", status = "default", size = "medium", icon, sideIcon, fullWidth, htmlType = "button", className, children, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={htmlType}
      className={clsx(
        "Button",
        `Button--${variant}`,
        `Button--status-${status}`,
        `Button--${size}`,
        fullWidth && "Button--full",
        !children && "Button--icon-only",
        className
      )}
      {...rest}
    >
      {icon && <span className="Button__icon">{icon}</span>}
      {children && <span className="Button__label">{children}</span>}
      {sideIcon && <span className="Button__icon Button__icon--side">{sideIcon}</span>}
    </button>
  );
});
