"use client";

import { useState } from "react";
import { api } from "@/lib/api";

// Clears every decision so the demo starts clean, then reloads the page.
export default function ResetButton() {
  const [busy, setBusy] = useState(false);

  async function reset() {
    if (!confirm("Clear all decisions and restart the demo?")) return;
    setBusy(true);
    try {
      await api.reset();
      window.location.reload();
    } catch (e) {
      alert(`Reset failed: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  return (
    <button
      onClick={reset}
      disabled={busy}
      className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:hover:bg-gray-800"
    >
      {busy ? "Resetting…" : "Reset demo"}
    </button>
  );
}