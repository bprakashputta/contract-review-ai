"""The clause categories this service extracts: 12 of CUAD v1's 41, chosen for the
questions a small legal/ops team asks first about a commercial contract (can we exit,
does it renew, how exposed are we, what are we giving up).

``keywords`` feed the lexical prefilter (regexes over lowercased text). They were tuned
against the CUAD *train* split only (``scripts/prefilter_recall.py``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    cuad_name: str
    label: str
    description: str
    keywords: tuple[str, ...]
    guidance: str = ""  # boundary notes added in extract.v2 after dev-split error analysis


CATEGORIES: tuple[Category, ...] = (
    Category(
        "governing_law",
        "Governing Law",
        "Governing law",
        "Which state's or country's law governs the interpretation of the contract.",
        (r"govern(ed|ing)", r"laws? of", r"construed", r"jurisdiction"),
        guidance="Quote the choice-of-law sentence, not the forum/venue sentence.",
    ),
    Category(
        "renewal_term",
        "Renewal Term",
        "Auto-renewal",
        "A renewal term after the initial term that starts automatically unless a party "
        "gives notice (evergreen / automatic renewal), including its length.",
        (
            r"renew",
            r"automatic(ally)?",
            r"successive",
            r"extend(ed)? for",
            r"evergreen",
        ),
        guidance="Count automatic renewals and renewals one party can trigger alone. A renewal that needs both parties to agree is NOT a renewal term.",
    ),
    Category(
        "notice_to_terminate_renewal",
        "Notice Period To Terminate Renewal",
        "Notice to stop renewal",
        "The notice period required to terminate or prevent renewal of the contract "
        "(e.g. 'unless either party gives 90 days' notice of non-renewal').",
        (
            r"renew",
            r"notice of (non-?renewal|termination)",
            r"prior to the (end|expiration)",
            r"days'? (prior )?(written )?notice",
        ),
        guidance="Only a notice period tied to stopping a renewal or extension (non-renewal notice). A general notice period for termination is NOT this category. If there is no renewal, this is not found.",
    ),
    Category(
        "termination_for_convenience",
        "Termination For Convenience",
        "Termination for convenience",
        "A party can terminate the contract without cause (solely by giving notice and "
        "allowing a waiting period to expire).",
        (
            r"terminat",
            r"for convenience",
            r"without cause",
            r"for any reason",
            r"at any time",
        ),
        guidance="Includes a right to terminate 'at any time' or 'for any reason' on notice, even for part of the contract. A clause saying the contract may NOT be terminated for convenience is not found.",
    ),
    Category(
        "cap_on_liability",
        "Cap On Liability",
        "Liability cap",
        "A cap on liability upon breach: a limit on the amount or time to bring claims, "
        "or an exclusion of certain damages (e.g. consequential, punitive).",
        (
            r"liabilit",
            r"in no event",
            r"shall not exceed",
            r"consequential",
            r"aggregate",
            r"limitation of",
            r"sole (and exclusive )?remedy",
            r"maximum",
        ),
        guidance="Includes exclusions of consequential/indirect/punitive damages, caps on amount, and time limits to bring claims.",
    ),
    Category(
        "uncapped_liability",
        "Uncapped Liability",
        "Uncapped liability",
        "A party's liability is uncapped upon breach: carve-outs from the limitation of "
        "liability (e.g. for indemnification, gross negligence, confidentiality breach).",
        (
            r"liabilit",
            r"shall not apply",
            r"except (for|with respect)",
            r"gross negligence",
            r"willful",
            r"indemnif",
            r"unlimited",
        ),
        guidance="Only where liability is expressly unlimited, or a carve-out says the limitation or exclusion of liability does not apply to certain claims. An ordinary indemnification clause on its own is NOT uncapped liability.",
    ),
    Category(
        "non_compete",
        "Non-Compete",
        "Non-compete",
        "A restriction on a party's ability to compete with the counterparty or operate "
        "in a certain geography, business or technology sector.",
        (
            r"compet",
            r"shall not (engage|directly|indirectly)",
            r"similar (product|business)",
            r"restrict",
            r"(agrees?|shall|will) not (to )?(open|engage|enter into|serve|offer)",
        ),
        guidance="Includes any promise not to deal with, promote, advertise for, or sell competing products or competitors, during or after the term.",
    ),
    Category(
        "exclusivity",
        "Exclusivity",
        "Exclusivity",
        "An exclusive dealing commitment: a party must buy/supply only from/to the "
        "counterparty, or is granted exclusive rights in a territory or channel.",
        (
            r"exclusiv",
            r"sole (and exclusive )?(distributor|supplier|provider|source)",
            r"only from",
            r"shall not (appoint|sell|purchase)",
        ),
        guidance="Includes exclusive licenses, exclusive appointments, requirements commitments and prohibitions on working with third parties. A 'sole right to enforce' or a mutual-consent clause is not exclusivity.",
    ),
    Category(
        "ip_ownership_assignment",
        "Ip Ownership Assignment",
        "IP assignment",
        "Intellectual property created by one party becomes the property of the "
        "counterparty (assignment of IP, work made for hire).",
        (
            r"intellectual property",
            r"assign",
            r"work(s)? (made )?for hire",
            r"own(ership|ed)?",
            r"title",
            r"proprietary rights",
            r"inventions?",
        ),
        guidance="Only IP created or developed by one party that is owned by or assigned to the OTHER party. A party keeping its own pre-existing IP, or a trademark remaining the licensor's property, is NOT an assignment.",
    ),
    Category(
        "change_of_control",
        "Change Of Control",
        "Change of control",
        "A party's rights on a change of control of the counterparty (merger, acquisition, "
        "sale of substantially all assets), e.g. a right to terminate or consent.",
        (
            r"change (of|in) control",
            r"merger",
            r"acqui",
            r"substantially all",
            r"controlling interest",
            r"reorgani",
        ),
        guidance="Rights to terminate, or consent/notice required, when a party undergoes a change of control, merger, sale of substantially all assets, or assignment by operation of law.",
    ),
    Category(
        "anti_assignment",
        "Anti-Assignment",
        "Anti-assignment",
        "Consent or notice is required of a party if the contract is assigned to a third party.",
        (r"assign", r"transfer", r"prior written consent", r"delegat"),
        guidance="Consent or notice required to assign or transfer the contract.",
    ),
    Category(
        "audit_rights",
        "Audit Rights",
        "Audit rights",
        "A party's right to audit the books, records or physical locations of the "
        "counterparty to ensure compliance with the contract.",
        (r"audit", r"inspect", r"books and records", r"records", r"examin"),
        guidance="Rights to audit or inspect books, records, facilities or compliance, including by an independent accountant.",
    ),
)

BY_KEY: dict[str, Category] = {c.key: c for c in CATEGORIES}
BY_CUAD: dict[str, Category] = {c.cuad_name: c for c in CATEGORIES}
KEYS: tuple[str, ...] = tuple(c.key for c in CATEGORIES)
