const portfolio = [
  { name: "Threadline", detail: "Turns customer-call notes into saved summaries and action items." },
  { name: "ProposalFlow", detail: "Turns client requirements into proposals that can be saved and reopened." },
  { name: "ClearPaste", detail: "Turns source text into clean, shareable publishing workflows." },
  { name: "NoteFlow", detail: "Turns quick notes into an organized, searchable daily journal." },
];

export function SocialProofMarquee() {
  const repeated = [...portfolio, ...portfolio];
  return (
    <section className="overflow-hidden border-y border-border bg-card py-8" aria-labelledby="coscale-proof-title">
      <div className="px-6 sm:px-8 lg:px-12">
        <p id="coscale-proof-title" className="text-center text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          Used by professionals building with Coscale
        </p>
      </div>
      <div className="mt-5 flex w-max animate-proof-marquee gap-4 hover:[animation-play-state:paused] motion-reduce:animate-none">
        {repeated.map((item, index) => (
          <article
            key={`${item.name}-${index}`}
            className="flex w-[22rem] shrink-0 items-start gap-4 rounded-xl border border-border bg-background p-4 shadow-sm"
            aria-hidden={index >= portfolio.length ? true : undefined}
          >
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-primary text-sm font-semibold text-primary-foreground" aria-hidden="true">
              {item.name.slice(0, 1)}
            </div>
            <div>
              <p className="font-semibold text-foreground">{item.name}</p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.detail}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
