# Building Overlap Agent Demo - Full Code

## File structure

- `.devcontainer/Dockerfile`
- `.devcontainer/devcontainer.json`
- `.env.example`
- `.gitignore`
- `README.md`
- `app/__init__.py`
- `app/agents/__init__.py`
- `app/agents/analysis_agent.py`
- `app/agents/decision_agent.py`
- `app/agents/ingestion_agent.py`
- `app/agents/repair_agent.py`
- `app/agents/report_agent.py`
- `app/agents/validation_agent.py`
- `app/api.py`
- `app/cli.py`
- `app/config.py`
- `app/orchestrator.py`
- `app/schemas.py`
- `app/services/__init__.py`
- `app/services/converter.py`
- `app/services/dxf_tools.py`
- `app/services/llm_router.py`
- `app/services/overlap_detector.py`
- `app/services/pdf_parser.py`
- `app/services/pdf_repair.py`
- `app/services/previews.py`
- `app/state.py`
- `app/utils/__init__.py`
- `app/utils/colors.py`
- `app/utils/fs.py`
- `app/utils/geometry.py`
- `app/utils/progress.py`
- `outputs/04c8ecdd5da5/report.json`
- `outputs/04c8ecdd5da5/summary.txt`
- `requirements.txt`
- `scripts/dxf_to_pdf.py`
- `scripts/generate_demo_pdf.py`

## Code

### `.devcontainer/Dockerfile`

```
FROM mcr.microsoft.com/devcontainers/python:1-3.11-bullseye

RUN apt-get update     && export DEBIAN_FRONTEND=noninteractive     && apt-get install -y --no-install-recommends         build-essential         curl         git         libgl1     && rm -rf /var/lib/apt/lists/*

```

### `.devcontainer/devcontainer.json`

```json
{
  "name": "building-overlap-agent",
  "build": {
    "dockerfile": "Dockerfile"
  },
  "customizations": {
    "vscode": {
      "settings": {
        "python.defaultInterpreterPath": "/usr/local/bin/python",
        "python.analysis.typeCheckingMode": "basic"
      },
      "extensions": [
        "ms-python.python",
        "ms-python.vscode-pylance",
        "ms-toolsai.jupyter"
      ]
    }
  },
  "forwardPorts": [8000],
  "postCreateCommand": "pip install -r requirements.txt",
  "remoteUser": "vscode"
}

```

### `.env.example`

```
# Optional local LLM router (completely optional for the demo)
USE_LLM_ROUTER=false
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:1.5b

# Optional external CAD conversion hooks
# Fully open-source path with LibreDWG:
DWG_TO_DXF_CMD=dwgread -O DXF -o "{output}" "{input}"

# Optional if you want to force an external DXF->PDF step instead of the built-in Python renderer
# DXF_TO_PDF_CMD=python scripts/dxf_to_pdf.py "{input}" "{output}"

# Demo repair tuning
OVERLAP_AREA_THRESHOLD=4.0
TEXT_PADDING=1.0
DRAWING_PADDING=1.5
CANDIDATE_STEP=12
MAX_SEARCH_RADIUS=180
DRAW_LEADER_LINES=false

```

### `.gitignore`

```
__pycache__/
*.pyc
*.pyo
*.pyd
.venv/
.env
outputs/
work/
*.log
.DS_Store

```

### `README.md`

```
# Building Drawing Text-Overlap Agent Demo

This repository is a **demo-grade agentic pipeline** that:

1. accepts **PDF**, **DXF**, or **DWG** input,
2. normalizes the input into a working PDF,
3. detects text that overlaps drawing geometry,
4. moves overlapping text to the nearest safe position,
5. validates the output PDF,
6. writes a JSON report and preview images.

The demo is designed for **GitHub Codespaces** and keeps the expensive or unreliable parts optional:

- **PDF input** works immediately.
- **DXF input** works when `ezdxf` is installed.
- **DWG input** works when you plug in a free external DWG->DXF converter command, for example LibreDWG.

## What is “agentic” here?

The flow is split into explicit agents:

- **IngestionAgent** - normalizes PDF/DXF/DWG input.
- **AnalysisAgent** - extracts text and vector drawing regions.
- **DecisionAgent** - chooses repair strategy.
- **RepairAgent** - applies PDF text relocation.
- **ValidationAgent** - re-checks the corrected PDF.
- **ReportAgent** - generates JSON and preview artifacts.

The router can optionally use a **local Ollama model**, but the repo also runs with **pure open-source deterministic heuristics** so you are not blocked by API credits.

## Suggested stack

- Python 3.11
- FastAPI
- PyMuPDF
- Shapely
- Rich
- Pillow
- ezdxf
- Optional Ollama (`qwen2.5:1.5b` or `qwen2.5:0.5b`)
- Optional LibreDWG for DWG->DXF

## Quick start

### 1) Open in Codespaces

This repo includes a `.devcontainer` setup. After Codespaces starts:

```bash
pip install -r requirements.txt
```

### 2) Generate a demo PDF

```bash
python scripts/generate_demo_pdf.py
```

This creates `inputs/demo_overlap.pdf`.

### 3) Run the pipeline

```bash
python -m app.cli run --input inputs/demo_overlap.pdf
```

### 4) Review outputs

The pipeline writes a job folder under `outputs/<job_id>/` with:

- `original.pdf`
- `corrected.pdf`
- `report.json`
- `summary.txt`
- preview PNG files

## DXF input

If `ezdxf` is installed, you can pass a DXF directly:

```bash
python -m app.cli run --input your_file.dxf
```

## DWG input

DWG parsing/rendering is the only part that depends on an external CAD converter. Set a command template in your environment.

Example with **LibreDWG**:

```bash
export DWG_TO_DXF_CMD='dwgread -O DXF -o "{output}" "{input}"'
python -m app.cli run --input your_file.dwg
```

If you prefer ODA or another converter, set the same environment variable with your working command.

## Run the API

```bash
uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Optional Ollama router

If you want the decision step to use a local open model:

```bash
export USE_LLM_ROUTER=true
export OLLAMA_HOST=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen2.5:1.5b
```

