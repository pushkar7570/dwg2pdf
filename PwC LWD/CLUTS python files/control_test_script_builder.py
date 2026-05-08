#!/usr/bin/env python3
"""
Control Test Script Builder
---------------------------------
Creates a structured test script for a control that clearly documents the
Who, What, When, Where, Why, How, and How Often (frequency).

✅ Zero external dependencies (standard library only)
✅ Works via interactive prompts OR JSON config file
✅ Outputs Markdown (default) or JSON

USAGE
-----
Interactive (guided prompts):
    python control_test_script_builder.py --interactive

From a JSON config file:
    python control_test_script_builder.py --config control.json --out md --file MyTestScript.md

Set the in-scope period (for context in the script):
    python control_test_script_builder.py --interactive --period-start 2024-07-01 --period-end 2025-06-30

EXAMPLE JSON CONFIG (save as control.json)
-----------------------------------------
{
  "control_id": "ITGC-AC-001",
  "control_title": "User Access Reviews",
  "objective": "Ensure that only authorized users retain access to critical systems.",
  "description": "Control owner performs a monthly review of active users and removes inappropriate access.",
  "who": "System Owner and Compliance Analyst",
  "what": "Review of active user listings vs. approved role matrix, documenting approvals and removals.",
  "when": "Within 15 days after month-end",
  "where": "Identity Governance tool and ticketing system",
  "why": "To mitigate risk of unauthorized access leading to data exfiltration or fraud.",
  "how": "Export user list, compare to role matrix, investigate exceptions, approve removals, and evidence with signed checklist.",
  "frequency": "Monthly",
  "population_description": "All monthly access reviews in scope period",
  "systems": ["Okta", "ServiceNow"],
  "owner_names": ["Jane Doe", "John Smith"],
  "assumptions": ["Access matrix is current", "Terminations feed is complete"],
  "expected_evidence": [
    "Signed review checklist",
    "Exception investigation notes",
    "Tickets showing access removal approvals"
  ]
}

NOTES
-----
- Suggested sample sizes are generic heuristics and should be aligned with your firm's methodology.
- Frequency normalization supports values like Daily, Weekly, Monthly, Quarterly, Semiannual, Annual, Continuous, Per-Transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import List, Optional, Dict, Tuple
import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------- Helpers & Constants ----------------------------
FREQUENCY_ALIASES = {
    "daily": {"day", "daily", "each day"},
    "weekly": {"week", "weekly", "each week"},
    "monthly": {"month", "monthly", "each month"},
    "quarterly": {"quarter", "quarterly", "qtr", "qtrly"},
    "semiannual": {"semi-annual", "semiannual", "half-year", "biannual"},
    "annual": {"year", "annually", "annual", "yearly"},
    "continuous": {"continuous", "real-time", "realtime", "ongoing"},
    "per-transaction": {"per transaction", "per-transaction", "each transaction", "event-driven"},
}

FREQUENCY_SAMPLE_HEURISTICS = {
    # Generic, illustrative numbers. Adjust to firm methodology as needed.
    "daily": (25, "Daily control: select ~25 across the period to cover variability."),
    "weekly": (25, "Weekly control: select ~25 across the period to cover multiple weeks."),
    "monthly": (15, "Monthly control: select ~15 months/occurrences (or all if <15)."),
    "quarterly": (5, "Quarterly control: select ~5 occurrences (or all if fewer)."),
    "semiannual": (3, "Semiannual control: select ~3 occurrences (or all if fewer)."),
    "annual": (2, "Annual control: select ~2 occurrences (or all if fewer)."),
    "continuous": (25, "Continuous monitoring: select ~25 transactions/alerts across the period."),
    "per-transaction": (25, "Per-transaction control: select ~25 transactions across the period."),
}

CHECKLIST_ACTIONS = [
    "Obtain population and define in-scope period",
    "Perform walkthrough with control owner (design assessment)",
    "Define sampling approach (representative across period)",
    "Inspect evidence for selected samples",
    "Reperform key control steps where feasible",
    "Evaluate exceptions and perform follow-up",
    "Form conclusion and document results",
]


def normalize_frequency(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    value = raw.strip().lower()
    value = re.sub(r"\s+", " ", value)
    for canonical, aliases in FREQUENCY_ALIASES.items():
        if value == canonical:
            return canonical
        if value in aliases:
            return canonical
    # Try partial contains
    for canonical, aliases in FREQUENCY_ALIASES.items():
        if any(tok in value for tok in aliases | {canonical}):
            return canonical
    return raw.lower()


def suggest_sample_size(freq: Optional[str], population_size: Optional[int] = None) -> Tuple[Optional[int], Optional[str]]:
    f = normalize_frequency(freq) if freq else None
    if f in FREQUENCY_SAMPLE_HEURISTICS:
        n, rationale = FREQUENCY_SAMPLE_HEURISTICS[f]
        if population_size is not None and population_size < n:
            return population_size, f"Population smaller than heuristic; testing all {population_size}."
        return n, rationale
    return None, None


# ---------------------------- Data Model ----------------------------
@dataclass
class Control:
    control_id: str = ""
    control_title: str = ""
    objective: str = ""
    description: str = ""
    who: str = ""      # Roles / names performing the control
    what: str = ""     # Activity performed
    when: str = ""     # Timing relative to process events
    where: str = ""    # Systems / locations
    why: str = ""      # Risk intent
    how: str = ""      # Detailed procedure
    frequency: str = ""  # How often

    population_description: str = ""
    systems: List[str] = field(default_factory=list)
    owner_names: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    expected_evidence: List[str] = field(default_factory=list)

    period_start: Optional[date] = None
    period_end: Optional[date] = None
    population_size: Optional[int] = None
    sample_size_override: Optional[int] = None

    def to_markdown(self) -> str:
        freq_norm = normalize_frequency(self.frequency)
        suggested_n, rationale = suggest_sample_size(freq_norm, self.population_size)
        sample_size = self.sample_size_override or suggested_n

        lines = []
        lines.append(f"# Test Script — {self.control_title or 'Control'}")
        if self.control_id:
            lines.append(f"**Control ID:** {self.control_id}")
        lines.append("")
        if self.objective:
            lines.append(f"**Objective:** {self.objective}")
            lines.append("")
        if self.description:
            lines.append("**Control Description:**")
            lines.append(self.description)
            lines.append("")

        # 5W2H
        lines.append("## Who / What / When / Where / Why / How / How Often")
        pairs = [
            ("Who", self.who),
            ("What", self.what),
            ("When", self.when),
            ("Where", self.where),
            ("Why", self.why),
            ("How", self.how),
            ("How Often", self.frequency),
        ]
        for k, v in pairs:
            if v:
                lines.append(f"- **{k}:** {v}")
        lines.append("")

        # Scope & period
        lines.append("## Scope and In-Scope Period")
        if self.period_start or self.period_end:
            ps = self.period_start.isoformat() if self.period_start else "(start not set)"
            pe = self.period_end.isoformat() if self.period_end else "(end not set)"
            lines.append(f"- **Period:** {ps} to {pe}")
        if self.population_description:
            lines.append(f"- **Population:** {self.population_description}")
        if self.population_size is not None:
            lines.append(f"- **Population Size (if known):** {self.population_size}")
        if self.systems:
            lines.append(f"- **Systems in Scope:** {', '.join(self.systems)}")
        if self.owner_names:
            lines.append(f"- **Control Owner(s):** {', '.join(self.owner_names)}")
        lines.append("")

        # Sampling
        lines.append("## Sampling Approach")
        if sample_size:
            lines.append(f"- **Sample Size:** {sample_size}")
        if rationale:
            lines.append(f"- **Rationale:** {rationale}")
        if freq_norm:
            lines.append(f"- **Frequency (normalized):** {freq_norm}")
        lines.append("- **Selection Method:** Representative across the period; avoid clustering around a single month/week.")
        lines.append("- **Stratification (if applicable):** Consider system, business unit, or risk-based strata.")
        lines.append("")

        # Procedures
        lines.append("## Test Procedures")
        lines.append("1. **Obtain** the complete population for the in-scope period and agree completeness/accuracy (e.g., system of record reports).")
        lines.append("2. **Perform a walkthrough** with the control owner to confirm design: inputs, processing, outputs, and evidence retained.")
        lines.append("3. **Define the sample** using the approach above and document the selection.")
        lines.append("4. **Inspect evidence** for each sample to verify that the control was performed as described (who/what/when/where/how) and that approvals are present.")
        lines.append("5. **Reperform** key steps where feasible to validate accuracy and appropriateness of outcomes.")
        lines.append("6. **Evaluate exceptions**, determine root cause and potential impact, and perform additional procedures as needed.")
        lines.append("7. **Conclude** on operating effectiveness and retain all supporting documentation.")
        lines.append("")

        # Expected Evidence
        if self.expected_evidence:
            lines.append("## Expected Evidence to Retain")
            for item in self.expected_evidence:
                lines.append(f"- {item}")
            lines.append("")

        # Assumptions / Constraints
        if self.assumptions:
            lines.append("## Assumptions / Constraints")
            for a in self.assumptions:
                lines.append(f"- {a}")
            lines.append("")

        # Results Placeholders
        lines.append("## Results")
        lines.append("- **Population obtained and validated:** [Yes/No]")
        lines.append("- **Samples tested:** [n]")
        lines.append("- **Exceptions identified:** [Describe or None]")
        lines.append("- **Conclusion:** [Effective / Not Effective / Partially Effective]")
        lines.append("")

        # Appendix
        lines.append("## Appendix")
        lines.append("- **Detailed sample listing:** [Attach]")
        lines.append("- **Evidence cross-reference:** [Attach index]")
        lines.append("- **Walkthrough notes:** [Attach]")
        lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        payload = asdict(self)
        # Serialize dates
        if self.period_start:
            payload["period_start"] = self.period_start.isoformat()
        if self.period_end:
            payload["period_end"] = self.period_end.isoformat()
        return json.dumps(payload, indent=2)


# ---------------------------- I/O & CLI ----------------------------

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a test script for a control (Who/What/When/Where/Why/How/Frequency)")
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--interactive", action="store_true", help="Run guided prompts to collect inputs")
    g.add_argument("--config", type=str, help="Path to JSON file with control details")

    p.add_argument("--out", choices=["md", "json"], default="md", help="Output format (default: md)")
    p.add_argument("--file", type=str, help="Output file path (default is derived from title/id)")

    p.add_argument("--period-start", type=str, help="In-scope period start (YYYY-MM-DD)")
    p.add_argument("--period-end", type=str, help="In-scope period end (YYYY-MM-DD)")

    p.add_argument("--sample-size", type=int, help="Override sample size")
    p.add_argument("--population-size", type=int, help="Population size (optional)")

    return p.parse_args(argv)


def read_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def prompt(label: str) -> str:
    try:
        return input(f"{label}: ").strip()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)


def prompt_list(label: str) -> List[str]:
    raw = prompt(label + " (comma-separated, optional)")
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def parse_date(val: Optional[str]) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"Invalid date '{val}'. Use YYYY-MM-DD.")


def build_control_from_inputs(args: argparse.Namespace) -> Control:
    data: Dict = {}

    if args.config:
        data = read_json(args.config)
    elif args.interactive:
        print("\n-- Control Basics --")
        data["control_id"] = prompt("Control ID (optional)")
        data["control_title"] = prompt("Control Title")
        data["objective"] = prompt("Objective")
        data["description"] = prompt("Control Description")

        print("\n-- 5W2H --")
        data["who"] = prompt("Who performs the control (roles/names)")
        data["what"] = prompt("What is performed")
        data["when"] = prompt("When is it performed (timing)")
        data["where"] = prompt("Where is it performed (systems/locations)")
        data["why"] = prompt("Why is it performed (risk addressed)")
        data["how"] = prompt("How is it performed (procedure)")
        data["frequency"] = prompt("How often (frequency)")

        print("\n-- Additional Context (optional) --")
        data["population_description"] = prompt("Population description")
        data["systems"] = prompt_list("Systems in scope")
        data["owner_names"] = prompt_list("Control owner names")
        data["assumptions"] = prompt_list("Assumptions/constraints")
        data["expected_evidence"] = prompt_list("Expected evidence items")
    else:
        # If neither provided, still allow empty control to be created (user may only want the template)
        data = {}

    ctrl = Control(
        control_id=data.get("control_id", ""),
        control_title=data.get("control_title", ""),
        objective=data.get("objective", ""),
        description=data.get("description", ""),
        who=data.get("who", ""),
        what=data.get("what", ""),
        when=data.get("when", ""),
        where=data.get("where", ""),
        why=data.get("why", ""),
        how=data.get("how", ""),
        frequency=data.get("frequency", ""),
        population_description=data.get("population_description", ""),
        systems=data.get("systems", []),
        owner_names=data.get("owner_names", []),
        assumptions=data.get("assumptions", []),
        expected_evidence=data.get("expected_evidence", []),
    )

    # Period
    ps = args.period_start or (data.get("period_start") if isinstance(data.get("period_start"), str) else None)
    pe = args.period_end or (data.get("period_end") if isinstance(data.get("period_end"), str) else None)
    ctrl.period_start = parse_date(ps)
    ctrl.period_end = parse_date(pe)

    # Sizes
    ctrl.population_size = args.population_size if args.population_size is not None else data.get("population_size")
    ctrl.sample_size_override = args.sample_size if args.sample_size is not None else data.get("sample_size_override")

    return ctrl


def derive_default_filename(control: Control, out_fmt: str) -> str:
    base = control.control_title or control.control_id or "Control_Test_Script"
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")
    ext = ".md" if out_fmt == "md" else ".json"
    return base + ext


def write_output(control: Control, out_fmt: str, path: Optional[str]):
    content = control.to_markdown() if out_fmt == "md" else control.to_json()
    fname = path or derive_default_filename(control, out_fmt)
    Path(fname).write_text(content, encoding="utf-8")
    print(f"Saved {out_fmt.upper()} to: {fname}")


# ---------------------------- Main ----------------------------

def main(argv: List[str]) -> None:
    args = parse_args(argv)
    control = build_control_from_inputs(args)

    if not control.control_title and not control.control_id:
        print("(No title/ID provided; using a generic filename.)")

    write_output(control, args.out, args.file)


if __name__ == "__main__":
    main(sys.argv[1:])
