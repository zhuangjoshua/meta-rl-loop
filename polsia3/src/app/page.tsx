export default function HomePage({ searchParams }: { searchParams?: { business?: string; queued?: string } }) {
  const business = searchParams?.business;
  const queued = searchParams?.queued === "1";
  return (
    <main className="terminal-home">
      <section className="terminal-home-panel">
        <p className="eyebrow">Takyon Operator Surface</p>
        <h1>Use the terminal.</h1>
        <p>
          The web dashboard is disabled. Business operation, filesystem inspection, test mode,
          pause/resume, cron visibility, and CEO wakeups live in the Takyon shell.
        </p>
        {business ? (
          <p className="terminal-home-note">
            {queued ? "Build queued for " : "Business: "}
            <strong>{business}</strong>
          </p>
        ) : null}
        <div className="terminal-home-commands" aria-label="Takyon terminal commands">
          <code>cd /Users/Zygote/Downloads/takyon/polsia3</code>
          <code>./takyon shell</code>
          <code>/businesses</code>
          <code>/use &lt;business-slug&gt;</code>
          <code>/workspace</code>
          <code>/files</code>
          <code>/read &lt;path&gt;</code>
          <code>/wake</code>
        </div>
      </section>
    </main>
  );
}
