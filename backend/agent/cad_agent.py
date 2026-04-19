import os
import math
import time
from typing import List, Tuple
from dotenv import load_dotenv

from .tools import (
    ALL_TOOLS,
    initialize_session,
    get_committed_positions
)
from .prompts import SYSTEM_PROMPT
from pipeline.detector import DetectionResult, OverlapConflict
from pipeline.parser import ParsedDocument
from pipeline.utils import get_logger

load_dotenv()
logger = get_logger(__name__)


# ── LLM Factory ──────────────────────────────────────────────────────────────

def _get_llm():
    """
    Get Groq LLM.
    Free tier: 14,400 requests/day, no quota issues for demo.
    """
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "\nGROQ_API_KEY not set in .env\n"
            "Get free key at: https://console.groq.com\n"
            "Then add to .env:\n"
            "  GROQ_API_KEY=gsk_...\n"
            "  LLM_PROVIDER=groq\n"
            "  GROQ_MODEL=llama3-8b-8192\n"
        )

    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import HumanMessage

        model_name = os.getenv("GROQ_MODEL", "llama3-8b-8192")
        logger.info(f"Connecting to Groq model: {model_name}")

        llm = ChatGroq(
            model=model_name,
            groq_api_key=os.getenv("GROQ_API_KEY"),
            temperature=0.1,
            max_tokens=2048,
        )

        # Smoke test
        llm.invoke([HumanMessage(content="Say OK")])
        logger.info(f"LLM ready: Groq/{model_name}")
        return llm

    except Exception as e:
        raise RuntimeError(f"Groq init failed: {e}")


# ── Agent Builder ─────────────────────────────────────────────────────────────

def _build_agent_executor(llm):
    """
    Build ReAct AgentExecutor using LangChain.
    """
    from langchain_core.prompts import PromptTemplate
    from langchain.agents import create_react_agent, AgentExecutor

    react_template = """Answer the following questions as best you can.
You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin!

Question: {input}
Thought:{agent_scratchpad}"""

    prompt = PromptTemplate.from_template(react_template)

    agent = create_react_agent(
        llm=llm,
        tools=ALL_TOOLS,
        prompt=prompt
    )

    executor = AgentExecutor(
        agent=agent,
        tools=ALL_TOOLS,
        verbose=True,
        max_iterations=8,
        max_execution_time=60,
        handle_parsing_errors=True,
    )

    logger.info("ReAct agent executor built successfully")
    return executor


# ── Helper Functions ──────────────────────────────────────────────────────────

def _build_conflict_summary(conflict: OverlapConflict) -> str:
    lines = []
    for i, drawing in enumerate(conflict.conflicting_drawings[:5], 1):
        bbox_r = [round(v, 1) for v in drawing.bbox]
        lines.append(f"  [{i}] {drawing.element_type} at {bbox_r}")
    return "\n".join(lines) if lines else "  (no drawing details)"


def _find_nearby_text(conflict, all_text_blocks, radius=100.0) -> str:
    nearby = []
    cx, cy = conflict.text_block.center
    for tb in all_text_blocks:
        if tb.block_id == conflict.text_block.block_id:
            continue
        tx, ty = tb.center
        if math.sqrt((tx - cx) ** 2 + (ty - cy) ** 2) < radius:
            nearby.append(f'"{tb.text}"@({tx:.0f},{ty:.0f})')
    return ", ".join(nearby[:5]) if nearby else "none"


# ── Main Public Function ──────────────────────────────────────────────────────

def run_overlap_fixing_agent(
    detection_result: DetectionResult,
    parsed_doc: ParsedDocument
) -> List[dict]:
    """
    Entry point: fix all detected overlaps using ReAct agent.
    Falls back to deterministic fixes if LLM fails.
    """
    if not detection_result.has_overlaps:
        logger.info("No overlaps to fix.")
        return []

    logger.info(
        f"Starting agent for "
        f"{detection_result.total_conflicts} overlap(s)..."
    )

    # ── Prepare session ──────────────────────────────────────────────────────
    page_dims       = [(p.width, p.height) for p in parsed_doc.pages]
    drawing_bboxes  = [d.bbox for p in parsed_doc.pages for d in p.drawing_elements]
    text_bboxes     = [t.bbox for p in parsed_doc.pages for t in p.text_blocks]
    all_text_blocks = parsed_doc.all_text_blocks

    initialize_session(
        page_dimensions=page_dims,
        text_bboxes=text_bboxes,
        drawing_bboxes=drawing_bboxes
    )

    # ── Build agent ──────────────────────────────────────────────────────────
    agent_executor = None
    try:
        llm = _get_llm()
        agent_executor = _build_agent_executor(llm)
    except Exception as e:
        logger.error(f"Agent setup failed: {e}")
        logger.warning("Using deterministic fallback for all items.")

    # ── Process each conflict ────────────────────────────────────────────────
    for idx, conflict in enumerate(detection_result.conflicts, 1):
        logger.info(
            f"  [{idx}/{detection_result.total_conflicts}] "
            f"Processing: '{conflict.text}'"
        )

        fixed = False

        if agent_executor:
            try:
                _run_agent_for_conflict(
                    agent_executor=agent_executor,
                    conflict=conflict,
                    page_dims=page_dims,
                    all_text_blocks=all_text_blocks,
                )
                fixed = True
            except Exception as e:
                logger.warning(
                    f"  Agent failed for '{conflict.text}': {e}"
                    f" → using fallback"
                )

        if not fixed:
            _apply_fallback_fix(conflict)

        # Small delay between Groq calls to be nice to rate limits
        if agent_executor and idx < detection_result.total_conflicts:
            time.sleep(1)

    results = get_committed_positions()
    logger.info(
        f"Agent complete. "
        f"{len(results)}/{detection_result.total_conflicts} repositioned."
    )
    return results


