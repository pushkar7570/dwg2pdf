#!/usr/bin/env python3
"""
Control Language Refiner
------------------------

Purpose
=======
Given a free‑form control description, this script extracts and normalizes the
canonical "7W/H" control attributes:
- Who
- What
- When
- Where
- Why
- How
- How Often (frequency)

It uses lightweight rule‑based NLP (regex + keyword dictionaries) so it runs
without external dependencies. It also provides an interactive gap‑filling
mode and a JSON/YAML export for downstream use (e.g., GRC tooling, audit workpapers).

Usage
=====
# 1) One‑off refinement from stdin
$ python control_language_refiner.py << 'EOF'
The AP Manager reviews the vendor change report from Oracle Fusion weekly to
ensure unauthorized updates are detected and corrected within 5 business days.
EOF

# 2) File input and export to JSON
$ python control_language_refiner.py --in control.txt --out refined.json

# 3) Interactive gap fill
$ python control_language_refiner.py --interactive

Output schema (example)
=======================
{
  "who": "AP Manager",
  "what": "reviews vendor change report",
  "when": "within 5 business days",
  "where": "Oracle Fusion",
  "why": "to ensure unauthorized updates are detected and corrected",
  "how": "manual review against change report",
  "how_often": {
    "raw": "weekly",
    "normalized": "Weekly",
    "period_days": 7
  },
  "original": "...",
  "residual_text": "... any text not mapped ...",
  "confidence": 0.78
}
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

# -------------------------
# Utility dictionaries
# -------------------------
FREQ_MAP = {
    # normalized: (aliases, period_days)
    "Continuous": ([r"continuous(ly)?", r"real[-\s]?time", r"on[-\s]?going"], 0),
    "Per Transaction": ([r"per\s+txn|per\s+transaction", r"each\s+transaction"], 0),
    "Daily": ([r"daily", r"each\s+day", r"every\s+day"], 1),
    "Weekly": ([r"weekly", r"each\s+week", r"every\s+week"], 7),
    "Biweekly": ([r"bi[-\s]?weekly", r"fortnightly"], 14),
    "Monthly": ([r"monthly", r"each\s+month", r"every\s+month"], 30),
    "Quarterly": ([r"quarterly", r"each\s+quarter", r"every\s+quarter", r"qtrly"], 90),
    "Semiannual": ([r"semi[-\s]?annual(ly)?", r"half[-\s]?year(ly)?"], 182),
    "Annual": ([r"annual(ly)?", r"yearly", r"each\s+year", r"every\s+year"], 365),
    "Ad Hoc": ([r"ad[-\s]?hoc", r"as\s+needed", r"as\s+required"], None),
}

WHO_TITLES = [
    "manager", "director", "analyst", "coordinator", "controller", "clerk",
    "vp", "cfo", "cio", "cto", "finance", "accounting", "treasury",
    "ap", "ar", "it", "security", "operations", "hr", "payroll",
]

SYSTEM_HINTS = [
    "oracle", "sap", "workday", "netsuite", "dynamics", "salesforce", "snowflake",
    "fusion", "s4hana", "concur", "coupa", "okta", "active directory", "ad",
]

WHY_PHRASES = [
    r"to\s+ensure[\w\s,-]*", r"to\s+prevent[\w\s,-]*", r"to\s+detect[\w\s,-]*",
    r"so\s+that[\w\s,-]*", r"in\s+order\s+to[\w\s,-]*",
]

WHEN_PHRASES = [
    r"within\s+\d+\s+(business\s+)?days?", r"prior\s+to[\w\s,-]*",
    r"before[\w\s,-]*", r"after[\w\s,-]*", r"by\s+end\s+of\s+month",
]

HOW_PHRASES = [
    r"by\s+\w[\w\s,-]*", r"using\s+\w[\w\s,-]*", r"via\s+\w[\w\s,-]*",
    r"through\s+\w[\w\s,-]*", r"manual(ly)?[\w\s,-]*", r"automated[\w\s,-]*",
]

WHAT_VERBS = [
    "reviews", "reconciles", "approves", "matches", "compares", "monitors",
    "analyzes", "authorizes", "validates", "tests", "configures", "restricts",
    "logs", "escapes", "segregates", "assesses", "reports",
]

# -------------------------
# Data model
# -------------------------
@dataclass
class Frequency:
    raw: Optional[str] = None
    normalized: Optional[str] = None
    period_days: Optional[int] = None

@dataclass
class Control7WH:
    who: Optional[str] = None
    what: Optional[str] = None
    when: Optional[str] = None
    where: Optional[str] = None
    why: Optional[str] = None
    how: Optional[str] = None
    how_often: Frequency = Frequency()
    original: Optional[str] = None
    residual_text: Optional[str] = None
    confidence: float = 0.0

# -------------------------
# Extraction helpers
# -------------------------

def _first(match_iter: re.finditer) -> Optional[Tuple[int, int, str]]:
    try:
        m = next(match_iter)
        return (m.start(), m.end(), m.group(0))
    except StopIteration:
        return None


def detect_frequency(text: str) -> Frequency:
    low = text.lower()
    for norm, (aliases, days) in FREQ_MAP.items():
        for pattern in aliases:
            if re.search(pattern, low):
                hit = re.search(pattern, low)
                return Frequency(raw=hit.group(0), normalized=norm, period_days=days)
    # Numeric cadence (e.g., "every 10 days")
    m = re.search(r"every\s+(\d+)\s+days?", low)
    if m:
        n = int(m.group(1))
        return Frequency(raw=m.group(0), normalized=f"Every {n} days", period_days=n)
    return Frequency()


def detect_who(text: str) -> Optional[str]:
    # Capture role phrases like "AP Manager", "IT Security Analyst"
    role = re.search(r"\b([A-Z]{1,3}\s+)?([A-Z][a-z]+\s+)?(Security|IT|AP|AR|Finance|Accounting|Payroll|Treasury|Operations|HR)\s+[A-Z][a-z]+\b", text)
    if role:
        return role.group(0)
    # fallback: any token ending with Manager/Director/Analyst etc.
    m = re.search(r"\b([A-Z][a-z]+\s+){0,2}(Manager|Director|Analyst|Coordinator|Controller|Clerk)\b", text)
    if m:
        return m.group(0)
    # lower‑case probes
    low = text.lower()
    for t in WHO_TITLES:
        if t in low:
            # lift a small window around the title
            m = re.search(rf"\b\w+(\s+\w+)?\s+{t}\b", low)
            if m:
                return text[m.start():m.end()]
    return None


def detect_where(text: str) -> Optional[str]:
    low = text.lower()
    for s in SYSTEM_HINTS:
        if s in low:
            # return the capitalized token span
            m = re.search(rf"\b([A-Z][\w-]*(\s+[A-Z][\w-]*){{0,3}})?{re.escape(s)}([\s-][A-Z][\w-]*)*\b", text, flags=re.IGNORECASE)
            if m:
                return text[m.start():m.end()].strip().strip(",.")
    # generic: "in <System>", "from <Report>"
    m = re.search(r"\b(in|from|on)\s+([A-Z][\w-]+(\s+[A-Z][\w-]+){0,4})\b", text)
    if m:
        return m.group(2)
    return None


def detect_why(text: str) -> Optional[str]:
    for pat in WHY_PHRASES:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            # extend to sentence end
            end = re.search(r"[\.;]", text[m.end():])
            stop = m.end() + (end.start() if end else len(text) - m.end())
            phrase = text[m.start():stop]
            return phrase.strip().rstrip(",")
    return None


def detect_when(text: str) -> Optional[str]:
    for pat in WHEN_PHRASES:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            # extend modestly
            span = text[m.start(): m.end()+40]
            span = re.split(r"[\.;]", span)[0]
            return span.strip().rstrip(",")
    return None


def detect_how(text: str) -> Optional[str]:
    for pat in HOW_PHRASES:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            span = text[m.start(): m.end()+60]
            span = re.split(r"[\.;]", span)[0]
            return span.strip().rstrip(",")
    return None


def detect_what(text: str) -> Optional[str]:
    low = text.lower()
    # find verb phrase beginning with a common control verb
    for v in WHAT_VERBS:
        if v in low:
            m = re.search(rf"\b{v}\b[\w\s,-]{{0,120}}", low)
            if m:
                span = text[m.start():m.end()]
                # trim trailing clauses like frequency/why if they leaked in
                span = re.split(r"\b(to\s+ensure|to\s+prevent|weekly|monthly|quarterly|daily|within\s+\d+\s+days)\b", span, flags=re.IGNORECASE)[0]
                return span.strip().strip(",")
    # fallback: noun phrase like "review of X report"
    m = re.search(r"\b(review|reconciliation|approval|monitoring)\s+of\s+[^\.;,]+", low)
    if m:
        return text[m.start():m.end()]
    return None


def residual(original: str, parts: Dict[str, Optional[str]]) -> str:
    tmp = original
    for k, v in parts.items():
        if v:
            tmp = re.sub(re.escape(v), " ", tmp, flags=re.IGNORECASE)
    # also strip frequency raw if present
    return re.sub(r"\s+", " ", tmp).strip()


def score(parts: Dict[str, Optional[str]], freq: Frequency) -> float:
    filled = sum(1 for v in parts.values() if v)
    if freq.normalized:
        filled += 1
    return round(filled / 7.0, 2)

# -------------------------
# Public API
# -------------------------

def refine(text: str) -> Control7WH:
    text = text.strip().replace("\n", " ")
    freq = detect_frequency(text)
    parts = {
        "who": detect_who(text),
        "what": detect_what(text),
        "when": detect_when(text),
        "where": detect_where(text),
        "why": detect_why(text),
        "how": detect_how(text),
    }
    res = Control7WH(
        who=parts["who"],
        what=parts["what"],
        when=parts["when"],
        where=parts["where"],
        why=parts["why"],
        how=parts["how"],
        how_often=freq,
        original=text,
        residual_text=residual(text, parts),
        confidence=score(parts, freq),
    )
    return res

# -------------------------
# CLI
# -------------------------

def _read_input(args: argparse.Namespace) -> str:
    if args.infile:
        with open(args.infile, "r", encoding="utf-8") as f:
            return f.read()
    # read from stdin
    data = "".join(iter(input, "")) if not args.text else args.text
    if not data:
        raise SystemExit("No input provided. Use --in, --text, or pipe text to stdin.")
    return data


def _maybe_interactive_fill(model: Control7WH) -> Control7WH:
    def ask(label: str, current: Optional[str]) -> Optional[str]:
        prompt = f"{label} [{current or ''}]: ".strip()
        ans = input(prompt)
        return ans.strip() or current

    print("\nInteractive gap fill — press Enter to keep detected values.\n")
    model.who = ask("Who", model.who)
    model.what = ask("What", model.what)
    model.when = ask("When", model.when)
    model.where = ask("Where", model.where)
    model.why = ask("Why", model.why)
    model.how = ask("How", model.how)

    if not model.how_often.normalized:
        freq_in = ask("How Often (e.g., Weekly, Monthly, Daily, Per Transaction)", model.how_often.raw)
        if freq_in:
            f = detect_frequency(freq_in)
            if f.normalized:
                model.how_often = f
            else:
                model.how_often.raw = freq_in
    model.confidence = score({
        "who": model.who,
        "what": model.what,
        "when": model.when,
        "where": model.where,
        "why": model.why,
        "how": model.how,
    }, model.how_often)
    return model


def main():
    p = argparse.ArgumentParser(description="Refine a control description into 7W/H fields")
    p.add_argument("--in", dest="infile", help="Path to input text file")
    p.add_argument("--out", dest="outfile", help="Path to write JSON/YAML output")
    p.add_argument("--yaml", action="store_true", help="Write YAML instead of JSON (requires PyYAML)")
    p.add_argument("--text", help="Inline text input (overrides --in)")
    p.add_argument("--interactive", action="store_true", help="Prompt to fill gaps / confirm values")
    args = p.parse_args()

    raw = _read_input(args)
    model = refine(raw)
    if args.interactive:
        model = _maybe_interactive_fill(model)

    payload = asdict(model)

    if args.outfile:
        if args.yaml:
            if yaml is None:
                raise SystemExit("PyYAML not available. Install pyyaml or omit --yaml.")
            with open(args.outfile, "w", encoding="utf-8") as f:
                yaml.safe_dump(payload, f, sort_keys=False)
        else:
            with open(args.outfile, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Written to {args.outfile}")
    else:
        # pretty print to stdout
        if args.yaml and yaml is not None:
            print(yaml.safe_dump(payload, sort_keys=False))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
