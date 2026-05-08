#!/usr/bin/env python3
"""
Control Language Analyzer (5W2H)
--------------------------------
A lightweight, rule-based Python utility that reviews a control description and
flags gaps or vagueness in the "Who, What, When, Where, Why, How, and How Often"
(5W2H) elements. It also suggests refined, audit-ready language.

Why this exists
---------------
Many control narratives are missing key specificity (e.g., "periodically",
"management reviews"), making design and operating effectiveness hard to test.
This script provides fast, explainable checks suited to ITGC and business
process controls.

Usage
-----
1) As a library:
   >>> from control_language_analyzer import analyze_control, suggest_refinement
   >>> result = analyze_control("Management periodically reviews user access.")
   >>> print(result["summary"])  # high-level issues
   >>> print(suggest_refinement("Management periodically reviews user access."))

2) From CLI (analyze a file with one control per line):
   $ python control_language_analyzer.py --in controls.txt --out findings.json

Outputs
-------
- Element-by-element findings (present/missing/needs_refinement, rationale)
- A refined control statement suggestion that fills in gaps with placeholders
  you can tailor to your environment (e.g., [System Name], [Role/Title]).

Note
----
This is intentionally rule-based (no heavyweight NLP dependencies) so it runs
anywhere. It uses pragmatic heuristics and curated keyword lists. Adjust the
keyword lists in CONFIG below to match your org's terminology.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional


# ----------------------------- CONFIG -------------------------------------- #

GENERIC_ROLE_WORDS = {
    "management", "team", "staff", "personnel", "employees", "operator",
    "it", "finance", "accounting", "security", "business", "operations",
}

ROLE_HINTS = {
    # Add more roles/titles used in your org as needed
    "control owner", "process owner", "manager", "senior manager",
    "director", "vp", "cfo", "controller", "analyst", "lead",
    "supervisor", "compliance", "audit", "security analyst",
    "iam analyst", "system administrator", "it administrator",
    "service owner", "product owner", "application owner",
    "sre", "devops", "database administrator", "dba",
}

# Action verbs typical for controls (WHAT)
ACTION_VERBS = {
    "review", "approve", "reconcile", "compare", "monitor", "grant",
    "revoke", "remove", "add", "validate", "investigate", "escalate",
    "log", "restrict", "configure", "test", "scan", "backup",
    "restore", "certify", "attest", "segregate", "match",
}

# Strong purpose verbs (WHY)
PURPOSE_VERBS = {
    "ensure", "verify", "confirm", "detect", "prevent", "mitigate",
    "comply", "maintain", "safeguard",
}

# Systems / locations (WHERE) — extend for your landscape
SYSTEM_HINTS = {
    "sap", "oracle", "workday", "servicenow", "sailpoint", "okta",
    "active directory", "ad", "azure ad", "powerbi", "tableau",
    "general ledger", "g/l", "subledger", "erp", "hris", "ticketing",
    "gl", "snowflake", "bigquery", "databricks", "postgres", "sql",
}

# Frequency and timing (HOW OFTEN / WHEN)
FREQUENCY_PATTERN = re.compile(
    r"\b(daily|weekly|biweekly|fortnightly|monthly|quarterly|semi[- ]?annual|"
    r"annually|yearly|every\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(hour|hours|day|days|week|weeks|month|months|quarter|quarters|year|years)s?)\b",
    re.IGNORECASE,
)

TIMING_TRIGGER_PATTERN = re.compile(
    r"\b(upon|within\s+\d+\s+(business\s+)?(day|days|hour|hours|week|weeks)|"
    r"at\s+(month|quarter|year)[- ]?end|before|after|on\s+receipt\s+of|when)\b",
    re.IGNORECASE,
)

# Vague words/phrases to flag as needing refinement
VAGUE_TERMS = {
    "periodic", "periodically", "regular", "regularly", "timely",
    "as needed", "as-needed", "appropriate", "appropriately", "sufficient",
    "reasonable", "adequate", "from time to time", "ongoing",
}

# Evidence cues (helpful for HOW)
EVIDENCE_HINTS = {
    "evidence", "documentation", "ticket", "jira", "servicenow", "screenshot",
    "report", "log", "attestation", "email", "approval", "workflow",
}

# ----------------------------- DATA TYPES ---------------------------------- #

@dataclass
class ElementAssessment:
    element: str
    status: str  # "present" | "missing" | "needs_refinement"
    rationale: str
    snippets: List[str]


@dataclass
class Analysis:
    who: ElementAssessment
    what: ElementAssessment
    when: ElementAssessment
    where: ElementAssessment
    why: ElementAssessment
    how: ElementAssessment
    frequency: ElementAssessment
    summary: List[str]
    refined_suggestion: str


# ----------------------------- HELPERS ------------------------------------- #

def _find_any(text: str, keywords: List[str] | set[str]) -> List[str]:
    hits = []
    for k in keywords:
        if re.search(rf"\b{re.escape(k)}\b", text, flags=re.IGNORECASE):
            hits.append(k)
    return hits


def _contains_vague(text: str) -> List[str]:
    return _find_any(text, VAGUE_TERMS)


def _who_assess(text: str) -> ElementAssessment:
    role_hits = _find_any(text, ROLE_HINTS)
    generic_hits = _find_any(text, GENERIC_ROLE_WORDS)

    if role_hits:
        status = "present"
        rationale = f"Specific role(s) referenced: {', '.join(sorted(set(role_hits)))}."
        # If generic terms also appear without a clear performer, nudge refinement
        if generic_hits:
            status = "needs_refinement"
            rationale += f" Also found generic term(s): {', '.join(sorted(set(generic_hits)))}. Prefer a specific performer."
        return ElementAssessment("Who", status, rationale, role_hits + generic_hits)

    if generic_hits:
        return ElementAssessment(
            "Who",
            "needs_refinement",
            f"Only generic performer(s) referenced: {', '.join(sorted(set(generic_hits)))}. Name a specific role/title.",
            generic_hits,
        )

    return ElementAssessment("Who", "missing", "No performer identified.", [])


def _what_assess(text: str) -> ElementAssessment:
    verb_hits = _find_any(text, ACTION_VERBS)
    if verb_hits:
        # If only very high-level verbs, nudge refinement
        high_level = {v for v in verb_hits if v in {"monitor", "ensure", "maintain"}}
        if high_level and len(verb_hits) == len(high_level):
            return ElementAssessment(
                "What",
                "needs_refinement",
                f"Only high-level action(s) found: {', '.join(sorted(high_level))}. Specify concrete steps (e.g., compare X vs Y, investigate variances).",
                verb_hits,
            )
        return ElementAssessment(
            "What", "present", f"Action verb(s) found: {', '.join(sorted(set(verb_hits)))}.", verb_hits
        )
    return ElementAssessment("What", "missing", "No clear control action found.", [])


def _where_assess(text: str) -> ElementAssessment:
    system_hits = _find_any(text, SYSTEM_HINTS)
    # Generic placeholders
    generic = []
    for pat in [r"\bin\s+the\s+system\b", r"\bin\s+system\b", r"\bin\s+tool\b", r"\busing\s+a?\s*report\b", r"\breport\b"]:
        generic.extend(re.findall(pat, text, flags=re.IGNORECASE))
    if system_hits:
        status = "present"
        rationale = f"System/location referenced: {', '.join(sorted(set(system_hits)))}."
        if generic:
            status = "needs_refinement"
            rationale += " Also contains generic terms (e.g., 'system', 'report'). Name the exact system/report ID."
        return ElementAssessment("Where", status, rationale, system_hits)
    if generic:
        return ElementAssessment(
            "Where", "needs_refinement", "Generic location (system/report) referenced. Name the specific system or report.", generic
        )
    return ElementAssessment("Where", "missing", "No system/location referenced.", [])


def _when_and_frequency_assess(text: str) -> Tuple[ElementAssessment, ElementAssessment]:
    freq_hits = [m.group(0) for m in FREQUENCY_PATTERN.finditer(text)]
    trigger_hits = [m.group(0) for m in TIMING_TRIGGER_PATTERN.finditer(text)]
    vague = _contains_vague(text)

    # Frequency element
    if freq_hits:
        freq_status = "present"
        freq_rationale = f"Frequency specified: {', '.join(sorted(set(freq_hits)))}."
        if vague:
            freq_status = "needs_refinement"
            freq_rationale += f" Also found vague term(s): {', '.join(sorted(set(vague)))}. Replace with measurable timing."
        freq_assess = ElementAssessment("How Often", freq_status, freq_rationale, freq_hits + vague)
    elif vague:
        freq_assess = ElementAssessment(
            "How Often",
            "needs_refinement",
            f"Vague timing term(s) used: {', '.join(sorted(set(vague)))}. Specify a measurable frequency.",
            vague,
        )
    else:
        freq_assess = ElementAssessment("How Often", "missing", "No frequency specified.", [])

    # When element (triggers and deadlines)
    if trigger_hits:
        when_status = "present"
        when_rationale = f"Trigger/deadline specified: {', '.join(sorted(set(trigger_hits)))}."
        if vague and not freq_hits:
            when_status = "needs_refinement"
            when_rationale += f" But uses vague timing term(s): {', '.join(sorted(set(vague)))}."
        when_assess = ElementAssessment("When", when_status, when_rationale, trigger_hits + vague)
    elif vague:
        when_assess = ElementAssessment(
            "When",
            "needs_refinement",
            f"Vague timing term(s) used: {', '.join(sorted(set(vague)))}. Replace with concrete trigger/deadline.",
            vague,
        )
    else:
        when_assess = ElementAssessment("When", "missing", "No timing trigger or deadline specified.", [])

    return when_assess, freq_assess


def _why_assess(text: str) -> ElementAssessment:
    # Look for purpose starting with "to <verb> ..."
    why_match = re.search(r"\bto\s+(ensure|verify|confirm|detect|prevent|mitigate|comply|maintain|safeguard)\b", text, re.IGNORECASE)
    if why_match:
        verb = why_match.group(1)
        return ElementAssessment("Why", "present", f"Purpose verb found: {verb}.", [verb])
    # Or explicit risk/purpose words
    if _find_any(text, PURPOSE_VERBS):
        hits = _find_any(text, PURPOSE_VERBS)
        return ElementAssessment("Why", "present", f"Purpose cue(s) found: {', '.join(sorted(set(hits)))}.", hits)
    return ElementAssessment("Why", "missing", "No clear purpose/risk mitigation stated.", [])


def _how_assess(text: str) -> ElementAssessment:
    cues = []
    for pat in [r"\bby\s+\w+ing\b", r"\bthrough\b", r"\busing\b", r"\bvia\b", r"\bcompare\b", r"\breconcile\b", r"\breview\s+of\b"]:
        cues.extend([m.group(0) for m in re.finditer(pat, text, re.IGNORECASE)])
    evidence = _find_any(text, EVIDENCE_HINTS)

    if cues or evidence:
        status = "present"
        rationale = "Methodology/evidence cues present."
        # Nudge refinement if only generic cues
        if not evidence and any(t in text.lower() for t in ["using a report", "review of report", "review of logs", "by reviewing"]):
            status = "needs_refinement"
            rationale = "Method stated but generic. Specify report name/ID, fields, thresholds, and evidence to retain."
        return ElementAssessment("How", status, rationale, list(set(cues + evidence)))

    return ElementAssessment("How", "missing", "No clear method or evidence described.", [])


# ----------------------------- SUGGESTION ---------------------------------- #

_TEMPLATE = (
    "On a {frequency}, the {who} {what} {object} in {where} by {how}, to {why}. "
    "Evidence retained: {evidence}. Exceptions are investigated and resolved within {sla} by the {who2}."
)

DEFAULT_FILLERS = {
    "frequency": "monthly basis",
    "who": "[Role/Title] (e.g., Access Administration Manager)",
    "what": "reviews and approves",
    "object": "[object of the control] (e.g., user access changes for in-scope applications)",
    "where": "[System/Report Name] (e.g., Okta Access Change Report #ACR-001)",
    "how": "comparing the request/ticket to authorized approvals and verifying least-privilege; variances > [Threshold]% are investigated",
    "why": "mitigate the risk of unauthorized access and ensure compliance with the Access Management Policy",
    "evidence": "approval record (ticket/workflow ID), system report, and reviewer sign-off with date",
    "sla": "3 business days",
    "who2": "[Reviewer Title]",
}


def _first_or_default(hits: List[str], default: str) -> str:
    return hits[0] if hits else default


def _build_suggestion(text: str, assessments: Dict[str, ElementAssessment]) -> str:
    t = text.lower()

    # WHO
    who_hits = _find_any(t, ROLE_HINTS)
    who = _first_or_default(who_hits, DEFAULT_FILLERS["who"])

    # WHAT
    what_hits = _find_any(t, ACTION_VERBS)
    # Prefer more concrete verbs if present
    concrete_priority = [
        "reconcile", "compare", "validate", "approve", "review", "monitor", "grant", "revoke"
    ]
    what = DEFAULT_FILLERS["what"]
    for v in concrete_priority:
        if v in what_hits:
            what = {
                "reconcile": "reconciles",
                "compare": "compares",
                "validate": "validates",
                "approve": "approves",
                "review": "reviews",
                "monitor": "monitors",
                "grant": "grants",
                "revoke": "revokes",
            }[v]
            break

    # WHERE
    where_hits = _find_any(t, SYSTEM_HINTS)
    where = _first_or_default(where_hits, DEFAULT_FILLERS["where"])

    # FREQUENCY / WHEN
    freq_match = FREQUENCY_PATTERN.search(t)
    frequency = freq_match.group(0) + " basis" if freq_match else DEFAULT_FILLERS["frequency"]

    # WHY
    why = DEFAULT_FILLERS["why"]
    why_m = re.search(r"to\s+([a-z ]{3,120})", t)
    if why_m:
        frag = why_m.group(1)
        # Trim trailing junk
        frag = re.split(r"[\.;]", frag)[0]
        why = frag.strip()
        if not any(why.startswith(v) for v in PURPOSE_VERBS):
            why = "mitigate risk by " + why

    # HOW
    if assessments["How"].status == "present":
        how = "provide detailed steps per procedure"  # nudge to customize
        # Try grab a simple 'by <verb>ing ...' phrase if present
        by_m = re.search(r"by\s+([a-z0-9 ,\-/()]{5,140})", t)
        if by_m:
            how = by_m.group(1).strip().rstrip(".,;")
    else:
        how = DEFAULT_FILLERS["how"]

    # OBJECT (loosely infer from common prepositions)
    obj = DEFAULT_FILLERS["object"]
    of_m = re.search(r"(of|for|to)\s+([a-z0-9 /\-_,]{5,120})", t)
    if of_m:
        candidate = of_m.group(2)
        candidate = re.split(r"[\.;]", candidate)[0].strip()
        if len(candidate) > 4:
            obj = candidate

    suggestion = _TEMPLATE.format(
        frequency=frequency,
        who=who,
        what=what,
        object=obj,
        where=where,
        how=how,
        why=why,
        evidence=DEFAULT_FILLERS["evidence"],
        sla=DEFAULT_FILLERS["sla"],
        who2=DEFAULT_FILLERS["who2"],
    )

    # Clean capitalization around placeholders
    suggestion = re.sub(r"\s+\)", ")", suggestion)
    return suggestion


# ----------------------------- MAIN LOGIC ---------------------------------- #

def analyze_control(text: str) -> Analysis:
    """Analyze a single control description and return detailed findings + suggestion."""
    if not text or not text.strip():
        raise ValueError("Control text is empty.")

    t = " ".join(text.split())  # collapse whitespace

    who = _who_assess(t)
    what = _what_assess(t)
    when, frequency = _when_and_frequency_assess(t)
    where = _where_assess(t)
    why = _why_assess(t)
    how = _how_assess(t)

    assessments = {a.element: a for a in [who, what, when, where, why, how, frequency]}

    issues = []
    for a in assessments.values():
        if a.status in {"missing", "needs_refinement"}:
            issues.append(f"{a.element}: {a.status} — {a.rationale}")

    suggestion = _build_suggestion(t, assessments)

    return Analysis(
        who=who,
        what=what,
        when=when,
        where=where,
        why=why,
        how=how,
        frequency=frequency,
        summary=issues or ["All 5W2H elements appear present with reasonable specificity."],
        refined_suggestion=suggestion,
    )


def suggest_refinement(text: str) -> str:
    """Convenience wrapper that only returns the suggested refined language."""
    return analyze_control(text).refined_suggestion


# ----------------------------- CLI ----------------------------------------- #

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Analyze control language for 5W2H completeness and specificity.")
    parser.add_argument("--in", dest="infile", type=str, help="Input file (one control per line)")
    parser.add_argument("--out", dest="outfile", type=str, help="Output JSON file for findings", default=None)
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout")
    args = parser.parse_args()

    if not args.infile:
        parser.error("--in is required (file with one control per line)")

    findings: List[Dict] = []
    with open(args.infile, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            analysis = analyze_control(line)
            findings.append({
                "control": line,
                "analysis": {
                    "who": asdict(analysis.who),
                    "what": asdict(analysis.what),
                    "when": asdict(analysis.when),
                    "where": asdict(analysis.where),
                    "why": asdict(analysis.why),
                    "how": asdict(analysis.how),
                    "frequency": asdict(analysis.frequency),
                },
                "summary": analysis.summary,
                "refined_suggestion": analysis.refined_suggestion,
            })

    if args.outfile:
        with open(args.outfile, "w", encoding="utf-8") as out:
            json.dump(findings, out, indent=2 if args.pretty else None, ensure_ascii=False)
    else:
        if args.pretty:
            print(json.dumps(findings, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(findings, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
