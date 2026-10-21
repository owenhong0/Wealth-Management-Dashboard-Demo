"use client";

import { useEffect, useState } from "react";
import { api, type SourceExcerpt, type SourceRef } from "@/lib/api";

const BANK_NAME: Record<string, string> = {
  harbour: "Harbour Private Bank · CSV export",
  meridian: "Meridian Wealth · PDF statement",
  straits: "Straits Custody · SWIFT MT535/MT536",
};

// A citation chip; clicking it opens the original file at the cited line.
export default function EvidenceChip({ source }: { source: SourceRef }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Show this line in the original file"
        className="rounded bg-black/5 px-2 py-0.5 font-mono text-xs text-ink hover:bg-black/10 dark:bg-white/10 dark:hover:bg-white/20"
      >
        {source.label}
      </button>
      {open && <SourceViewer source={source} onClose={() => setOpen(false)} />}
    </>
  );
}

function SourceViewer({ source, onClose }: { source: SourceRef; onClose: () => void }) {
  const [excerpt, setExcerpt] = useState<SourceExcerpt | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.source(source).then(setExcerpt).catch((e: Error) => setError(e.message));
  }, [source]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Source: ${source.label}`}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-3xl rounded-lg border border-gray-300 bg-white p-4 shadow-xl dark:border-gray-700 dark:bg-gray-900"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted">
              {BANK_NAME[source.bank] ?? source.bank}
            </p>
            <p className="mt-0.5 font-mono text-sm text-ink">{source.label}</p>
          </div>
          <button onClick={onClose} className="text-sm text-muted hover:text-ink">
            Close
          </button>
        </div>

        <div className="mt-3 overflow-x-auto rounded-md bg-black/5 py-2 font-mono text-xs dark:bg-white/5">
          {error && <p className="px-3 text-red-600">{error}</p>}
          {!error && !excerpt && <p className="px-3 text-muted">Loading…</p>}
          {excerpt?.lines.map((l) => (
            <div
              key={l.n}
              className={`flex gap-3 whitespace-pre px-3 py-0.5 ${
                l.cited ? "bg-amber-500/20 font-semibold text-ink" : "text-muted"
              }`}
            >
              <span className="w-8 shrink-0 select-none text-right opacity-60">{l.n}</span>
              <span>{l.text}</span>
            </div>
          ))}
        </div>

        <p className="mt-2 text-xs text-muted">
          Read from the original file. The highlighted line is the one this exception cites.
        </p>
      </div>
    </div>
  );
}