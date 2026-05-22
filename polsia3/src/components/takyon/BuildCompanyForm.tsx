"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Rocket } from "lucide-react";

export function BuildCompanyForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    const payload = {
      name: String(form.get("name") || ""),
      pitch: String(form.get("pitch") || ""),
      customer: String(form.get("customer") || ""),
      pain: String(form.get("pain") || ""),
      offer: String(form.get("offer") || "")
    };

    startTransition(async () => {
      const response = await fetch("/api/companies", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload)
      });
      const body = (await response.json()) as { ok?: boolean; error?: string; company?: { id: string } };
      if (!response.ok || !body.ok || !body.company) {
        setError(body.error || "Company build could not be queued.");
        return;
      }
      router.push(`/dashboard/companies/${body.company.id}`);
      router.refresh();
    });
  }

  return (
    <form className="panel form-grid" onSubmit={onSubmit}>
      <div>
        <p className="eyebrow">Build Company</p>
        <h2>Website first, product lanes in parallel</h2>
      </div>
      <label className="field">
        <span className="label">Company name</span>
        <input className="input" name="name" placeholder="Four Manifold" required minLength={2} maxLength={120} />
      </label>
      <label className="field">
        <span className="label">What should it sell?</span>
        <textarea className="textarea" name="pitch" placeholder="A concise offer, who it helps, and why now." required minLength={8} maxLength={1200} />
      </label>
      <label className="field">
        <span className="label">Customer</span>
        <input className="input" name="customer" placeholder="Founders, agencies, operators..." maxLength={240} />
      </label>
      <label className="field">
        <span className="label">Pain</span>
        <input className="input" name="pain" placeholder="The expensive problem this should solve." maxLength={500} />
      </label>
      <label className="field">
        <span className="label">Offer</span>
        <input className="input" name="offer" placeholder="The first product workflow or promise." maxLength={500} />
      </label>
      {error ? <p className="status-pill failed">{error}</p> : null}
      <button className="button" type="submit" disabled={isPending}>
        <Rocket size={17} />
        {isPending ? "Queuing" : "Build company"}
      </button>
    </form>
  );
}