If Ollama is unavailable, the pipeline falls back automatically to deterministic routing.

```

### `app/__init__.py`

```python
"""Building drawing overlap agent demo."""

```

### `app/agents/__init__.py`

```python
"""Agent implementations."""

```

### `app/agents/analysis_agent.py`

```python
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services.overlap_detector import detect_overlaps
from app.services.pdf_parser import parse_pdf
from app.state import PipelineState
from app.utils.progress import ProgressTracker


class AnalysisAgent:
    name = "analysis_agent"

    def run(self, state: PipelineState, tracker: ProgressTracker, settings: Settings) -> PipelineState:
        tracker.log("analysis", self.name, "Parsing PDF objects and computing overlaps")
        parsed = parse_pdf(Path(state["normalized_pdf_path"]), settings)
        analyzed = detect_overlaps(parsed, settings)
        state["original_analysis"] = analyzed
        state["overlaps_before"] = sum(len(page.overlaps) for page in analyzed)
        tracker.log("analysis", self.name, f"Detected {state['overlaps_before']} overlaps")
        return state

```

### `app/agents/decision_agent.py`

```python
from __future__ import annotations

from app.config import Settings
from app.schemas import PipelineDecision
from app.services.llm_router import decide_strategy
from app.state import PipelineState
from app.utils.progress import ProgressTracker


class DecisionAgent:
    name = "decision_agent"

    def run(self, state: PipelineState, tracker: ProgressTracker, settings: Settings) -> PipelineState:
        decision: PipelineDecision = decide_strategy(state["original_analysis"], settings)
        state["decision"] = decision
        tracker.log("decision", self.name, f"Strategy={decision.strategy} | {decision.reason}")
        return state

```

### `app/agents/ingestion_agent.py`

```python
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services.converter import normalize_input_to_pdf
from app.state import PipelineState
from app.utils.progress import ProgressTracker


class IngestionAgent:
    name = "ingestion_agent"

    def run(self, state: PipelineState, tracker: ProgressTracker, settings: Settings) -> PipelineState:
        tracker.log("ingest", self.name, f"Normalizing input: {state['input_path']}")
        workdir = Path(state["workdir"])
        pdf_path, source_kind, dxf_path, warnings = normalize_input_to_pdf(Path(state["input_path"]), workdir, settings)
        state["normalized_pdf_path"] = str(pdf_path)
        state["source_kind"] = source_kind
        state["intermediate_dxf_path"] = str(dxf_path) if dxf_path else None
        state.setdefault("warnings", []).extend(warnings)
        tracker.log("ingest", self.name, f"Working PDF ready: {pdf_path}")
        return state

```

### `app/agents/repair_agent.py`

```python
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services.pdf_repair import repair_pdf
from app.state import PipelineState
from app.utils.fs import safe_copy
from app.utils.progress import ProgressTracker


class RepairAgent:
    name = "repair_agent"

    def run(self, state: PipelineState, tracker: ProgressTracker, settings: Settings) -> PipelineState:
        decision = state["decision"]
        output_path = Path(state["workdir"]) / "corrected.pdf"
        if decision.strategy == "no_change":
            safe_copy(Path(state["normalized_pdf_path"]), output_path)
            state["corrected_pdf_path"] = str(output_path)
            state["repair_actions"] = []
            tracker.log("repair", self.name, "No repair needed; copied original PDF")
            return state

        tracker.log("repair", self.name, "Applying PDF text relocation repair")
        corrected_path, actions = repair_pdf(
            Path(state["normalized_pdf_path"]),
            state["original_analysis"],
            decision,
            output_path,
            settings,
        )
        state["corrected_pdf_path"] = str(corrected_path)
        state["repair_actions"] = actions
        tracker.log("repair", self.name, f"Repair actions created: {len(actions)}")
        return state

```

### `app/agents/report_agent.py`

```python
from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.schemas import PipelineReport
from app.services.previews import write_preview_images
from app.state import PipelineState
from app.utils.progress import ProgressTracker


class ReportAgent:
    name = "report_agent"

    def run(self, state: PipelineState, tracker: ProgressTracker, settings: Settings) -> PipelineState:
        tracker.log("report", self.name, "Writing report and preview artifacts")
        workdir = Path(state["workdir"])
        preview_paths = write_preview_images(
            Path(state["normalized_pdf_path"]),
            Path(state["corrected_pdf_path"]),
            state["original_analysis"],
            state.get("repair_actions", []),
            workdir / "previews",
        )
        state["preview_paths"] = preview_paths
        report = PipelineReport(
            job_id=state["job_id"],
            input_path=state["input_path"],
            source_kind=state["source_kind"],
            normalized_pdf_path=state["normalized_pdf_path"],
            corrected_pdf_path=state["corrected_pdf_path"],
            overlaps_before=state["overlaps_before"],
            overlaps_after=state["overlaps_after"],
            decision=state["decision"],
            repair_actions=state.get("repair_actions", []),
            preview_paths=preview_paths,
            warnings=state.get("warnings", []),
            timeline=tracker.events,
        )
        report_path = workdir / "report.json"
        summary_path = workdir / "summary.txt"
        report_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
        summary_path.write_text(
            "\n".join(
                [
                    f"job_id: {report.job_id}",
                    f"input: {report.input_path}",
                    f"source_kind: {report.source_kind}",
                    f"overlaps_before: {report.overlaps_before}",
                    f"overlaps_after: {report.overlaps_after}",
                    f"decision: {report.decision.strategy}",
                    f"reason: {report.decision.reason}",
                ]
            ),
            encoding="utf-8",
        )
        state["report_path"] = str(report_path)
        state["summary_path"] = str(summary_path)
        state["timeline"] = tracker.events
        state["report"] = report
        tracker.log("report", self.name, f"Report ready: {report_path}")
        return state

```

### `app/agents/validation_agent.py`

```python
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services.overlap_detector import detect_overlaps
from app.services.pdf_parser import parse_pdf
from app.state import PipelineState
from app.utils.progress import ProgressTracker


