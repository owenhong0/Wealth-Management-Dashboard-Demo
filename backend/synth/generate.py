"""Synthetic data for the Lim family: one true ledger, rendered as three banks' files.

    cd backend && source .venv/bin/activate
    python -m synth.generate

Writes to data/generated/:
    harbour_transactions.csv, harbour_holdings.csv   Harbour Private Bank (CSV export)
    meridian_statement.pdf                           Meridian Wealth (PDF statement)
    straits_mt535.txt, straits_mt536.txt             Straits Custody (SWIFT-style)
    secmaster.csv                                    reference data: ISIN + known aliases
    fx_rates.csv                                     USD per unit of currency, by year
    ground_truth.json                                what the pipeline must produce

ALL DATA IS FICTIONAL. Listed companies are real, but every quantity, price,
trade and dividend is invented. The bond and its issuer do not exist.

Planted problems (each one must show up in the app):
  1. NAME_CONFLICT       Meridian names the bond by its full legal name, which
                         the securities master doesn't know yet.
  2. BASIS_MISSING       Tencent moves Harbour -> Straits on 14 Jun 2024. The
                         Straits receipt has no cost; Harbour's delivery has the
                         same quantity and date, so the basis can be carried over.
  3. UNMATCHED_TRANSFER  Singtel arrives at Straits on 5 Nov 2024 from a custodian
                         we have no data for. Its basis stays unevidenced.
  4. POSITION_BREAK      Meridian's 2025 statement omits a sale of 10 ASML, so
                         opening + trades != closing. Flagged, never plugged.
  5. Loss vs gain        2025: Meridian realizes a loss, Harbour and Straits gains,
                         so consolidated gains < account-by-account gains.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config import GENERATED_DIR

D = Decimal
YEARS = (2023, 2024, 2025)
TAX_YEAR = 2025
TAX_RATE = D("0.20")  # Announcement No. 21 rate on offshore trust events

# --- banks and accounts ------------------------------------------------------------

HARBOUR, MERIDIAN, STRAITS = "harbour", "meridian", "straits"
ACCOUNTS = {
    HARBOUR: {"account_id": "HPB-001", "bank_name": "Harbour Private Bank", "city": "Hong Kong"},
    MERIDIAN: {"account_id": "MW-7731", "bank_name": "Meridian Wealth", "city": "Hong Kong"},
    STRAITS: {"account_id": "SC-4420", "bank_name": "Straits Custody", "city": "Singapore"},
}
CLIENT = "Lim Family Holdings Ltd (synthetic)"

# --- securities ---------------------------------------------------------------------
# names: how each bank writes the security. The secmaster gets every alias EXCEPT
# the ones listed in UNKNOWN_ALIASES (that's planted problem 1).

BOND = "XS9999LIM016"


@dataclass(frozen=True)
class Sec:
    isin: str
    name: str
    asset_class: str  # EQUITY | BOND
    currency: str
    names: dict[str, str]

    @property
    def factor(self) -> Decimal:
        return D("0.01") if self.asset_class == "BOND" else D(1)


SECURITIES = [
    Sec("US0378331005", "Apple Inc", "EQUITY", "USD",
        {HARBOUR: "APPLE INC COM", STRAITS: "APPLE INC"}),
    Sec("US5949181045", "Microsoft Corp", "EQUITY", "USD",
        {HARBOUR: "MICROSOFT CORP COM"}),
    Sec("KYG875721634", "Tencent Holdings Ltd", "EQUITY", "HKD",
        {HARBOUR: "TENCENT HLDGS 0700 HK", STRAITS: "TENCENT HOLDINGS LTD"}),
    Sec("GB0005405286", "HSBC Holdings plc", "EQUITY", "HKD",
        {HARBOUR: "HSBC HLDGS 0005 HK"}),
    Sec("HK0000069689", "AIA Group Ltd", "EQUITY", "HKD",
        {STRAITS: "AIA GROUP LTD"}),
    Sec("SG1L01001701", "DBS Group Holdings Ltd", "EQUITY", "SGD",
        {STRAITS: "DBS GROUP HOLDINGS LTD"}),
    Sec("SG1T75931496", "Singapore Telecommunications Ltd", "EQUITY", "SGD",
        {STRAITS: "SINGAPORE TELECOMMUNICATIONS LTD"}),
    Sec("NL0010273215", "ASML Holding NV", "EQUITY", "EUR",
        {MERIDIAN: "ASML Holding N.V."}),
    Sec("DE0007164600", "SAP SE", "EQUITY", "EUR",
        {MERIDIAN: "SAP SE"}),
    # Fictional issuer, fictional ISIN (valid check digit). Same bond, three names:
    Sec(BOND, "Pacific Harbour Infrastructure 4.25% 2030", "BOND", "USD",
        {HARBOUR: "PHI 4.25 03/15/30",
         MERIDIAN: "Pacific Harbour Infrastructure Limited 4.250% Senior Notes due 15 March 2030",
         STRAITS: "PAC HBR INFRA 4.25 2030"}),
]
SEC = {s.isin: s for s in SECURITIES}
UNKNOWN_ALIASES = {(MERIDIAN, BOND)}

# --- reference data -------------------------------------------------------------------

FX = {  # USD per 1 unit, one rate per year to keep every number hand-checkable
    "USD": {2023: D("1"), 2024: D("1"), 2025: D("1")},
    "HKD": {2023: D("0.128"), 2024: D("0.128"), 2025: D("0.128")},
    "SGD": {2023: D("0.745"), 2024: D("0.75"), 2025: D("0.77")},
    "EUR": {2023: D("1.08"), 2024: D("1.09"), 2025: D("1.12")},
}

YEAR_END_PRICE = {  # bond prices are per 100 face
    "US0378331005": {2023: D("190"), 2024: D("248"), 2025: D("230")},
    "US5949181045": {2023: D("375"), 2024: D("420"), 2025: D("450")},
    "KYG875721634": {2023: D("295"), 2024: D("415"), 2025: D("560")},
    "GB0005405286": {2023: D("63"), 2024: D("76"), 2025: D("98")},
    "HK0000069689": {2023: D("68.05"), 2024: D("56.40"), 2025: D("74")},
    "SG1L01001701": {2023: D("33.40"), 2024: D("43.70"), 2025: D("50")},
    "SG1T75931496": {2023: D("2.47"), 2024: D("3.08"), 2025: D("4.30")},
    "NL0010273215": {2023: D("680"), 2024: D("677"), 2025: D("710")},
    "DE0007164600": {2023: D("139"), 2024: D("236"), 2025: D("250")},
    BOND: {2023: D("100.20"), 2024: D("99.40"), 2025: D("101.10")},
}

# --- the true ledger ------------------------------------------------------------------


@dataclass(frozen=True)
class T:
    bank: str
    trade_date: date
    type: str          # BUY SELL TRANSFER_IN TRANSFER_OUT DIVIDEND COUPON FEE
    isin: str | None
    quantity: Decimal = D(0)
    price: Decimal | None = None
    amount: Decimal | None = None   # gross, positive, in currency; None for free transfers
    currency: str = "USD"
    counterparty: str | None = None  # for transfers: the other custodian
    hidden: bool = False             # True = happened, but the bank's statement omits it

    @property
    def settle_date(self) -> date:
        if self.type in ("TRANSFER_IN", "TRANSFER_OUT", "DIVIDEND", "COUPON", "FEE"):
            return self.trade_date
        d = self.trade_date + timedelta(days=2)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d


def trade(bank, d, side, isin, qty, price) -> T:
    s = SEC[isin]
    qty, price = D(qty), D(price)
    return T(bank, d, side, isin, qty, price, money(qty * price * s.factor), s.currency)


def income(bank, d, kind, isin, qty, rate) -> T:
    """Dividend per share, or coupon rate per year (semiannual payment)."""
    s = SEC[isin]
    qty, rate = D(qty), D(rate)
    amount = qty * rate if kind == "DIVIDEND" else qty * rate / 100 / 2
    return T(bank, d, kind, isin, qty, None, money(amount), s.currency)


def fee(bank, d, amount, ccy) -> T:
    return T(bank, d, "FEE", None, D(0), None, money(D(amount)), ccy)


def money(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


AAPL, MSFT, TCEHY, HSBC, AIA, DBS, STEL, ASML, SAP = (
    "US0378331005", "US5949181045", "KYG875721634", "GB0005405286", "HK0000069689",
    "SG1L01001701", "SG1T75931496", "NL0010273215", "DE0007164600",
)


def build_ledger() -> list[T]:
    L: list[T] = []
    H, M, S = HARBOUR, MERIDIAN, STRAITS

    # Harbour Private Bank -----------------------------------------------------------
    L += [
        trade(H, date(2023, 1, 16), "BUY", AAPL, 300, "150"),
        trade(H, date(2023, 2, 10), "BUY", TCEHY, 300, "380"),
        trade(H, date(2023, 3, 15), "BUY", BOND, 200_000, "99.50"),
        trade(H, date(2023, 6, 20), "BUY", HSBC, 2_000, "60"),
        trade(H, date(2023, 9, 5), "BUY", TCEHY, 200, "320"),
        trade(H, date(2024, 2, 8), "BUY", MSFT, 100, "405"),
        T(H, date(2024, 6, 14), "TRANSFER_OUT", TCEHY, D(400), currency="HKD",
          counterparty="STRAITS CUSTODY SG"),
        trade(H, date(2025, 4, 22), "SELL", AAPL, 100, "205"),
    ]
    aapl_div = {2023: "0.24", 2024: "0.25", 2025: "0.26"}
    for d in [date(2023, 2, 16), date(2023, 5, 18), date(2023, 8, 17), date(2023, 11, 16),
              date(2024, 2, 15), date(2024, 5, 16), date(2024, 8, 15), date(2024, 11, 14),
              date(2025, 2, 13), date(2025, 5, 15), date(2025, 8, 14), date(2025, 11, 13)]:
        qty = 300 if d < date(2025, 4, 22) else 200
        L.append(income(H, d, "DIVIDEND", AAPL, qty, aapl_div[d.year]))
    for d in [date(2024, 3, 14), date(2024, 6, 13), date(2024, 9, 12), date(2024, 12, 12),
              date(2025, 3, 13), date(2025, 6, 12), date(2025, 9, 11), date(2025, 12, 11)]:
        L.append(income(H, d, "DIVIDEND", MSFT, 100, "0.75" if d.year == 2024 else "0.83"))
    for d, rate in [(date(2023, 9, 28), "0.80"), (date(2024, 4, 25), "0.85"), (date(2024, 9, 26), "0.85"),
                    (date(2025, 4, 24), "0.90"), (date(2025, 9, 25), "0.90")]:
        L.append(income(H, d, "DIVIDEND", HSBC, 2_000, rate))
    for d in [date(2023, 9, 15), date(2024, 3, 15), date(2024, 9, 15), date(2025, 3, 15), date(2025, 9, 15)]:
        L.append(income(H, d, "COUPON", BOND, 200_000, "4.25"))
    for d in [date(2023, 12, 29), date(2024, 12, 30), date(2025, 12, 30)]:
        L.append(fee(H, d, "1200", "USD"))

    # Meridian Wealth ---------------------------------------------------------------
    L += [
        trade(M, date(2023, 4, 3), "BUY", SAP, 150, "115"),
        trade(M, date(2023, 7, 20), "BUY", BOND, 100_000, "98.75"),
        trade(M, date(2024, 3, 11), "BUY", ASML, 120, "780"),
        trade(M, date(2025, 6, 17), "SELL", ASML, 40, "610"),
    ]
    # Planted problem 4: this sale happened but the PDF statement leaves it out.
    hidden = trade(M, date(2025, 8, 12), "SELL", ASML, 10, "640")
    L.append(T(**{**hidden.__dict__, "hidden": True}))
    for d, rate in [(date(2023, 5, 15), "2.05"), (date(2024, 5, 17), "2.20"), (date(2025, 5, 16), "2.35")]:
        L.append(income(M, d, "DIVIDEND", SAP, 150, rate))
    for d in [date(2023, 9, 15), date(2024, 3, 15), date(2024, 9, 15), date(2025, 3, 15), date(2025, 9, 15)]:
        L.append(income(M, d, "COUPON", BOND, 100_000, "4.25"))
    for d in [date(2023, 12, 29), date(2024, 12, 30), date(2025, 12, 30)]:
        L.append(fee(M, d, "450", "EUR"))

    # Straits Custody (income arrives by other message types; not modelled) -----------
    L += [
        trade(S, date(2023, 5, 8), "BUY", DBS, 1_000, "33.00"),
        trade(S, date(2023, 8, 14), "BUY", BOND, 50_000, "99.10"),
        trade(S, date(2023, 10, 2), "BUY", AIA, 1_500, "68"),
        # Planted problem 2: arrives free, no cost. Harbour's delivery matches it.
        T(S, date(2024, 6, 14), "TRANSFER_IN", TCEHY, D(400), currency="HKD",
          counterparty="HARBOUR PRIVATE BANK HK"),
        # Planted problem 3: arrives from a custodian we have no statements for.
        T(S, date(2024, 11, 5), "TRANSFER_IN", STEL, D(2_000), currency="SGD",
          counterparty="KESTREL SECURITIES SG"),
        trade(S, date(2025, 3, 20), "SELL", TCEHY, 250, "520"),
        trade(S, date(2025, 7, 9), "SELL", DBS, 400, "42.00"),
    ]
    return sorted(L, key=lambda t: (t.trade_date, t.bank, t.type, t.isin or ""))


QTY_SIGN = {"BUY": 1, "TRANSFER_IN": 1, "SELL": -1, "TRANSFER_OUT": -1}


def holdings(ledger: list[T], bank: str, as_of: date) -> dict[str, Decimal]:
    """True units held at `as_of`, including transactions a statement omits."""
    pos: dict[str, Decimal] = defaultdict(D)
    for t in ledger:
        if t.bank == bank and t.trade_date <= as_of and t.type in QTY_SIGN:
            pos[t.isin] += QTY_SIGN[t.type] * t.quantity
    return {isin: q for isin, q in pos.items() if q != 0}


def year_end(y: int) -> date:
    return date(y, 12, 31)


# --- formatting helpers ----------------------------------------------------------------

def num(x: Decimal, places: int = 2) -> str:
    """1234.5 -> '1,234.50'"""
    return f"{x:,.{places}f}"


def plain(x: Decimal) -> str:
    """Shortest exact form: 33.00 -> '33', 99.50 -> '99.5'."""
    s = format(x.normalize(), "f")
    return s


def swift_num(x: Decimal) -> str:
    """SWIFT style: comma decimal, no thousands separator, always a comma. 42.5 -> '42,5'."""
    s = plain(x)
    return s.replace(".", ",") if "." in s else s + ","


# --- Harbour: CSV ----------------------------------------------------------------------

HARBOUR_CODES = {"BUY": "B", "SELL": "S", "TRANSFER_OUT": "XO", "TRANSFER_IN": "XI",
                 "DIVIDEND": "DV", "COUPON": "CP", "FEE": "FE"}


def render_harbour(ledger: list[T], out: Path) -> None:
    acct = ACCOUNTS[HARBOUR]["account_id"]
    with open(out / "harbour_transactions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Trade Dt", "Settle Dt", "Acct No", "Txn Code", "Sec Desc", "ISIN",
                    "Qty", "Price", "Ccy", "Net Amt", "Remarks"])
        for t in ledger:
            if t.bank != HARBOUR or t.hidden:
                continue
            s = SEC.get(t.isin) if t.isin else None
            sign = -1 if t.type in ("BUY", "FEE") else 1
            w.writerow([
                t.trade_date.strftime("%d/%m/%Y"),
                t.settle_date.strftime("%d/%m/%Y"),
                acct,
                HARBOUR_CODES[t.type],
                s.names[HARBOUR] if s else "CUSTODY FEE",
                t.isin or "",
                plain(t.quantity) if t.quantity else "",
                plain(t.price) if t.price is not None else "",
                t.currency,
                f"{sign * t.amount:.2f}" if t.amount is not None else "",
                f"Delivered free to {t.counterparty}" if t.counterparty else "",
            ])

    with open(out / "harbour_holdings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["As Of", "Acct No", "Sec Desc", "ISIN", "Qty", "Mkt Px", "Ccy", "Mkt Val"])
        for y in YEARS:
            for isin, qty in sorted(holdings(ledger, HARBOUR, year_end(y)).items()):
                s = SEC[isin]
                px = YEAR_END_PRICE[isin][y]
                w.writerow([year_end(y).strftime("%d/%m/%Y"), acct, s.names[HARBOUR], isin,
                            plain(qty), plain(px), s.currency, f"{money(qty * px * s.factor):.2f}"])


# --- Meridian: PDF ---------------------------------------------------------------------

MERIDIAN_LABELS = {"BUY": "Purchase", "SELL": "Sale", "DIVIDEND": "Dividend",
                   "COUPON": "Coupon", "FEE": "Fee",
                   "TRANSFER_IN": "Transfer in", "TRANSFER_OUT": "Transfer out"}


def _qty_text(isin: str, qty: Decimal) -> str:
    return f"{num(qty, 0)} nominal" if SEC[isin].asset_class == "BOND" else num(qty, 0)


def _position_rows(ledger, as_of: date, year: int) -> list[list[str]]:
    rows = [["Security", "Quantity", "Price", "Ccy", "Market value"]]
    for isin, qty in sorted(holdings(ledger, MERIDIAN, as_of).items(),
                            key=lambda kv: SEC[kv[0]].names[MERIDIAN]):
        s = SEC[isin]
        px = YEAR_END_PRICE[isin][year]
        rows.append([s.names[MERIDIAN], _qty_text(isin, qty), num(px), s.currency,
                     num(money(qty * px * s.factor))])
    if len(rows) == 1:
        rows.append(["No positions held", "", "", "", ""])
    return rows


def render_meridian(ledger: list[T], out: Path) -> None:
    acct = ACCOUNTS[MERIDIAN]["account_id"]
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = styles["BodyText"].clone("small", fontSize=7.5, leading=9)

    def table(rows, widths):
        wrapped = [[Paragraph(c, small) if i == 0 else c for i, c in enumerate(r)] for r in rows]
        tbl = Table(wrapped, colWidths=widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#1f2a44")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f9")]),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return tbl

    story = []
    for y in YEARS:
        story += [
            Paragraph("<b>MERIDIAN WEALTH</b> &nbsp; Portfolio Statement", styles["Title"]),
            Paragraph(f"Client: {CLIENT}<br/>Account: {acct}<br/>"
                      f"Statement period: 01 Jan {y} to 31 Dec {y}<br/>"
                      "Reporting currency: position currency (no conversion)", body),
            Spacer(1, 4 * mm),
            Paragraph(f"<b>Opening positions as at 31 Dec {y - 1}</b>", body),
            table(_position_rows(ledger, year_end(y - 1), y - 1 if y > YEARS[0] else y),
                  [80 * mm, 28 * mm, 22 * mm, 14 * mm, 30 * mm]),
            Spacer(1, 4 * mm),
            Paragraph(f"<b>Transactions 01 Jan {y} to 31 Dec {y}</b>", body),
        ]
        rows = [["Description", "Date", "Type", "Quantity", "Price", "Ccy", "Amount"]]
        for t in ledger:
            if t.bank != MERIDIAN or t.hidden or t.trade_date.year != y:
                continue
            sign = -1 if t.type in ("BUY", "FEE") else 1
            rows.append([
                SEC[t.isin].names[MERIDIAN] if t.isin else "Management fee",
                t.trade_date.strftime("%d %b %Y"),
                MERIDIAN_LABELS[t.type],
                _qty_text(t.isin, t.quantity) if t.quantity else "",
                num(t.price) if t.price is not None else "",
                t.currency,
                num(sign * t.amount),
            ])
        story += [
            table(rows, [62 * mm, 20 * mm, 22 * mm, 24 * mm, 16 * mm, 10 * mm, 22 * mm]),
            Spacer(1, 4 * mm),
            Paragraph(f"<b>Closing positions as at 31 Dec {y}</b>", body),
            table(_position_rows(ledger, year_end(y), y), [80 * mm, 28 * mm, 22 * mm, 14 * mm, 30 * mm]),
            Spacer(1, 6 * mm),
            Paragraph("SYNTHETIC DATA: fictional bank, client and figures. No client information.", small),
        ]
        if y != YEARS[-1]:
            story.append(PageBreak())

    SimpleDocTemplate(str(out / "meridian_statement.pdf"), pagesize=A4,
                      leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                      title="Meridian Wealth statement (synthetic)").build(story)


# --- Straits: SWIFT MT535 / MT536 style -------------------------------------------------

STRAITS_BIC = "STRTSGSGXXX"


def _swift_header(mt: int, ref: str) -> list[str]:
    return [f"{{1:F01{STRAITS_BIC}0000000000}}{{2:O{mt}LIMFAMHKXXXX}}{{4:", ":16R:GENL",
            f":20C::SEME//{ref}", ":23G:NEWM"]


def _qty_tag(isin: str, qty: Decimal) -> str:
    kind = "FAMT" if SEC[isin].asset_class == "BOND" else "UNIT"
    return f"{kind}/{swift_num(qty)}"


def render_straits(ledger: list[T], out: Path) -> None:
    acct = ACCOUNTS[STRAITS]["account_id"]
    lines: list[str] = []
    for y in YEARS:  # MT535: statement of holdings, one message per year-end
        lines += _swift_header(535, f"SC535-{y}") + [
            f":98A::STAT//{year_end(y):%Y%m%d}", f":97A::SAFE//{acct}", ":16S:GENL", ":16R:SUBSAFE"]
        for isin, qty in sorted(holdings(ledger, STRAITS, year_end(y)).items()):
            s = SEC[isin]
            px = YEAR_END_PRICE[isin][y]
            price_tag = (f":90A::MRKT//PRCT/{swift_num(px)}" if s.asset_class == "BOND"
                         else f":90B::MRKT//ACTU/{s.currency}{swift_num(px)}")
            lines += [":16R:FIN", f":35B:ISIN {isin}", s.names[STRAITS], price_tag,
                      f":93B::AGGR//{_qty_tag(isin, qty)}",
                      f":19A::HOLD//{s.currency}{swift_num(money(qty * px * s.factor))}", ":16S:FIN"]
        lines += [":16S:SUBSAFE", "-}", ""]
    (out / "straits_mt535.txt").write_text("\n".join(lines))

    lines = []
    for y in YEARS:  # MT536: statement of transactions, one message per year
        lines += _swift_header(536, f"SC536-{y}") + [
            f":69A::STAT//{y}0101/{y}1231", f":97A::SAFE//{acct}", ":16S:GENL", ":16R:SUBSAFE"]
        for t in ledger:
            if t.bank != STRAITS or t.hidden or t.trade_date.year != y:
                continue
            s = SEC[t.isin]
            receive = t.type in ("BUY", "TRANSFER_IN")
            free = t.type.startswith("TRANSFER")
            lines += [":16R:FIN", f":35B:ISIN {t.isin}", s.names[STRAITS], ":16R:TRAN", ":16R:TRANSDET",
                      f":36B::PSTA//{_qty_tag(t.isin, t.quantity)}",
                      f":22H::REDE//{'RECE' if receive else 'DELI'}",
                      f":22H::PAYM//{'FREE' if free else 'APMT'}",
                      f":98A::TRAD//{t.trade_date:%Y%m%d}",
                      f":98A::ESET//{t.settle_date:%Y%m%d}"]
            if not free:
                price_tag = (f":90A::DEAL//PRCT/{swift_num(t.price)}" if s.asset_class == "BOND"
                             else f":90B::DEAL//ACTU/{t.currency}{swift_num(t.price)}")
                lines += [price_tag, f":19A::PSTA//{t.currency}{swift_num(t.amount)}"]
            if t.counterparty:
                party = "DEAG" if receive else "REAG"
                lines.append(f":95Q::{party}//{t.counterparty}")
            lines += [":16S:TRANSDET", ":16S:TRAN", ":16S:FIN"]
        lines += [":16S:SUBSAFE", "-}", ""]
    (out / "straits_mt536.txt").write_text("\n".join(lines))


# --- reference data files -----------------------------------------------------------------

def render_reference(out: Path) -> None:
    with open(out / "secmaster.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["isin", "name", "asset_class", "currency", "aliases"])
        for s in SECURITIES:
            known = [n for bank, n in s.names.items() if (bank, s.isin) not in UNKNOWN_ALIASES]
            w.writerow([s.isin, s.name, s.asset_class, s.currency, "|".join(known)])
    with open(out / "fx_rates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["currency", "year", "usd_per_unit"])
        for ccy, by_year in FX.items():
            for y, rate in by_year.items():
                w.writerow([ccy, y, plain(rate)])


# --- ground truth: what the pipeline must produce, computed from the VISIBLE records --------

@dataclass
class Lot:
    acquired: date
    quantity: Decimal
    unit_cost: Decimal | None  # in currency; None = not evidenced
    fx: Decimal | None         # USD per unit on the acquisition date


def fifo(ledger: list[T], carry_transfer_basis: bool) -> tuple[dict, list[dict]]:
    """Replay the visible transactions. Returns (open lots, realized sales)."""
    lots: dict[tuple[str, str], list[Lot]] = defaultdict(list)
    delivered: dict[tuple[str, date, Decimal], list[Lot]] = {}
    sales: list[dict] = []
    visible = [t for t in ledger if not t.hidden and t.type in QTY_SIGN]
    # Deliveries before receipts on the same day, so a receipt can find its match.
    visible.sort(key=lambda t: (t.trade_date, 0 if t.type == "TRANSFER_OUT" else 1))
    for t in visible:
        key = (t.bank, t.isin)
        fx = FX[t.currency][t.trade_date.year]
        if t.type == "BUY":
            lots[key].append(Lot(t.trade_date, t.quantity, t.price * SEC[t.isin].factor, fx))
        elif t.type == "TRANSFER_OUT":
            delivered[(t.isin, t.trade_date, t.quantity)] = take(lots[key], t.quantity)
        elif t.type == "TRANSFER_IN":
            match = delivered.get((t.isin, t.trade_date, t.quantity))
            if match and carry_transfer_basis:
                lots[key].extend(Lot(l.acquired, l.quantity, l.unit_cost, l.fx) for l in match)
            else:
                lots[key].append(Lot(t.trade_date, t.quantity, None, None))
        elif t.type == "SELL":
            used = take(lots[key], t.quantity)
            proceeds = t.quantity * t.price * SEC[t.isin].factor * fx
            cost = sum((l.quantity * l.unit_cost * l.fx for l in used if l.unit_cost is not None), D(0))
            unevidenced = sum((l.quantity for l in used if l.unit_cost is None), D(0))
            sales.append({
                "account_id": ACCOUNTS[t.bank]["account_id"], "bank": t.bank, "isin": t.isin,
                "date": t.trade_date.isoformat(), "quantity": plain(t.quantity),
                "proceeds_usd": money(proceeds), "cost_usd": money(cost),
                "gain_usd": money(proceeds - cost),
                "units_with_unevidenced_basis": plain(unevidenced),
                "lots_used": [{"acquired": l.acquired.isoformat(), "quantity": plain(l.quantity),
                               "unit_cost": plain(l.unit_cost) if l.unit_cost is not None else None}
                              for l in used],
            })
    return lots, sales


def take(lots: list[Lot], qty: Decimal) -> list[Lot]:
    """Remove `qty` units from the front of `lots` (FIFO), return what was taken."""
    taken: list[Lot] = []
    while qty > 0:
        lot = lots[0]
        n = min(qty, lot.quantity)
        taken.append(Lot(lot.acquired, n, lot.unit_cost, lot.fx))
        lot.quantity -= n
        qty -= n
        if lot.quantity == 0:
            lots.pop(0)
    return taken


def payoff(ledger: list[T], carry_transfer_basis: bool) -> dict:
    lots, sales = fifo(ledger, carry_transfer_basis)
    by_account: dict[str, Decimal] = {ACCOUNTS[b]["account_id"]: D(0) for b in ACCOUNTS}
    for s in sales:
        if s["date"].startswith(str(TAX_YEAR)):
            by_account[s["account_id"]] += s["gain_usd"]
    account_by_account = sum((max(g, D(0)) for g in by_account.values()), D(0))
    consolidated = max(sum(by_account.values(), D(0)), D(0))

    unevidenced = []
    for (bank, isin), open_lots in sorted(lots.items()):
        qty = sum((l.quantity for l in open_lots if l.unit_cost is None), D(0))
        if qty:
            s = SEC[isin]
            mv = qty * YEAR_END_PRICE[isin][TAX_YEAR] * s.factor * FX[s.currency][TAX_YEAR]
            unevidenced.append({"account_id": ACCOUNTS[bank]["account_id"], "isin": isin,
                                "quantity": plain(qty), "market_value_usd": money(mv)})
    return {
        "realized_gain_by_account_usd": {k: money(v) for k, v in by_account.items()},
        "taxable_account_by_account_usd": money(account_by_account),
        "taxable_consolidated_usd": money(consolidated),
        "tax_account_by_account_usd": money(account_by_account * TAX_RATE),
        "tax_consolidated_usd": money(consolidated * TAX_RATE),
        "unevidenced_positions": unevidenced,
        "unevidenced_market_value_usd": money(sum((u["market_value_usd"] for u in unevidenced), D(0))),
        "sales": [s for s in sales if s["date"].startswith(str(TAX_YEAR))],
    }


def position_breaks(ledger: list[T]) -> list[dict]:
    """Opening (prior statement) + visible trades vs closing (statement), per year."""
    breaks = []
    for bank in ACCOUNTS:
        for y in YEARS:
            opening = holdings(ledger, bank, year_end(y - 1))
            closing = holdings(ledger, bank, year_end(y))
            moved: dict[str, Decimal] = defaultdict(D)
            for t in ledger:
                if t.bank == bank and not t.hidden and t.trade_date.year == y and t.type in QTY_SIGN:
                    moved[t.isin] += QTY_SIGN[t.type] * t.quantity
            for isin in sorted(set(opening) | set(closing) | set(moved)):
                expected = opening.get(isin, D(0)) + moved.get(isin, D(0))
                actual = closing.get(isin, D(0))
                if expected != actual:
                    breaks.append({"account_id": ACCOUNTS[bank]["account_id"], "isin": isin, "year": y,
                                   "opening": plain(opening.get(isin, D(0))), "net_trades": plain(moved[isin]),
                                   "expected_closing": plain(expected), "statement_closing": plain(actual),
                                   "difference": plain(actual - expected)})
    return breaks


def ground_truth(ledger: list[T]) -> dict:
    before = payoff(ledger, carry_transfer_basis=False)
    after = payoff(ledger, carry_transfer_basis=True)
    final_positions = []
    for bank in ACCOUNTS:
        for isin, qty in sorted(holdings(ledger, bank, year_end(TAX_YEAR)).items()):
            s = SEC[isin]
            mv = qty * YEAR_END_PRICE[isin][TAX_YEAR] * s.factor
            final_positions.append({"account_id": ACCOUNTS[bank]["account_id"], "isin": isin,
                                    "quantity": plain(qty), "currency": s.currency,
                                    "market_value": money(mv),
                                    "market_value_usd": money(mv * FX[s.currency][TAX_YEAR])})
    visible = [t for t in ledger if not t.hidden]
    tencent_lots = [{"acquired": "2023-02-10", "quantity": "300", "unit_cost": "380"},
                    {"acquired": "2023-09-05", "quantity": "100", "unit_cost": "320"}]
    return {
        "_note": "Synthetic. Expected pipeline output, computed from what the files show.",
        "tax_year": TAX_YEAR, "tax_rate": plain(TAX_RATE), "base_currency": "USD",
        "cost_basis_method": "FIFO; unevidenced basis treated as zero (conservative)",
        "counts": {
            "transactions_in_files": len(visible),
            "by_bank": {b: sum(1 for t in visible if t.bank == b) for b in ACCOUNTS},
            "securities": len(SECURITIES),
        },
        "security_names": {s.isin: s.names for s in SECURITIES},
        "expected_exceptions": [
            {"type": "NAME_CONFLICT", "account_id": "MW-7731",
             "security_name": SEC[BOND].names[MERIDIAN], "resolves_to": BOND,
             "why": "full legal name not in the securities master; coupon 4.25 and maturity 2030 match"},
            {"type": "BASIS_MISSING", "account_id": "SC-4420", "isin": TCEHY, "date": "2024-06-14",
             "quantity": "400", "matching_transfer_out": {"account_id": "HPB-001", "date": "2024-06-14",
                                                          "quantity": "400"},
             "proposed_lots_hkd": tencent_lots, "proposed_basis_hkd": "146000"},
            {"type": "UNMATCHED_TRANSFER", "account_id": "SC-4420", "isin": STEL, "date": "2024-11-05",
             "quantity": "2000", "counterparty": "KESTREL SECURITIES SG"},
            {"type": "POSITION_BREAK", **position_breaks(ledger)[0]},
        ],
        "positions_2025_12_31": final_positions,
        "bond_total_face": plain(sum(holdings(ledger, b, year_end(TAX_YEAR)).get(BOND, D(0)) for b in ACCOUNTS)),
        "payoff_before_approval": before,
        "payoff_after_basis_approval": after,
    }


def check_by_hand(gt: dict, ledger: list[T]) -> None:
    """The numbers worked out on paper. If the generator drifts, this fails loudly."""
    b, a = gt["payoff_before_approval"], gt["payoff_after_basis_approval"]
    # Harbour: AAPL 100 x (205 - 150)                                   = 5,500
    # Meridian: ASML 40 x (610 x 1.12 - 780 x 1.09) = 40 x -167.00       = -6,680
    # Straits: DBS 400 x (42 x 0.77 - 33 x 0.745) = 12,936 - 9,834      = 3,102
    #          Tencent 250 x 520 x 0.128 = 16,640 proceeds
    #            basis unevidenced -> gain 16,640
    #            basis carried (300 @ 380) -> 250 x 140 x 0.128 = 4,480
    assert a["realized_gain_by_account_usd"] == {"HPB-001": D("5500.00"), "MW-7731": D("-6680.00"),
                                                 "SC-4420": D("7582.00")}, a["realized_gain_by_account_usd"]
    assert b["realized_gain_by_account_usd"]["SC-4420"] == D("19742.00")
    assert (b["taxable_account_by_account_usd"], b["taxable_consolidated_usd"]) == (D("25242.00"), D("18562.00"))
    assert (a["taxable_account_by_account_usd"], a["taxable_consolidated_usd"]) == (D("13082.00"), D("6402.00"))
    # Unevidenced at 2025-12-31: Tencent 150 x 560 x 0.128 = 10,752; Singtel 2,000 x 4.30 x 0.77 = 6,622
    assert b["unevidenced_market_value_usd"] == D("17374.00")
    assert a["unevidenced_market_value_usd"] == D("6622.00")
    breaks = position_breaks(ledger)
    assert len(breaks) == 1 and breaks[0]["isin"] == ASML and breaks[0]["difference"] == "-10", breaks
    assert gt["bond_total_face"] == "350000"


def main() -> None:
    out = GENERATED_DIR
    out.mkdir(parents=True, exist_ok=True)
    ledger = build_ledger()
    render_reference(out)
    render_harbour(ledger, out)
    render_meridian(ledger, out)
    render_straits(ledger, out)
    gt = ground_truth(ledger)
    check_by_hand(gt, ledger)
    (out / "ground_truth.json").write_text(json.dumps(gt, indent=2, default=str) + "\n")

    print(f"wrote {len(list(out.iterdir()))} files to {out}")
    print(f"transactions in files: {gt['counts']['transactions_in_files']} {gt['counts']['by_bank']}")
    for label, p in (("before approval", gt["payoff_before_approval"]),
                     ("after basis approval", gt["payoff_after_basis_approval"])):
        print(f"{label:>22}: account-by-account {p['taxable_account_by_account_usd']:>10,}  "
              f"consolidated {p['taxable_consolidated_usd']:>10,}  "
              f"unevidenced MV {p['unevidenced_market_value_usd']:>10,}")


if __name__ == "__main__":
    main()
