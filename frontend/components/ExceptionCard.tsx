"use client";

import {useState} from "react";
import type {ExceptionStatus, ExceptionType, ReconException, SourceRef} from "@/lib/api";
import EvidenceChip from "@/components/EvidenceChip";

const TYPE_LABEL: Record<ExceptionType, string> = {
    BASIS_MISSING: "Missing cost basis",
    NAME_CONFLICT: "Unknown security name",
    POSITION_BREAK: "Position break",
    UNMATCHED_TRANSFER: "Unmatched transfer",
};

const TYPE_STYLE: Record<ExceptionType, string> = {
    BASIS_MISSING: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
    NAME_CONFLICT: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
    POSITION_BREAK: "bg-red-500/15 text-red-700 dark:text-red-300",
    UNMATCHED_TRANSFER: "bg-gray-500/15 text-gray-700 dark:text-gray-300",
};

const BUTTON_PRIMARY =
    "rounded-md bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50";
const BUTTON_OUTLINE =
    "rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-ink hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:hover:bg-gray-800";

// Evidence chips shown before "+N more".
const EVIDENCE_PREVIEW = 4;

// Same file together, then page, then line, so the chips read in statement order.
function sortEvidence(refs: SourceRef[]): SourceRef[] {
    return [...refs].sort(
        (a, b) => a.file.localeCompare(b.file) || (a.page ?? 0) - (b.page ?? 0) || a.line - b.line,
    );
}

type Props = {
    exc: ReconException;
    onApprove: (id: string) => Promise<void>;
    onOverride: (id: string, note: string) => Promise<void>;
};

export default function ExceptionCard({exc, onApprove, onOverride}: Props) {
    const [overriding, setOverriding] = useState(false);
    const [note, setNote] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showAllEvidence, setShowAllEvidence] = useState(false);

    const open = exc.status === "OPEN";
    const hasFix = exc.fix !== null;
    // With a proposed fix, the alternative is overriding it; without one, a person closes it with a note.
    const overrideLabel = hasFix ? "Override" : "Close with note";

    const evidence = sortEvidence(exc.evidence);
    const hidden = evidence.length - EVIDENCE_PREVIEW;
    const visible = showAllEvidence || hidden <= 0 ? evidence : evidence.slice(0, EVIDENCE_PREVIEW);

    async function run(action: () => Promise<void>) {
        setBusy(true);
        setError(null);
        try {
            await action();
            setOverriding(false);
            setNote("");
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <article
            className={`rounded-lg border p-4 ${
                open ? "border-gray-300 dark:border-gray-700" : "border-gray-200 opacity-75 dark:border-gray-800"
            }`}
        >
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <span
                    className={`rounded px-2 py-0.5 font-medium ${TYPE_STYLE[exc.type]}`}>{TYPE_LABEL[exc.type]}</span>
                <span className="text-muted">
          {exc.account_id} · {exc.isin}
        </span>
                <StatusPill status={exc.status}/>
            </div>

            <p className="mt-2 text-ink">{exc.summary}</p>

            <div className="mt-3">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">
                    Evidence · {evidence.length} source {evidence.length === 1 ? "line" : "lines"}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {visible.map((ref, i) => (
                        <EvidenceChip key={`${ref.label}-${i}`} source={ref}/>
                    ))}
                    {hidden > 0 && (
                        <button
                            onClick={() => setShowAllEvidence((v) => !v)}
                            className="px-1 text-xs font-medium text-muted underline-offset-2 hover:underline"
                        >
                            {showAllEvidence ? "show less" : `+${hidden} more`}
                        </button>
                    )}
                </div>
            </div>

            <div className="mt-3 rounded-md bg-black/5 p-3 text-sm dark:bg-white/5">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">
                    {hasFix ? "Proposed fix" : "Needs follow-up"}
                    {exc.confidence != null && ` · ${Math.round(exc.confidence * 100)}% confidence`}
                </p>
                <p className="mt-1 text-ink">{exc.proposed_fix}</p>
            </div>

            {open && !overriding && (
                <div className="mt-3 flex gap-2">
                    {hasFix && (
                        <button className={BUTTON_PRIMARY} disabled={busy}
                                onClick={() => run(() => onApprove(exc.exception_id))}>
                            {busy ? "Saving…" : "Approve"}
                        </button>
                    )}
                    <button className={BUTTON_OUTLINE} disabled={busy} onClick={() => setOverriding(true)}>
                        {overrideLabel}
                    </button>
                </div>
            )}

            {open && overriding && (
                <div className="mt-3 space-y-2">
          <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder={
                  hasFix
                      ? "Why are you overriding the proposed fix? Required; it goes in the audit log."
                      : "What was done or decided? Required; it goes in the audit log."
              }
              className="w-full rounded-md border border-gray-300 bg-transparent p-2 text-sm text-ink dark:border-gray-600"
          />
                    <div className="flex gap-2">
                        <button
                            className={BUTTON_PRIMARY}
                            disabled={busy || !note.trim()}
                            onClick={() => run(() => onOverride(exc.exception_id, note.trim()))}
                        >
                            {busy ? "Saving…" : "Save"}
                        </button>
                        <button
                            className={BUTTON_OUTLINE}
                            disabled={busy}
                            onClick={() => {
                                setOverriding(false);
                                setNote("");
                            }}
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            )}

            {!open && exc.decided_at && (
                <p className="mt-3 text-xs text-muted">
                    {exc.status === "APPROVED" ? "Approved" : hasFix ? "Overridden" : "Closed"}
                    {exc.decided_by ? ` by ${exc.decided_by}` : ""} · {new Date(exc.decided_at).toLocaleString()}
                </p>
            )}

            {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </article>
    );
}

function StatusPill({status}: { status: ExceptionStatus }) {
    const style =
        status === "OPEN"
            ? "border-amber-500/40 text-amber-700 dark:text-amber-300"
            : status === "APPROVED"
                ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
                : "border-gray-500/40 text-muted";
    return <span className={`ml-auto rounded-full border px-2 py-0.5 ${style}`}>{status.toLowerCase()}</span>;
}