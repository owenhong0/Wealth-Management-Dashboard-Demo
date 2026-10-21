// Typed client for the Basis Trail Flask API.
// Shapes mirror the JSON returned by /api/exceptions and /api/payoff.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5001";

// --- exceptions ------------------------------------------------------------------

export type ExceptionType =
    | "NAME_CONFLICT"
    | "BASIS_MISSING"
    | "UNMATCHED_TRANSFER"
    | "POSITION_BREAK";

export type ExceptionStatus = "OPEN" | "APPROVED" | "OVERRIDDEN";

export type SourceRef = {
    bank: string;
    file: string;
    line: number;
    page: number | null;
    label: string; // e.g. "meridian_statement.pdf p2 line 16"
};

export type ReconException = {
    exception_id: string;
    type: ExceptionType;
    account_id: string;
    isin: string;
    summary: string;
    details: Record<string, unknown>;
    affected_txn_ids: string[];
    proposed_fix: string;
    fix: Record<string, unknown> | null; // null = nothing to apply, a human must chase it
    confidence: number | null;
    status: ExceptionStatus;
    decided_by: string | null;
    decided_at: string | null;
    evidence: SourceRef[];
};

export type ExceptionList = {
    items: ReconException[];
    counts: Partial<Record<ExceptionStatus, number>>;
    total: number;
};

export type SourceLine = { n: number; text: string; cited: boolean };

export type SourceExcerpt = {
    file: string;
    page: number | null;
    line: number;
    lines: SourceLine[]; // the cited line plus a few either side
};

// --- payoff ----------------------------------------------------------------------

export type Lot = {
    acquired: string;
    quantity: string;
    unit_cost: string | null;
    currency: string;
    fx: string | null;
    cost_usd: string | null; // null = unevidenced
    source: string;
    via: unknown[];
};

export type Disposal = {
    txn_id: string;
    account_id: string;
    isin: string;
    security_name: string;
    date: string;
    quantity: string;
    proceeds_usd: string;
    cost_usd: string;
    gain_usd: string;
    unevidenced_quantity: string;
    source: string;
    lots: Lot[];
};

export type UnevidencedPosition = {
    account_id: string;
    isin: string;
    security_name: string;
    quantity: string;
    market_value_usd: string;
    sources: string[];
};

export type PayoffFigures = {
    tax_year: number;
    tax_rate: string;
    cost_basis_method: string;
    realized_gain_by_account_usd: Record<string, string>;
    taxable_account_by_account_usd: string;
    taxable_consolidated_usd: string;
    tax_account_by_account_usd: string;
    tax_consolidated_usd: string;
    unevidenced_market_value_usd: string;
    unevidenced_positions: UnevidencedPosition[];
    disposals: Disposal[];
};

export type PayoffChange = Pick<
    PayoffFigures,
    | "taxable_account_by_account_usd"
    | "taxable_consolidated_usd"
    | "tax_account_by_account_usd"
    | "tax_consolidated_usd"
    | "unevidenced_market_value_usd"
>;

export type Payoff = {
    current: PayoffFigures;
    baseline: PayoffFigures;
    change: PayoffChange;
    consolidation_saves_tax_usd: string;
};

// --- requests --------------------------------------------------------------------

export class ApiError extends Error {
    constructor(public status: number, message: string) {
        super(message);
    }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${API_URL}${path}`, {cache: "no-store", ...init});
    const body = await res.json().catch(() => null);
    if (!res.ok) {
        throw new ApiError(res.status, body?.error?.message ?? `${res.status} ${res.statusText}`);
    }
    return body as T;
}

function post(body?: unknown): RequestInit {
    return body === undefined
        ? {method: "POST"}
        : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
}

export const api = {
    exceptions: () => request<ExceptionList>("/api/exceptions"),
    payoff: () => request<Payoff>("/api/payoff"),
    approve: (id: string, note?: string) =>
        request<unknown>(`/api/exceptions/${encodeURIComponent(id)}/approve`, post({note: note ?? ""})),
    override: (id: string, note: string) =>
        request<unknown>(`/api/exceptions/${encodeURIComponent(id)}/override`, post({note})),
    reset: () => request<unknown>("/api/reset", post()),
    source: (ref: Pick<SourceRef, "file" | "page" | "line">) => {
        const q = new URLSearchParams({file: ref.file, line: String(ref.line)});
        if (ref.page != null) q.set("page", String(ref.page));
        return request<SourceExcerpt>(`/api/sources?${q}`);
    },
};

// --- formatting ------------------------------------------------------------------

/** "25242.00" -> "$25,242". Rounds to whole dollars for display only. */
export function usd(value: string | number): string {
    const n = typeof value === "string" ? Number(value) : value;
    return n.toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0});
}