"""Playbook risk rules, evaluated in code over verified clauses.

The model never decides risk. It finds and quotes clauses; deterministic rules decide
what to flag, and numeric facts (notice days, jurisdiction) are parsed from the verified
quote text by code, not taken from the model."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

from .categories import KEYS
from .models import Clause, RiskFlag, Severity


class _Rule(BaseModel):
    id: str
    category: str
    severity: Severity
    message: str


class RequiredRule(_Rule):
    type: Literal["required"]


class ForbiddenRule(_Rule):
    type: Literal["forbidden"]


class MinNoticeRule(_Rule):
    type: Literal["min_notice_days"]
    notice_category: str
    min_days: int
    missing_message: str = ""


class AllowedValuesRule(_Rule):
    type: Literal["allowed_values"]
    allowed: list[str]


Rule = Annotated[
    RequiredRule | ForbiddenRule | MinNoticeRule | AllowedValuesRule,
    Field(discriminator="type"),
]


class Playbook(BaseModel):
    name: str
    version: int = 1
    rules: list[Rule]

    def model_post_init(self, _: object) -> None:
        for r in self.rules:
            cats = [r.category] + ([r.notice_category] if isinstance(r, MinNoticeRule) else [])
            for c in cats:
                if c not in KEYS:
                    raise ValueError(f"rule {r.id}: unknown category {c!r}")


def load_playbook(path: Path) -> Playbook:
    return Playbook.model_validate(yaml.safe_load(path.read_text()))


# ---- fact parsers over verified quote text ----

_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty-five": 45,
    "forty five": 45,
    "forty": 40,
    "sixty": 60,
    "seventy-five": 75,
    "ninety": 90,
    "one hundred twenty": 120,
    "one hundred eighty": 180,
    "one hundred and eighty": 180,
    "one hundred and twenty": 120,
}
_UNIT_DAYS = {
    "day": 1,
    "business day": 1,
    "calendar day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}
_PERIOD = re.compile(
    r"(?P<num>\d{1,3}|"
    + "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True))
    + r")(?:\s*\(\s*\d{1,3}\s*\))?\s*(?P<unit>business day|calendar day|day|week|month|year)s?",
    re.I,
)


def notice_days(text: str) -> list[int]:
    """All periods mentioned in a quote, converted to days ("ninety (90) days" -> 90)."""
    out = []
    for m in _PERIOD.finditer(text):
        num = m.group("num").lower()
        n = int(num) if num.isdigit() else _NUM_WORDS[num]
        out.append(n * _UNIT_DAYS[m.group("unit").lower()])
    return out


_JURISDICTIONS = [
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "Wisconsin",
    "Wyoming",
    "Ontario",
    "Quebec",
    "Alberta",
    "Germany",
    "France",
    "Switzerland",
    "Singapore",
    "Israel",
    "Japan",
    "Ireland",
    "Netherlands",
    "Sweden",
] + [
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "West Virginia",
    "District of Columbia",
    "British Columbia",
    "England",
    "Hong Kong",
    "People's Republic of China",
    "India",
]


def jurisdictions(text: str) -> list[str]:
    found = []
    for j in sorted(_JURISDICTIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(j)}\b", text) and not any(j in f for f in found):
            found.append(j)
    if "Virginia" in found and "West Virginia" in found:
        found.remove("Virginia")
    return found


def evaluate(playbook: Playbook, clauses: list[Clause]) -> list[RiskFlag]:
    by = {c.category: c for c in clauses}
    flags: list[RiskFlag] = []
    for r in playbook.rules:
        c = by.get(r.category)
        found = bool(c and c.found)
        if isinstance(r, RequiredRule) and not found:
            flags.append(
                RiskFlag(
                    rule_id=r.id,
                    severity=r.severity,
                    category=r.category,
                    message=r.message,
                )
            )
        elif isinstance(r, ForbiddenRule) and found:
            flags.append(
                RiskFlag(
                    rule_id=r.id,
                    severity=r.severity,
                    category=r.category,
                    message=r.message,
                    evidence=c.quotes,
                )
            )
        elif isinstance(r, MinNoticeRule) and found:
            n = by.get(r.notice_category)
            if not (n and n.found):
                flags.append(
                    RiskFlag(
                        rule_id=r.id,
                        severity=r.severity,
                        category=r.category,
                        message=r.missing_message or r.message,
                        evidence=c.quotes,
                    )
                )
                continue
            days = [d for q in n.quotes for d in notice_days(q.text)]
            if days and min(days) < r.min_days:
                flags.append(
                    RiskFlag(
                        rule_id=r.id,
                        severity=r.severity,
                        category=r.notice_category,
                        message=f"{r.message} Found {min(days)} days.",
                        evidence=n.quotes,
                    )
                )
        elif isinstance(r, AllowedValuesRule) and found:
            js = [j for q in c.quotes for j in jurisdictions(q.text)]
            bad = [j for j in dict.fromkeys(js) if j not in r.allowed]
            if bad and not any(j in r.allowed for j in js):
                flags.append(
                    RiskFlag(
                        rule_id=r.id,
                        severity=r.severity,
                        category=r.category,
                        message=f"{r.message} Found: {', '.join(bad)}.",
                        evidence=c.quotes,
                    )
                )
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(flags, key=lambda f: order[f.severity])
