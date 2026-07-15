import { MatchList } from "./match-list";

export default function Home() {
  return (
    <main className="page-main">
      <section className="home-shell" aria-label="Match analyzer">
        <div className="page-header">
          <div>
            <p className="eyebrow">01 / Match intelligence</p>
            <h1>Tier 1 matches.<br />Clear signals.</h1>
          </div>
          <p className="subtitle">
            Formula, Elo and local ML combined into one guarded pre-match view.
          </p>
        </div>
        <MatchList />
      </section>
    </main>
  );
}
