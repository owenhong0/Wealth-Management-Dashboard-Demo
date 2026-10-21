"""Securities master: map every bank's name for a security to one ISIN.

Resolution order for each record:
  1. The bank gave an ISIN we know            -> use it
  2. The bank's name matches a known alias     -> that alias's ISIN
  3. A human approved a mapping for this name  -> that ISIN (NAME_CONFLICT approvals)
  4. Otherwise                                 -> unresolved; reconcile raises NAME_CONFLICT

Matching is exact on a normalized name (case, spacing and punctuation ignored).
Nothing fuzzy is ever applied automatically: `suggest()` only proposes a match
for a human to approve.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import GENERATED_DIR
from pipeline.schema import AssetClass, Security, TxnType

SECMASTER_FILE = "secmaster.csv"

_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_STOPWORDS = {"LTD", "LIMITED", "INC", "CORP", "PLC", "NV", "SE", "THE", "OF", "DUE", "COM", "HLDGS",
              "HOLDINGS", "GROUP", "NOTES", "SENIOR"}


def norm(name: str) -> str:
    """'ASML Holding N.V.' -> 'ASML HOLDING NV'"""
    return " ".join(_PUNCT.sub("", name.upper().replace("&", " AND ")).split())


def _words(name: str) -> set[str]:
    """Distinctive words only: no stopwords, no numbers (numbers are scored separately)."""
    return {w for w in norm(name).split() if w not in _STOPWORDS and not w.isdigit()}


def load_securities(path: Path) -> dict[str, Security]:
    out: dict[str, Security] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out[row["isin"]] = Security(
                isin=row["isin"],
                name=row["name"],
                asset_class=AssetClass(row["asset_class"]),
                currency=row["currency"],
                aliases=tuple(a for a in row["aliases"].split("|") if a),
            )
    return out


@dataclass(frozen=True)
class Suggestion:
    isin: str
    score: float
    reasons: tuple[str, ...]


class SecMaster:
    def __init__(self, securities: dict[str, Security], approved_aliases: dict[str, str] | None = None):
        self.securities = securities
        self._by_alias: dict[str, str] = {}
        for s in securities.values():
            for name in (s.name, *s.aliases):
                self._by_alias[norm(name)] = s.isin
        self._approved = {norm(name): isin for name, isin in (approved_aliases or {}).items()}

    @classmethod
    def load(cls, folder: Path = GENERATED_DIR, approved_aliases: dict[str, str] | None = None) -> "SecMaster":
        return cls(load_securities(folder / SECMASTER_FILE), approved_aliases)

    def resolve(self, name: str, isin: str | None = None) -> tuple[str | None, str]:
        """Returns (isin or None, how it was resolved)."""
        if isin and isin in self.securities:
            return isin, "isin"
        key = norm(name)
        if key in self._by_alias:
            return self._by_alias[key], "alias"
        if key in self._approved:
            return self._approved[key], "approved"
        return None, "unresolved"

    def suggest(self, name: str) -> Suggestion | None:
        """Best candidate for an unresolved name, for a human to approve.

        Scores two kinds of evidence:
        - numbers that appear in both names (a bond's coupon and maturity year)
        - distinctive words shared with the security's name or aliases
        """
        numbers = set(_NUMBER.findall(name.replace(",", "")))
        numbers |= {f"{float(n):g}" for n in numbers if "." in n}  # '4.250' also matches '4.25'
        words = _words(name)
        best: Suggestion | None = None
        for s in self.securities.values():
            known = " ".join((s.name, *s.aliases))
            known_numbers = set(_NUMBER.findall(known)) | {f"{float(n):g}" for n in _NUMBER.findall(known)}
            known_words = _words(known)
            shared_numbers = sorted(numbers & known_numbers)
            shared_words = sorted(words & known_words)
            score = 0.25 * len(shared_numbers) + 0.15 * len(shared_words)
            if s.asset_class is AssetClass.BOND and len(shared_numbers) >= 2:
                score += 0.2  # coupon + maturity both match
            score = min(score, 0.95)  # never certain: a human decides
            if score > 0 and (best is None or score > best.score):
                reasons = []
                if shared_numbers:
                    reasons.append("numbers match: " + ", ".join(shared_numbers))
                if shared_words:
                    reasons.append("words match: " + ", ".join(shared_words))
                best = Suggestion(s.isin, round(score, 2), tuple(reasons))
        return best


def resolve_securities(event: dict[str, Any]) -> dict[str, Any]:
    """Pipeline step: set `isin` on every transaction and snapshot we can resolve.

    Unresolved names are grouped (one entry per bank + name) so reconcile can
    raise one NAME_CONFLICT per name, citing every record that uses it.
    """
    folder = Path(event.get("generated_dir", GENERATED_DIR))
    sm = SecMaster.load(folder, event.get("approved_aliases"))
    event["secmaster"] = sm

    unresolved: dict[tuple[str, str, str], list] = defaultdict(list)
    how_counts: dict[str, int] = defaultdict(int)
    for key in ("transactions", "snapshots"):
        resolved = []
        for r in event.get(key, []):
            if key == "transactions" and r.type is TxnType.FEE:
                resolved.append(r)  # fees are cash-only: no security to resolve
                continue
            isin, how = sm.resolve(r.security_name, r.isin)
            how_counts[how] += 1
            if isin is None:
                unresolved[(r.source.bank, r.account_id, r.security_name)].append(r)
            resolved.append(r.model_copy(update={"isin": isin}) if isin != r.isin else r)
        event[key] = resolved

    event["unresolved_names"] = [
        {"bank": bank, "account_id": account, "security_name": name, "records": records,
         "suggestion": sm.suggest(name)}
        for (bank, account, name), records in unresolved.items()
    ]
    event["count_resolved_by"] = dict(how_counts)
    event["count_unresolved_names"] = len(unresolved)
    return event