class ValidationAgent:
    name = "validation_agent"

    def run(self, state: PipelineState, tracker: ProgressTracker, settings: Settings) -> PipelineState:
        tracker.log("validate", self.name, "Re-running overlap detection on corrected PDF")
        parsed = parse_pdf(Path(state["corrected_pdf_path"]), settings)
        analyzed = detect_overlaps(parsed, settings)
        state["repaired_analysis"] = analyzed
        state["overlaps_after"] = sum(len(page.overlaps) for page in analyzed)
        if state["overlaps_after"] > settings.validation_max_remaining_overlaps:
            state.setdefault("warnings", []).append(
                f"Validation found {state['overlaps_after']} remaining overlaps."
            )
        tracker.log("validate", self.name, f"Remaining overlaps: {state['overlaps_after']}")
        return state

```

### `app/api.py`

```python
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse

from app.orchestrator import AgenticPipeline

app = FastAPI(title="Building Overlap Agent Demo", version="0.1.0")
pipeline = AgenticPipeline()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/process")
async def process(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "uploaded.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    state = pipeline.run(tmp_path)
    return state["report"].model_dump(mode="json")


@app.get("/download/{job_id}")
def download(job_id: str) -> FileResponse:
    output = Path("outputs") / job_id / "corrected.pdf"
    return FileResponse(output)

```

### `app/cli.py`

```python
from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console

from app.orchestrator import AgenticPipeline
from app.utils.progress import ProgressTracker


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Building drawing overlap agent demo")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run the pipeline")
    run_cmd.add_argument("--input", required=True, help="Path to PDF, DXF, or DWG")

    serve_cmd = sub.add_parser("serve", help="Run the FastAPI server")
    serve_cmd.add_argument("--host", default="0.0.0.0")
    serve_cmd.add_argument("--port", type=int, default=8000)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    console = Console()

    if args.command == "run":
        tracker = ProgressTracker(console=console)
        state = AgenticPipeline().run(Path(args.input), tracker=tracker)
        tracker.print_summary()
        report = state["report"]
        console.print("\n[bold green]Done[/]")
        console.print(f"Job ID: {report.job_id}")
        console.print(f"Original PDF: {report.normalized_pdf_path}")
        console.print(f"Corrected PDF: {report.corrected_pdf_path}")
        console.print(f"Report JSON: {state['report_path']}")
        console.print(f"Summary TXT: {state['summary_path']}")
        if report.warnings:
            console.print("Warnings:")
            for warning in report.warnings:
                console.print(f"  - {warning}")
        return

    if args.command == "serve":
        import uvicorn

        uvicorn.run("app.api:app", host=args.host, port=args.port, reload=True)


if __name__ == "__main__":
    main()

```

### `app/config.py`

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


@dataclass(slots=True)
class Settings:
    output_root: Path = Path("outputs")
    work_root: Path = Path("work")
    overlap_area_threshold: float = _env_float("OVERLAP_AREA_THRESHOLD", 4.0)
    text_padding: float = _env_float("TEXT_PADDING", 1.0)
    drawing_padding: float = _env_float("DRAWING_PADDING", 1.5)
    candidate_step: int = _env_int("CANDIDATE_STEP", 12)
    max_search_radius: int = _env_int("MAX_SEARCH_RADIUS", 180)
    draw_leader_lines: bool = _env_bool("DRAW_LEADER_LINES", False)
    min_text_chars: int = _env_int("MIN_TEXT_CHARS", 1)
    use_llm_router: bool = _env_bool("USE_LLM_ROUTER", False)
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    dwg_to_dxf_cmd: str | None = os.getenv("DWG_TO_DXF_CMD")
    dxf_to_pdf_cmd: str | None = os.getenv("DXF_TO_PDF_CMD")
    validation_max_remaining_overlaps: int = _env_int("VALIDATION_MAX_REMAINING_OVERLAPS", 0)


settings = Settings()

```

### `app/orchestrator.py`

```python
from __future__ import annotations

from pathlib import Path

from app.agents.analysis_agent import AnalysisAgent
from app.agents.decision_agent import DecisionAgent
from app.agents.ingestion_agent import IngestionAgent
from app.agents.repair_agent import RepairAgent
from app.agents.report_agent import ReportAgent
from app.agents.validation_agent import ValidationAgent
from app.config import Settings, settings
from app.state import PipelineState
from app.utils.fs import make_job_dirs
from app.utils.progress import ProgressTracker


class AgenticPipeline:
    def __init__(self, settings_obj: Settings | None = None) -> None:
        self.settings = settings_obj or settings
        self.ingestion = IngestionAgent()
        self.analysis = AnalysisAgent()
        self.decision = DecisionAgent()
        self.repair = RepairAgent()
        self.validation = ValidationAgent()
        self.report = ReportAgent()

    def run(self, input_path: str | Path, tracker: ProgressTracker | None = None) -> PipelineState:
        tracker = tracker or ProgressTracker()
        self.settings.output_root.mkdir(parents=True, exist_ok=True)
        job_id, workdir = make_job_dirs(self.settings.output_root)
        state: PipelineState = {
            "job_id": job_id,
            "input_path": str(Path(input_path).resolve()),
            "workdir": str(workdir.resolve()),
            "warnings": [],
        }
        tracker.log("pipeline", "orchestrator", f"Job started: {job_id}")
        state = self.ingestion.run(state, tracker, self.settings)
        state = self.analysis.run(state, tracker, self.settings)
        state = self.decision.run(state, tracker, self.settings)
        state = self.repair.run(state, tracker, self.settings)
        state = self.validation.run(state, tracker, self.settings)
        state = self.report.run(state, tracker, self.settings)
        tracker.log("pipeline", "orchestrator", f"Job completed: {job_id}")
        state["timeline"] = tracker.events
        return state

```

### `app/schemas.py`

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Rect4 = tuple[float, float, float, float]


class TimelineEvent(BaseModel):
    stage: str
    agent: str
    message: str
    elapsed_seconds: float


class TextSpan(BaseModel):
    span_id: str
    page_number: int
    text: str
    bbox: Rect4
    font: str = "helv"
    fontsize: float = 10.0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    origin: tuple[float, float] = (0.0, 0.0)
    rotation: int = 0


class DrawingZone(BaseModel):
    zone_id: str
    page_number: int
    bbox: Rect4


