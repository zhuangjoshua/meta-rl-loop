import type { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import "./Symphony.scss";

const Caret = () => (
  <svg className="Node__caret" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M4 6l4 4 4-4" />
  </svg>
);

export type NodeType = "weight" | "condition" | "filter";

export interface NodeProps extends HTMLAttributes<HTMLDivElement> {
  type?: NodeType;
  label?: ReactNode;
  value?: ReactNode;
  caret?: boolean;
}

/** Node — the coloured "function" pill (WEIGHT / CONDITION / FILTER). */
export function Node({ type = "weight", label, value, caret = true, className, children, ...rest }: NodeProps) {
  return (
    <div className={clsx("Node", `Node--${type}`, className)} {...rest}>
      {caret && <Caret />}
      {children ?? (
        <>
          <span className="Node__label">{label}</span>
          {value != null && <span className="Node__value">{value}</span>}
        </>
      )}
    </div>
  );
}

export type BlockVariant = "asset" | "add" | "placeholder";

export interface BlockProps extends HTMLAttributes<HTMLDivElement> {
  variant?: BlockVariant;
  invalid?: boolean;
}

/** Block — asset / placeholder card on the canvas. */
export function Block({ variant = "asset", invalid, className, children, ...rest }: BlockProps) {
  return <div className={clsx("Block", `Block--${variant}`, invalid && "Block--invalid", className)} {...rest}>{children}</div>;
}

export interface AddBlockProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  invalid?: boolean;
  title?: ReactNode;
  hint?: ReactNode;
}

/** The "Add a Block" placeholder row. */
export function AddBlock({ invalid, title = "Add a Block", hint = "Assets, Weights, Conditions…", ...rest }: AddBlockProps) {
  return (
    <Block variant="add" invalid={invalid} {...rest}>
      <span className="Block__plus">+</span>
      <span className="Block__text">
        <span className="Block__title">{title}</span> <span className="Block__hint">{hint}</span>
      </span>
    </Block>
  );
}

/** Tree layout that draws the connector rails between a node and its children. */
export const Tree = ({ className, ...rest }: HTMLAttributes<HTMLDivElement>) => (
  <div className={clsx("Tree", className)} {...rest} />
);
export const TreeChildren = ({ className, ...rest }: HTMLAttributes<HTMLDivElement>) => (
  <div className={clsx("Tree__children", className)} {...rest} />
);
export const TreeRow = ({ className, ...rest }: HTMLAttributes<HTMLDivElement>) => (
  <div className={clsx("Tree__row", className)} {...rest} />
);
