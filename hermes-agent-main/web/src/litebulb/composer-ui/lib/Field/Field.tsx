import {
  forwardRef,
} from "react";
import type {
  InputHTMLAttributes,
  TextareaHTMLAttributes,
  SelectHTMLAttributes,
  LabelHTMLAttributes,
  ReactNode,
} from "react";
import clsx from "clsx";
import "./Field.scss";

/** Field label — 12px, tracking-wide (Composer `.label`). */
export const Label = ({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) => (
  <label className={clsx("Label", className)} {...props} />
);

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}
/** Input — Composer `.input-light`: grey fill, white + dark border on focus. */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, className, ...props }, ref) {
  return <input ref={ref} className={clsx("Input", invalid && "Input--invalid", className)} {...props} />;
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea({ invalid, className, ...props }, ref) {
  return <textarea ref={ref} className={clsx("Input", "Textarea", invalid && "Input--invalid", className)} {...props} />;
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
}
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select({ invalid, className, children, ...props }, ref) {
  return (
    <select ref={ref} className={clsx("Input", "Select", invalid && "Input--invalid", className)} {...props}>
      {children}
    </select>
  );
});

/** Label + control wrapper. */
export const FormField = ({ label, children }: { label: ReactNode; children: ReactNode }) => (
  <div className="FormField">
    <Label>{label}</Label>
    {children}
  </div>
);
