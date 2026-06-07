import type { HTMLAttributes } from "react";
import clsx from "clsx";
import "./Card.scss";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Strong-bordered, shadowless variant (used for the editor side panels). */
  variant?: "raised" | "outline";
}

/** Card — a surface container with border, radius and elevation. */
export function Card({ variant = "raised", className, ...rest }: CardProps) {
  return <div className={clsx("Card", `Card--${variant}`, className)} {...rest} />;
}
