import type { Metadata } from "next";
import ResetButton from "@/components/ResetButton";
import "./globals.css";

export const metadata: Metadata = {
  title: "Basis Trail",
  description: "Three banks' statements, one reconciled ledger, a short exception queue.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: browser extensions add attributes to <html>
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen">
        <div className="bg-amber-100 px-4 py-1.5 text-center text-sm font-medium text-amber-900">
          Synthetic data — no client information
        </div>
        <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-5">
          <div>
            <h1 className="text-xl font-semibold text-ink">Basis Trail</h1>
            <p className="text-sm text-muted">
              Lim family · Harbour Private Bank, Meridian Wealth, Straits Custody · tax year 2025
            </p>
          </div>
          <ResetButton />
        </header>
        <main className="mx-auto max-w-5xl px-4 pb-16">{children}</main>
      </body>
    </html>
  );
}