def _run_agent_for_conflict(
    agent_executor,
    conflict: OverlapConflict,
    page_dims: List[Tuple],
    all_text_blocks: List,
):
    """Invoke ReAct agent for a single overlapping text block."""
    pn    = conflict.page_no
    pw, ph = page_dims[pn] if pn < len(page_dims) else (800, 600)

    orig  = conflict.current_bbox
    tw    = round(conflict.text_block.width, 1)
    th    = round(conflict.text_block.height, 1)
    cx    = round((orig[0] + orig[2]) / 2, 1)
    cy    = round((orig[1] + orig[3]) / 2, 1)

    task = f"""Fix this overlapping text label in a 2D CAD drawing.

TEXT INFO:
  text         = "{conflict.text}"
  page_no      = {pn}
  current_bbox = {[round(v, 1) for v in orig]}
  width        = {tw}  height = {th}
  font_size    = {conflict.text_block.font_size}
  page_size    = {pw:.0f} x {ph:.0f}
  overlap      = {conflict.max_overlap_ratio:.0%}

CONFLICTS:
{_build_conflict_summary(conflict)}

NEARBY LABELS: {_find_nearby_text(conflict, all_text_blocks)}

STEPS:
1. Call get_page_context with page_no={pn}
2. Call analyze_free_spaces with page_no={pn}, center_x={cx}, center_y={cy}
3. Pick the closest free position to the original
4. Call validate_position to confirm it is clear
   Use text_width={tw} and text_height={th}
5. Call commit_repositioning to save the fix

Keep the text as close to its original position as possible.
The text must NOT overlap any drawing elements after moving.
"""

    result = agent_executor.invoke({"input": task})
    logger.debug(
        f"  Agent output: "
        f"{str(result.get('output', ''))[:150]}"
    )


def _apply_fallback_fix(conflict: OverlapConflict):
    """
    Deterministic fallback: tries 7 candidate positions and
    picks the first one that does not overlap anything.
    """
    from agent.tools import _session_state
    from shapely.geometry import box as shapely_box

    orig = conflict.current_bbox
    tw   = orig[2] - orig[0]
    th   = orig[3] - orig[1]
    g    = 10

    candidates = [
        (orig[0],            orig[1] - th - g),
        (orig[0],            orig[3] + g),
        (orig[2] + g,        orig[1]),
        (orig[0] - tw - g,   orig[1]),
        (orig[0],            orig[1] - 2 * (th + g)),
        (orig[0] + 20,       orig[1] - th - g),
        (orig[0] - 20,       orig[1] - th - g),
    ]

    dims   = _session_state.get("page_dimensions", [])
    pw, ph = (
        dims[conflict.page_no]
        if conflict.page_no < len(dims)
        else (800, 600)
    )

    occupied = (
        [shapely_box(*b) for b in _session_state.get("all_drawing_bboxes", [])]
        + [
            shapely_box(*p["new_bbox"])
            for p in _session_state.get("committed_positions", [])
            if p.get("page_no") == conflict.page_no
        ]
    )

    for nx, ny in candidates:
        if nx < 2 or ny < 2 or (nx + tw) > pw - 2 or (ny + th) > ph - 2:
            continue
        probe = shapely_box(nx - 3, ny - 3, nx + tw + 3, ny + th + 3)
        if not any(probe.intersects(occ) for occ in occupied):
            _session_state["committed_positions"].append({
                "page_no"      : conflict.page_no,
                "text"         : conflict.text,
                "original_bbox": list(orig),
                "new_bbox"     : [nx, ny, nx + tw, ny + th],
                "font_size"    : conflict.text_block.font_size,
                "color"        : list(conflict.text_block.color),
            })
            logger.info(
                f"  Fallback OK: '{conflict.text}' "
                f"→ ({nx:.0f}, {ny:.0f})"
            )
            return

    # Emergency nudge
    logger.warning(f"  Emergency nudge for '{conflict.text}'")
    _session_state["committed_positions"].append({
        "page_no"      : conflict.page_no,
        "text"         : conflict.text,
        "original_bbox": list(orig),
        "new_bbox"     : [orig[0], orig[1] - 20, orig[2], orig[3] - 20],
        "font_size"    : conflict.text_block.font_size,
        "color"        : list(conflict.text_block.color),
    })