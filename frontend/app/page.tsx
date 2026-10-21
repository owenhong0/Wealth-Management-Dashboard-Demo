"use client";

import { useCallback, useEffect, useState } from "react";
import ExceptionCard from "@/components/ExceptionCard";
import PayoffPanel from "@/components/PayoffPanel";
import { api, type ExceptionList, type ExceptionType, type Payoff, type ReconException } from "@/lib/api";

// Fixed demo order: the Tencent basis fix first, because approving it moves the numbers.
// Order doesn't change with status, so a decided card stays where the audience saw it.
const ORDER: ExceptionType[] = ["BASIS_MISSING", "NAME_CONFLICT", "POSITION_BREAK", "UNMATCHED_TRANSFER"];

function sortQueue(items: ReconException[]): ReconException[] {
  return [...items].sort((a, b) => ORDER.indexOf(a.type) - ORDER.indexOf(b.type));
}

export default function Home() {
  const [queue, setQueue] = useState<ExceptionList | null>(null);
  const [payoff, setPayoff] = useState<Payoff | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The API recomputes everything from source on each call, so after any decision
  // we simply refetch both rather than patching local state.
  const refresh = useCallback(async () => {
    const [q, p] = await Promise.all([api.exceptions(), api.payoff()]);
    setQueue(q);
    setPayoff(p);
  }, []);

  useEffect(() => {
    refresh().catch((e: Error) => setError(e.message));
  }, [refresh]);

  // Errors here propagate to the card, which shows them under its buttons.
  async function approve(id: string) {
    await api.approve(id);
    await refresh();
  }

  async function override(id: string, note: string) {
    await api.override(id, note);
    await refresh();
  }

  if (error) return <p className="text-red-600">API error: {error}</p>;
  if (!queue || !payoff) return <p className="text-muted">Loading…</p>;

  const open = queue.counts.OPEN ?? 0;

  return (
    <div className="space-y-8">
      <PayoffPanel payoff={payoff} />

      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold text-ink">Exception queue</h2>
          <p className="text-sm text-muted">
            {open} open of {queue.total} · nothing changes until a person decides
          </p>
        </div>
        {sortQueue(queue.items).map((exc) => (
          <ExceptionCard key={exc.exception_id} exc={exc} onApprove={approve} onOverride={override} />
        ))}
      </section>
    </div>
  );
}