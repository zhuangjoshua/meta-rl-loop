"use client";

import { useLayoutEffect, useRef, type TextareaHTMLAttributes } from "react";

type AutoResizeTextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  maxAutoHeight?: number;
};

function resizeTextarea(textarea: HTMLTextAreaElement, maxAutoHeight: number) {
  textarea.style.height = "auto";
  const nextHeight = Math.min(textarea.scrollHeight, maxAutoHeight);
  textarea.style.height = `${nextHeight}px`;
  textarea.style.overflowY = textarea.scrollHeight > maxAutoHeight ? "auto" : "hidden";
}

export function resetAutoResizeTextarea(textarea: HTMLTextAreaElement | null, maxAutoHeight = 220) {
  if (!textarea) return;
  resizeTextarea(textarea, maxAutoHeight);
}

export function AutoResizeTextarea({ maxAutoHeight = 220, onInput, className, ...props }: AutoResizeTextareaProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    resetAutoResizeTextarea(textareaRef.current, maxAutoHeight);
  }, [maxAutoHeight, props.defaultValue, props.value]);

  return (
    <textarea
      {...props}
      ref={textareaRef}
      className={["takyon-auto-textarea", className].filter(Boolean).join(" ")}
      onInput={(event) => {
        resizeTextarea(event.currentTarget, maxAutoHeight);
        onInput?.(event);
      }}
    />
  );
}
