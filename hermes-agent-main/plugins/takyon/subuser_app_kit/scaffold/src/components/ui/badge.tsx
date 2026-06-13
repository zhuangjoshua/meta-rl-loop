import { type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

type Variant = "default" | "accent" | "outline" | "destructive";

const variantClasses: Record<Variant, string> = {
  default: "border-transparent bg-primary text-primary-foreground",
  accent: "border-transparent bg-accent text-accent-foreground",
  outline: "border-border text-foreground",
  destructive: "border-transparent bg-destructive text-destructive-foreground",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-2.5 py-0.5 text-xs font-semibold",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  );
}
