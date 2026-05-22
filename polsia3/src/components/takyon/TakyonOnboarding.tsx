"use client";

import { useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { AutoResizeTextarea } from "./AutoResizeTextarea";

type TakyonOnboardingProps = {
  actionUrl?: string;
  initialIdea?: string;
  businessName?: string;
  template?: string;
  brandName?: string;
};

export function TakyonOnboarding({ actionUrl = "/new/takyon/start", initialIdea = "", businessName, template, brandName = "Takyon" }: TakyonOnboardingProps) {
  const formRef = useRef<HTMLFormElement>(null);
  const [submitting, setSubmitting] = useState(false);

  return (
    <main className="takyon-root takyon-onboarding">
      <div className="takyon-aurora" />
      <header className="takyon-onboarding-header">
        <a className="takyon-wordmark" href="/dashboard/takyon">
          <span />
          {brandName}
        </a>
      </header>

      <section className="takyon-onboarding-center">
        <h1>
          What do you want
          <br />
          to <em>build</em>?
        </h1>
        <form ref={formRef} action={actionUrl} method="post" className="takyon-prompt-box" onSubmit={() => setSubmitting(true)}>
          {businessName ? <input name="businessName" type="hidden" value={businessName} /> : null}
          {template ? <input name="template" type="hidden" value={template} /> : null}
          <AutoResizeTextarea
            autoFocus
            name="businessIdea"
            required
            maxLength={8000}
            maxAutoHeight={240}
            defaultValue={initialIdea}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (formRef.current?.reportValidity()) {
                  formRef.current.requestSubmit();
                }
              }
            }}
            rows={1}
            placeholder="Describe a company and the team starts building it..."
          />
          <button type="submit" disabled={submitting} aria-label="Start">
            <ArrowUp size={17} />
          </button>
        </form>
      </section>
    </main>
  );
}