class OverlapRecord(BaseModel):
    overlap_id: str
    page_number: int
    span_id: str
    zone_id: str
    area: float
    text: str
    span_bbox: Rect4
    zone_bbox: Rect4


class PageAnalysis(BaseModel):
    page_number: int
    page_bbox: Rect4
    text_spans: list[TextSpan] = Field(default_factory=list)
    drawing_zones: list[DrawingZone] = Field(default_factory=list)
    overlaps: list[OverlapRecord] = Field(default_factory=list)

    @property
    def overlap_count(self) -> int:
        return len(self.overlaps)


class PipelineDecision(BaseModel):
    strategy: Literal["no_change", "local_move", "margin_relocate", "review"]
    reason: str
    llm_used: bool = False


class RepairAction(BaseModel):
    page_number: int
    span_id: str
    text: str
    old_bbox: Rect4
    new_bbox: Rect4 | None = None
    action: Literal["moved", "flagged", "skipped"]
    reason: str


class PipelineReport(BaseModel):
    job_id: str
    input_path: str
    source_kind: str
    normalized_pdf_path: str
    corrected_pdf_path: str
    overlaps_before: int
    overlaps_after: int
    decision: PipelineDecision
    repair_actions: list[RepairAction]
    preview_paths: list[str]
    warnings: list[str]
    timeline: list[TimelineEvent]

```

### `app/services/__init__.py`

```python
"""Service layer."""

```

### `app/services/converter.py`

```python
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from app.config import Settings
from app.services.dxf_tools import render_dxf_to_pdf
from app.utils.fs import safe_copy


def _run_template(template: str, input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    safe_template = template.format(
        input=shlex.quote(str(input_path.resolve())),
        output=shlex.quote(str(output_path.resolve())),
    )
    subprocess.run(safe_template, shell=True, check=True)


def normalize_input_to_pdf(input_path: Path, workdir: Path, settings: Settings) -> tuple[Path, str, Path | None, list[str]]:
    source_kind = input_path.suffix.lower().lstrip(".")
    warnings: list[str] = []
    intermediate_dxf: Path | None = None

    if source_kind == "pdf":
        target = workdir / "original.pdf"
        safe_copy(input_path, target)
        return target, "pdf", None, warnings

    if source_kind == "dxf":
        target = workdir / "original.pdf"
        if settings.dxf_to_pdf_cmd:
            _run_template(settings.dxf_to_pdf_cmd, input_path, target)
        else:
            render_dxf_to_pdf(input_path, target)
        return target, "dxf", input_path, warnings

    if source_kind == "dwg":
        if not settings.dwg_to_dxf_cmd:
            raise RuntimeError(
                "DWG input needs DWG_TO_DXF_CMD. Example: dwgread -O DXF -o \"{output}\" \"{input}\""
            )
        intermediate_dxf = workdir / "converted_from_dwg.dxf"
        _run_template(settings.dwg_to_dxf_cmd, input_path, intermediate_dxf)
        target = workdir / "original.pdf"
        if settings.dxf_to_pdf_cmd:
            _run_template(settings.dxf_to_pdf_cmd, intermediate_dxf, target)
        else:
            render_dxf_to_pdf(intermediate_dxf, target)
        warnings.append("DWG was converted externally before PDF analysis.")
        return target, "dwg", intermediate_dxf, warnings

    raise RuntimeError(f"Unsupported input type: {input_path.suffix}")

```

### `app/services/dxf_tools.py`

```python
from __future__ import annotations

from pathlib import Path


def render_dxf_to_pdf(input_path: Path, output_path: Path) -> Path:
    try:
        import ezdxf
        from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf
    except Exception as exc:  # pragma: no cover - optional dependency at runtime
        raise RuntimeError(
            "DXF support requires ezdxf. Install requirements.txt in Codespaces."
        ) from exc

    doc = ezdxf.readfile(str(input_path))
    msp = doc.modelspace()
    context = RenderContext(doc)
    backend = pymupdf.PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
    frontend = Frontend(context, backend, config=cfg)
    frontend.draw_layout(msp)
    page = layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(5))
    pdf_bytes = backend.get_pdf_bytes(page)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
    return output_path

```

### `app/services/llm_router.py`

```python
from __future__ import annotations

import json
from typing import Any

import requests

from app.config import Settings
from app.schemas import PageAnalysis, PipelineDecision


def _heuristic_decision(page_analyses: list[PageAnalysis]) -> PipelineDecision:
    overlaps = sum(len(page.overlaps) for page in page_analyses)
    rotated_spans = sum(1 for page in page_analyses for span in page.text_spans if span.rotation != 0)
    if overlaps == 0:
        return PipelineDecision(strategy="no_change", reason="No overlaps detected.", llm_used=False)
    if rotated_spans > 0:
        return PipelineDecision(
            strategy="local_move",
            reason="Overlaps exist; rotated text may be skipped while horizontal spans are repaired.",
            llm_used=False,
        )
    if overlaps <= 30:
        return PipelineDecision(
            strategy="local_move",
            reason="Overlap count is manageable, so nearest-safe relocation is appropriate.",
            llm_used=False,
        )
    if overlaps <= 100:
        return PipelineDecision(
            strategy="margin_relocate",
            reason="High overlap density suggests allowing margin fallback positions.",
            llm_used=False,
        )
    return PipelineDecision(
        strategy="review",
        reason="Too many overlaps for a safe fully automatic demo repair.",
        llm_used=False,
    )


