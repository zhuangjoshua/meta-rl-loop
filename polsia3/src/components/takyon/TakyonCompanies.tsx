import type { TakyonCompanyCard } from "@/lib/takyon-dashboard";
import { AutoResizeTextarea } from "./AutoResizeTextarea";

type TakyonCompaniesProps = {
  companies: TakyonCompanyCard[];
};

export function TakyonCompanies({ companies }: TakyonCompaniesProps) {
  return (
    <main className="takyon-root takyon-companies">
      <header className="takyon-companies-header">
        <a className="takyon-wordmark" href="/dashboard/takyon">
          <span />
          Takyon
        </a>
        <a className="takyon-small-link" href="/dashboard">
          Classic dashboard
        </a>
      </header>

      <section className="takyon-company-intake">
        <div>
          <p>Company operator</p>
          <h1>Build or open a company.</h1>
        </div>
        <form action="/new/takyon/start" method="post" className="takyon-inline-intake">
          <AutoResizeTextarea
            name="businessIdea"
            required
            maxLength={8000}
            rows={2}
            maxAutoHeight={180}
            placeholder="Describe the company you want built..."
          />
          <button type="submit">Start</button>
        </form>
      </section>

      <section className="takyon-company-grid">
        {companies.map((company) => (
          <a key={company.id} className="takyon-company-card" href={company.href}>
            <div className="takyon-company-card-top">
              <strong>{company.name}</strong>
              <span>{company.status}</span>
            </div>
            <div className="takyon-card-chart" aria-hidden>
              <span style={{ height: "32%" }} />
              <span style={{ height: "46%" }} />
              <span style={{ height: "41%" }} />
              <span style={{ height: "62%" }} />
              <span style={{ height: "58%" }} />
              <span style={{ height: "78%" }} />
            </div>
            <div className="takyon-company-card-bottom">
              <span>{company.role}</span>
              <span>Open</span>
            </div>
          </a>
        ))}
      </section>

      {companies.length === 0 ? <p className="takyon-empty-line">No companies yet.</p> : null}
    </main>
  );
}
