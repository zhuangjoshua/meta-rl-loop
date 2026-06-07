import clsx from "clsx";
import type { ReactNode } from "react";
import "./Tabs.scss";

export interface TabOption<T extends string = string> {
  value: T;
  label: ReactNode;
}

export interface TabsProps<T extends string = string> {
  options: TabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

/** Segmented tabs — active tab on `tab-dark #dadde0`-style surface. */
export function Tabs<T extends string = string>({ options, value, onChange, className }: TabsProps<T>) {
  return (
    <div className={clsx("Tabs", className)} role="tablist">
      {options.map((opt) => (
        <button
          key={opt.value}
          role="tab"
          aria-selected={opt.value === value}
          className="Tabs__tab"
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