def decide_strategy(page_analyses: list[PageAnalysis], settings: Settings) -> PipelineDecision:
    if not settings.use_llm_router:
        return _heuristic_decision(page_analyses)

    summary: dict[str, Any] = {
        "pages": len(page_analyses),
        "total_overlaps": sum(len(page.overlaps) for page in page_analyses),
        "per_page_overlap_counts": [len(page.overlaps) for page in page_analyses],
        "rotated_spans": sum(1 for page in page_analyses for span in page.text_spans if span.rotation != 0),
    }
    prompt = (
        "You are a routing agent for a PDF text-overlap repair pipeline. "
        "Choose one strategy from no_change, local_move, margin_relocate, review. "
        "Return strict JSON with keys strategy and reason. "
        f"Context: {json.dumps(summary)}"
    )

    try:
        response = requests.post(
            f"{settings.ollama_host.rstrip('/')}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        raw_text = body.get("response", "").strip()
        parsed = json.loads(raw_text)
        return PipelineDecision(
            strategy=parsed["strategy"],
            reason=str(parsed.get("reason", "LLM router selected the strategy.")),
            llm_used=True,
        )
    except Exception:
        return _heuristic_decision(page_analyses)

```

### `app/services/overlap_detector.py`

```python
from __future__ import annotations

from app.config import Settings
from app.schemas import OverlapRecord, PageAnalysis
from app.utils.geometry import expand_rect, intersection_area


def detect_overlaps(page_analyses: list[PageAnalysis], settings: Settings) -> list[PageAnalysis]:
    output: list[PageAnalysis] = []
    for page in page_analyses:
        overlaps: list[OverlapRecord] = []
        for span in page.text_spans:
            padded_span = expand_rect(span.bbox, settings.text_padding)
            for zone in page.drawing_zones:
                padded_zone = expand_rect(zone.bbox, settings.drawing_padding)
                area = intersection_area(padded_span, padded_zone)
                if area > settings.overlap_area_threshold:
                    overlaps.append(
                        OverlapRecord(
                            overlap_id=f"{span.span_id}__{zone.zone_id}",
                            page_number=page.page_number,
                            span_id=span.span_id,
                            zone_id=zone.zone_id,
                            area=round(area, 4),
                            text=span.text,
                            span_bbox=span.bbox,
                            zone_bbox=zone.bbox,
                        )
                    )
        output.append(
            PageAnalysis(
                page_number=page.page_number,
                page_bbox=page.page_bbox,
                text_spans=page.text_spans,
                drawing_zones=page.drawing_zones,
                overlaps=sorted(overlaps, key=lambda item: item.area, reverse=True),
            )
        )
    return output

```

### `app/services/pdf_parser.py`

```python
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from app.config import Settings
from app.schemas import DrawingZone, PageAnalysis, TextSpan
from app.utils.colors import int_to_pdf_rgb


def _extract_drawing_zones(page: fitz.Page, page_number: int) -> list[DrawingZone]:
    zones: list[DrawingZone] = []
    drawings = page.get_drawings()
    page_area = float(page.rect.width * page.rect.height)
    for idx, drawing in enumerate(drawings):
        rect = drawing.get("rect")
        if rect is None:
            continue
        bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        width = abs(bbox[2] - bbox[0])
        height = abs(bbox[3] - bbox[1])
        area = width * height
        # Ignore very large grouped boxes like title borders / page frames.
        if area > page_area * 0.35 and min(width, height) > 30:
            continue
        zones.append(DrawingZone(zone_id=f"p{page_number}_z{idx}", page_number=page_number, bbox=bbox))
    return zones


def _extract_text_spans(page: fitz.Page, page_number: int, settings: Settings) -> list[TextSpan]:
    spans: list[TextSpan] = []
    text_dict = page.get_text("dict")
    span_idx = 0
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            rotation = 0 if direction == (1, 0) else 1
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if len(text) < settings.min_text_chars:
                    continue
                bbox = tuple(float(v) for v in span.get("bbox"))
                spans.append(
                    TextSpan(
                        span_id=f"p{page_number}_s{span_idx}",
                        page_number=page_number,
                        text=text,
                        bbox=bbox,  # type: ignore[arg-type]
                        font=str(span.get("font") or "helv"),
                        fontsize=float(span.get("size") or 10.0),
                        color=int_to_pdf_rgb(span.get("color")),
                        origin=tuple(float(v) for v in span.get("origin", (bbox[0], bbox[3]))),
                        rotation=rotation,
                    )
                )
                span_idx += 1
    return spans


def parse_pdf(pdf_path: Path, settings: Settings) -> list[PageAnalysis]:
    analyses: list[PageAnalysis] = []
    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            page_bbox = (float(page.rect.x0), float(page.rect.y0), float(page.rect.x1), float(page.rect.y1))
            analyses.append(
                PageAnalysis(
                    page_number=page_index,
                    page_bbox=page_bbox,
                    text_spans=_extract_text_spans(page, page_index, settings),
                    drawing_zones=_extract_drawing_zones(page, page_index),
                )
            )
    return analyses

```

### `app/services/pdf_repair.py`

```python
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pymupdf as fitz

from app.config import Settings
from app.schemas import PageAnalysis, PipelineDecision, RepairAction, TextSpan
from app.utils.geometry import candidate_rects, distance_between_rect_centers, expand_rect, has_conflict


def _find_safe_rect(
    span: TextSpan,
    page_bbox: tuple[float, float, float, float],
    obstacles: list[tuple[float, float, float, float]],
    settings: Settings,
) -> tuple[float, float, float, float] | None:
    candidates = candidate_rects(span.bbox, page_bbox, settings.candidate_step, settings.max_search_radius)
    best: tuple[tuple[float, float, float, float], float] | None = None
    for candidate in candidates:
        padded = expand_rect(candidate, settings.text_padding)
        if has_conflict(padded, obstacles, settings.overlap_area_threshold):
            continue
        score = distance_between_rect_centers(span.bbox, candidate)
        if best is None or score < best[1]:
            best = (candidate, score)
    return best[0] if best else None


def repair_pdf(
    input_pdf_path: Path,
    page_analyses: list[PageAnalysis],
    decision: PipelineDecision,
    output_path: Path,
    settings: Settings,
) -> tuple[Path, list[RepairAction]]:
    doc = fitz.open(input_pdf_path)
    page_map = {page.page_number: page for page in page_analyses}
    actions: list[RepairAction] = []

    for page_number, analysis in page_map.items():
        if not analysis.overlaps:
            continue
        page = doc[page_number]
        overlap_by_span: dict[str, list[str]] = defaultdict(list)
        for overlap in analysis.overlaps:
            overlap_by_span[overlap.span_id].append(overlap.zone_id)

        overlapping_spans = [span for span in analysis.text_spans if span.span_id in overlap_by_span]
        static_obstacles: list[tuple[float, float, float, float]] = []
        static_obstacles.extend(expand_rect(zone.bbox, settings.drawing_padding) for zone in analysis.drawing_zones)
        static_obstacles.extend(
            expand_rect(span.bbox, settings.text_padding)
            for span in analysis.text_spans
            if span.span_id not in overlap_by_span
        )
        dynamic_obstacles = list(static_obstacles)
        pending_redactions: list[tuple[float, float, float, float]] = []
        pending_insertions: list[tuple[tuple[float, float, float, float], TextSpan, tuple[float, float, float, float]]] = []

        for span in sorted(overlapping_spans, key=lambda item: item.fontsize, reverse=True):
            if span.rotation != 0:
                actions.append(
                    RepairAction(
                        page_number=page_number,
                        span_id=span.span_id,
                        text=span.text,
                        old_bbox=span.bbox,
                        action="flagged",
                        reason="Rotated text is skipped in this demo.",
                    )
                )
                continue

            candidate = _find_safe_rect(span, analysis.page_bbox, dynamic_obstacles, settings)
            if candidate is None:
                actions.append(
                    RepairAction(
                        page_number=page_number,
                        span_id=span.span_id,
                        text=span.text,
                        old_bbox=span.bbox,
                        action="flagged",
                        reason="No safe candidate location was found.",
                    )
                )
                continue

            pending_redactions.append(span.bbox)
            pending_insertions.append((candidate, span, span.bbox))
            dynamic_obstacles.append(expand_rect(candidate, settings.text_padding))
            actions.append(
                RepairAction(
                    page_number=page_number,
                    span_id=span.span_id,
                    text=span.text,
                    old_bbox=span.bbox,
                    new_bbox=candidate,
                    action="moved",
                    reason="Moved to nearest safe rectangle.",
                )
            )

        for rect in pending_redactions:
            page.add_redact_annot(fitz.Rect(*rect), fill=False, cross_out=False)
        if pending_redactions:
            page.apply_redactions(images=0, graphics=0, text=0)

        for new_rect, span, old_rect in pending_insertions:
            dx = new_rect[0] - old_rect[0]
            dy = new_rect[1] - old_rect[1]
            new_origin = (span.origin[0] + dx, span.origin[1] + dy)
            page.insert_text(
                fitz.Point(*new_origin),
                span.text,
                fontname="helv",
                fontsize=span.fontsize,
                color=span.color,
                overlay=True,
            )
            if settings.draw_leader_lines:
                old_center = ((old_rect[0] + old_rect[2]) / 2.0, (old_rect[1] + old_rect[3]) / 2.0)
                new_center = ((new_rect[0] + new_rect[2]) / 2.0, (new_rect[1] + new_rect[3]) / 2.0)
                if abs(old_center[0] - new_center[0]) + abs(old_center[1] - new_center[1]) > settings.candidate_step:
                    page.draw_line(old_center, new_center, color=(0, 0, 0), width=0.5)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    doc.close()
    return output_path, actions

```

### `app/services/previews.py`

```python
from __future__ import annotations

import io
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

from app.schemas import PageAnalysis, RepairAction


def _render_page_image(page: fitz.Page, scale: float = 1.5) -> Image.Image:
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def write_preview_images(
    original_pdf: Path,
    corrected_pdf: Path,
    original_analysis: list[PageAnalysis],
    repair_actions: list[RepairAction],
    output_dir: Path,
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    action_map: dict[int, list[RepairAction]] = {}
    for action in repair_actions:
        action_map.setdefault(action.page_number, []).append(action)

    preview_paths: list[str] = []
    with fitz.open(original_pdf) as before_doc, fitz.open(corrected_pdf) as after_doc:
        for page in original_analysis:
            if not page.overlaps:
                continue
            page_number = page.page_number
            before_img = _render_page_image(before_doc[page_number])
            after_img = _render_page_image(after_doc[page_number])
            before_draw = ImageDraw.Draw(before_img)
            after_draw = ImageDraw.Draw(after_img)
            scale = 1.5
            for overlap in page.overlaps:
                x0, y0, x1, y1 = overlap.span_bbox
                before_draw.rectangle((x0 * scale, y0 * scale, x1 * scale, y1 * scale), outline=(255, 0, 0), width=2)
            for action in action_map.get(page_number, []):
                if action.new_bbox is not None:
                    x0, y0, x1, y1 = action.new_bbox
                    after_draw.rectangle((x0 * scale, y0 * scale, x1 * scale, y1 * scale), outline=(0, 128, 0), width=2)
            before_path = output_dir / f"page_{page_number + 1:02d}_before.png"
            after_path = output_dir / f"page_{page_number + 1:02d}_after.png"
            before_img.save(before_path)
            after_img.save(after_path)
            preview_paths.extend([str(before_path), str(after_path)])
    return preview_paths

```

### `app/state.py`

```python
from __future__ import annotations

from typing import TypedDict

from app.schemas import PageAnalysis, PipelineDecision, PipelineReport, RepairAction, TimelineEvent


class PipelineState(TypedDict, total=False):
    job_id: str
    input_path: str
    source_kind: str
    workdir: str
    normalized_pdf_path: str
    intermediate_dxf_path: str | None
    original_analysis: list[PageAnalysis]
    repaired_analysis: list[PageAnalysis]
    overlaps_before: int
    overlaps_after: int
    decision: PipelineDecision
    repair_actions: list[RepairAction]
    corrected_pdf_path: str
    report_path: str
    summary_path: str
    preview_paths: list[str]
    timeline: list[TimelineEvent]
    warnings: list[str]
    report: PipelineReport

```

### `app/utils/__init__.py`

```python
"""Utility helpers."""

```

### `app/utils/colors.py`

```python
from __future__ import annotations


def int_to_pdf_rgb(color_value: int | tuple[float, float, float] | None) -> tuple[float, float, float]:
    if color_value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(color_value, tuple):
        return tuple(float(max(0.0, min(1.0, c))) for c in color_value)  # type: ignore[return-value]
    r = (color_value >> 16) & 0xFF
    g = (color_value >> 8) & 0xFF
    b = color_value & 0xFF
    return (r / 255.0, g / 255.0, b / 255.0)

```

### `app/utils/fs.py`

```python
from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4


def make_job_dirs(base_output: Path) -> tuple[str, Path]:
    job_id = uuid4().hex[:12]
    job_dir = base_output / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_id, job_dir


def safe_copy(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst

```

### `app/utils/geometry.py`

```python
from __future__ import annotations

import math
from typing import Iterable

from shapely.geometry import box

Rect4 = tuple[float, float, float, float]


def normalize_rect(rect: Rect4) -> Rect4:
    x0, y0, x1, y1 = rect
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def rect_area(rect: Rect4) -> float:
    x0, y0, x1, y1 = normalize_rect(rect)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def expand_rect(rect: Rect4, padding: float) -> Rect4:
    x0, y0, x1, y1 = normalize_rect(rect)
    return (x0 - padding, y0 - padding, x1 + padding, y1 + padding)


def clamp_rect(rect: Rect4, page_rect: Rect4) -> Rect4:
    x0, y0, x1, y1 = normalize_rect(rect)
    px0, py0, px1, py1 = normalize_rect(page_rect)
    width = x1 - x0
    height = y1 - y0
    width = min(width, px1 - px0)
    height = min(height, py1 - py0)
    x0 = min(max(x0, px0), px1 - width)
    y0 = min(max(y0, py0), py1 - height)
    return (x0, y0, x0 + width, y0 + height)


def shift_rect(rect: Rect4, dx: float, dy: float) -> Rect4:
    x0, y0, x1, y1 = rect
    return (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def rect_center(rect: Rect4) -> tuple[float, float]:
    x0, y0, x1, y1 = normalize_rect(rect)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def distance_between_rect_centers(a: Rect4, b: Rect4) -> float:
    ax, ay = rect_center(a)
    bx, by = rect_center(b)
    return math.dist((ax, ay), (bx, by))


def intersection_area(a: Rect4, b: Rect4) -> float:
    return box(*normalize_rect(a)).intersection(box(*normalize_rect(b))).area


def has_conflict(rect: Rect4, obstacles: Iterable[Rect4], threshold: float) -> bool:
    for obstacle in obstacles:
        if intersection_area(rect, obstacle) > threshold:
            return True
    return False


def candidate_rects(base_rect: Rect4, page_rect: Rect4, step: int, max_radius: int) -> list[Rect4]:
    candidates: list[Rect4] = []
    candidates.append(clamp_rect(base_rect, page_rect))
    directions = [
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1),
        (1, 1),
        (-1, 1),
        (1, -1),
        (-1, -1),
    ]
    for radius in range(step, max_radius + step, step):
        for dx, dy in directions:
            candidates.append(clamp_rect(shift_rect(base_rect, dx * radius, dy * radius), page_rect))
        for i in range(12):
            theta = (2 * math.pi * i) / 12.0
            dx = math.cos(theta) * radius
            dy = math.sin(theta) * radius
            candidates.append(clamp_rect(shift_rect(base_rect, dx, dy), page_rect))
    # margin candidates last
    x0, y0, x1, y1 = normalize_rect(base_rect)
    w = x1 - x0
    h = y1 - y0
    px0, py0, px1, py1 = normalize_rect(page_rect)
    candidates.extend([
        clamp_rect((px0 + 8, py0 + 8, px0 + 8 + w, py0 + 8 + h), page_rect),
        clamp_rect((px1 - w - 8, py0 + 8, px1 - 8, py0 + 8 + h), page_rect),
        clamp_rect((px0 + 8, py1 - h - 8, px0 + 8 + w, py1 - 8), page_rect),
        clamp_rect((px1 - w - 8, py1 - h - 8, px1 - 8, py1 - 8), page_rect),
    ])
    unique: list[Rect4] = []
    seen: set[tuple[int, int, int, int]] = set()
    for rect in candidates:
        key = tuple(int(round(v)) for v in rect)
        if key not in seen:
            seen.add(key)
            unique.append(rect)
    return unique

```

### `app/utils/progress.py`

```python
from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from app.schemas import TimelineEvent


@dataclass
class ProgressTracker:
    console: Console = field(default_factory=Console)
    start_time: float = field(default_factory=time.perf_counter)
    events: list[TimelineEvent] = field(default_factory=list)

    def log(self, stage: str, agent: str, message: str) -> None:
        elapsed = time.perf_counter() - self.start_time
        self.events.append(
            TimelineEvent(
                stage=stage,
                agent=agent,
                message=message,
                elapsed_seconds=round(elapsed, 3),
            )
        )
        self.console.print(f"[bold cyan]{stage}[/] | [green]{agent}[/] | {message}")

    def print_summary(self) -> None:
        table = Table(title="Pipeline Timeline")
        table.add_column("t+s", justify="right")
        table.add_column("Stage")
        table.add_column("Agent")
        table.add_column("Message")
        for event in self.events:
            table.add_row(f"{event.elapsed_seconds:.3f}", event.stage, event.agent, event.message)
        self.console.print(table)

```

### `outputs/04c8ecdd5da5/report.json`

```json
{
  "job_id": "04c8ecdd5da5",
  "input_path": "/mnt/data/building_overlap_agent/inputs/demo_overlap.pdf",
  "source_kind": "pdf",
  "normalized_pdf_path": "/mnt/data/building_overlap_agent/outputs/04c8ecdd5da5/original.pdf",
  "corrected_pdf_path": "/mnt/data/building_overlap_agent/outputs/04c8ecdd5da5/corrected.pdf",
  "overlaps_before": 5,
  "overlaps_after": 0,
  "decision": {
    "strategy": "local_move",
    "reason": "Overlap count is manageable, so nearest-safe relocation is appropriate.",
    "llm_used": false
  },
  "repair_actions": [
    {
      "page_number": 0,
      "span_id": "p0_s2",
      "text": "WALL NOTE",
      "old_bbox": [
        280.0,
        91.0999984741211,
        349.3479919433594,
        107.58799743652344
      ],
      "new_bbox": [
        280.0,
        79.0999984741211,
        349.3479919433594,
        95.58799743652344
      ],
      "action": "moved",
      "reason": "Moved to nearest safe rectangle."
    },
    {
      "page_number": 0,
      "span_id": "p0_s3",
      "text": "DIM 4500",
      "old_bbox": [
        288.0,
        291.1000061035156,
        340.0199890136719,
        307.5880126953125
      ],
      "new_bbox": [
        288.0,
        303.1000061035156,
        340.0199890136719,
        319.5880126953125
      ],
      "action": "moved",
      "reason": "Moved to nearest safe rectangle."
    },
    {
      "page_number": 0,
      "span_id": "p0_s4",
      "text": "PIPE SHAFT",
      "old_bbox": [
        606.0,
        247.10000610351562,
        676.0200805664062,
        263.5880126953125
      ],
      "new_bbox": [
        630.0,
        247.10000610351562,
        700.0200805664062,
        263.5880126953125
      ],
      "action": "moved",
      "reason": "Moved to nearest safe rectangle."
    }
  ],
  "preview_paths": [
    "/mnt/data/building_overlap_agent/outputs/04c8ecdd5da5/previews/page_01_before.png",
    "/mnt/data/building_overlap_agent/outputs/04c8ecdd5da5/previews/page_01_after.png"
  ],
  "warnings": [],
  "timeline": [
    {
      "stage": "pipeline",
      "agent": "orchestrator",
      "message": "Job started: 04c8ecdd5da5",
      "elapsed_seconds": 0.0
    },
    {
      "stage": "ingest",
      "agent": "ingestion_agent",
      "message": "Normalizing input: /mnt/data/building_overlap_agent/inputs/demo_overlap.pdf",
      "elapsed_seconds": 0.004
    },
    {
      "stage": "ingest",
      "agent": "ingestion_agent",
      "message": "Working PDF ready: /mnt/data/building_overlap_agent/outputs/04c8ecdd5da5/original.pdf",
      "elapsed_seconds": 0.007
    },
    {
      "stage": "analysis",
      "agent": "analysis_agent",
      "message": "Parsing PDF objects and computing overlaps",
      "elapsed_seconds": 0.008
    },
    {
      "stage": "analysis",
      "agent": "analysis_agent",
      "message": "Detected 5 overlaps",
      "elapsed_seconds": 0.018
    },
    {
      "stage": "decision",
      "agent": "decision_agent",
      "message": "Strategy=local_move | Overlap count is manageable, so nearest-safe relocation is appropriate.",
      "elapsed_seconds": 0.019
    },
    {
      "stage": "repair",
      "agent": "repair_agent",
      "message": "Applying PDF text relocation repair",
      "elapsed_seconds": 0.019
    },
    {
      "stage": "repair",
      "agent": "repair_agent",
      "message": "Repair actions created: 3",
      "elapsed_seconds": 0.374
    },
    {
      "stage": "validate",
      "agent": "validation_agent",
      "message": "Re-running overlap detection on corrected PDF",
      "elapsed_seconds": 0.375
    },
    {
      "stage": "validate",
      "agent": "validation_agent",
      "message": "Remaining overlaps: 0",
      "elapsed_seconds": 0.382
    },
    {
      "stage": "report",
      "agent": "report_agent",
      "message": "Writing report and preview artifacts",
      "elapsed_seconds": 0.382
    }
  ]
}
```

### `outputs/04c8ecdd5da5/summary.txt`

```
job_id: 04c8ecdd5da5
input: /mnt/data/building_overlap_agent/inputs/demo_overlap.pdf
source_kind: pdf
overlaps_before: 5
overlaps_after: 0
decision: local_move
reason: Overlap count is manageable, so nearest-safe relocation is appropriate.
```

### `requirements.txt`

```
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pydantic>=2.7.0
pymupdf>=1.24.9
shapely>=2.0.4
rich>=13.7.1
requests>=2.32.0
python-multipart>=0.0.9
Pillow>=10.4.0
ezdxf>=1.4.0

```

### `scripts/dxf_to_pdf.py`

```python
from __future__ import annotations

import sys
from pathlib import Path

from app.services.dxf_tools import render_dxf_to_pdf


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python scripts/dxf_to_pdf.py <input.dxf> <output.pdf>")
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    render_dxf_to_pdf(input_path, output_path)
    print(output_path)


if __name__ == "__main__":
    main()

```

### `scripts/generate_demo_pdf.py`

```python
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz


def build_demo_pdf(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)

    # Drawing frame and wall lines
    page.draw_rect(fitz.Rect(60, 60, 780, 520), color=(0, 0, 0), width=1.2)
    page.draw_line((120, 100), (120, 460), color=(0, 0, 0), width=2)
    page.draw_line((120, 100), (500, 100), color=(0, 0, 0), width=2)
    page.draw_line((500, 100), (500, 300), color=(0, 0, 0), width=2)
    page.draw_line((120, 300), (500, 300), color=(0, 0, 0), width=2)
    page.draw_line((300, 100), (300, 300), color=(0, 0, 0), width=2)
    page.draw_line((120, 460), (700, 460), color=(0, 0, 0), width=2)
    page.draw_line((620, 180), (620, 460), color=(0, 0, 0), width=2)
    page.draw_circle((210, 200), 18, color=(0, 0, 0), width=1.2)

    # Good labels (clear of geometry)
    page.insert_text((140, 130), "ROOM A", fontsize=12, fontname="helv", color=(0, 0, 0))
    page.insert_text((520, 130), "CORRIDOR", fontsize=12, fontname="helv", color=(0, 0, 0))

    # Intentionally overlapping labels
    page.insert_text((280, 104), "WALL NOTE", fontsize=12, fontname="helv", color=(0, 0, 0))
    page.insert_text((288, 304), "DIM 4500", fontsize=12, fontname="helv", color=(0, 0, 0))
    page.insert_text((606, 260), "PIPE SHAFT", fontsize=12, fontname="helv", color=(0, 0, 0))

    doc.save(output_path)
    doc.close()
    return output_path


if __name__ == "__main__":
    out = build_demo_pdf(Path("inputs/demo_overlap.pdf"))
    print(f"Demo PDF written to: {out}")

```
