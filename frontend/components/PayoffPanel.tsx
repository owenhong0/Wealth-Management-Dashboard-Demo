import { usd, type Payoff, type PayoffChange } from "@/lib/api";

type TileProps = { label: string; hint: string; field: keyof PayoffChange; payoff: Payoff };

// Shows the current figure; once a decision has moved it, shows baseline → now in green.
function Tile({ label, hint, field, payoff }: TileProps) {
  const before = payoff.baseline[field];
  const now = payoff.current[field];
  const change = Number(payoff.change[field]);

  return (
    <div className="rounded-lg border border-gray-300 p-4 dark:border-gray-700">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{usd(now)}</p>
      {change !== 0 ? (
        <p
          className={`mt-1 text-sm tabular-nums ${
            change < 0 ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"
          }`}
        >
          {usd(before)} → {usd(now)} ({change < 0 ? "−" : "+"}
          {usd(Math.abs(change))})
        </p>
      ) : (
        <p className="mt-1 text-sm text-muted">{hint}</p>
      )}
    </div>
  );
}

export default function PayoffPanel({ payoff }: { payoff: Payoff }) {
  const c = payoff.current;
  const rate = Math.round(Number(c.tax_rate) * 100);

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-ink">{c.tax_year} realized gains (USD)</h2>
        <p className="text-xs text-muted">
          {c.cost_basis_method} · tax at {rate}%
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Tile
          label="Taxable, account by account"
          hint="Each bank filed separately; losses never offset gains"
          field="taxable_account_by_account_usd"
          payoff={payoff}
        />
        <Tile
          label="Taxable, consolidated"
          hint="Losses at one bank offset gains at another"
          field="taxable_consolidated_usd"
          payoff={payoff}
        />
        <Tile
          label="Market value, unevidenced basis"
          hint={`${c.unevidenced_positions.length} positions with no evidenced cost`}
          field="unevidenced_market_value_usd"
          payoff={payoff}
        />
      </div>

      <p className="text-sm text-muted">
        Tax due: {usd(c.tax_account_by_account_usd)} filed account by account vs {usd(c.tax_consolidated_usd)}{" "}
        consolidated. Consolidating saves {usd(payoff.consolidation_saves_tax_usd)}.
      </p>
    </section>
  );
}