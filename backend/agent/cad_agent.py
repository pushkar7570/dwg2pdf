# backend/agent/cad_agent.py

import os
import math
import time
from typing import List, Tuple, Dict
from dotenv import load_dotenv

from .tools import (
    ALL_TOOLS,
    initialize_session,
    get_committed_positions,
)
from .prompts import SYSTEM_PROMPT, REPOSITION_PROMPT_TEMPLATE
from pipeline.detector import DetectionResult, OverlapConflict, PageZones
from pipeline.parser import ParsedDocument
from pipeline.utils import get_logger

load_dotenv()
logger = get_logger(__name__)


# ── LLM Factory ──────────────────────────────────────────────────────────────

def _get_llm():
    """
    Get Groq LLM with automatic model fallback chain.
    All models are free tier on Groq.
    """
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "\nGROQ_API_KEY not set in .env\n"
            "Get a free key at: https://console.groq.com\n"
        )

    from langchain_groq import ChatGroq
    from langchain_core.messages import HumanMessage

    env_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    fallback_chain = [
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "llama3-groq-8b-8192-tool-use-preview",
        "llama3-groq-70b-8192-tool-use-preview",
        "gemma2-9b-it",
        "gemma-7b-it",
        "mixtral-8x7b-32768",
    ]

    # Put env model first without duplicating
    models_to_try = [env_model] + [
        m for m in fallback_chain if m != env_model
    ]

    last_error = None
    for model_name in models_to_try:
        try:
            logger.info(f"Trying Groq model: {model_name}")
            llm = ChatGroq(
                model=model_name,
                groq_api_key=os.getenv("GROQ_API_KEY"),
                temperature=0.1,
                max_tokens=2048,
            )
            llm.invoke([HumanMessage(content="Say OK")])
            logger.info(f"LLM ready: Groq/{model_name}")
            return llm

        except Exception as e:
            err = str(e)
            if any(x in err for x in ["decommissioned", "not found", "404"]):
                logger.warning(f"  {model_name} decommissioned, trying next...")
            elif any(x in err for x in ["rate_limit", "429"]):
                logger.warning(f"  Rate limited on {model_name}, waiting 10s...")
                time.sleep(10)
            else:
                logger.warning(f"  {model_name} failed: {err[:60]}")
            last_error = e

    raise RuntimeError(
        f"All Groq models failed. Last error: {last_error}\n"
        "Check https://console.groq.com/docs/models for active models."
    )


# ── Agent Builder ─────────────────────────────────────────────────────────────

def _build_agent_executor(llm):
    """Build ReAct AgentExecutor."""
    from langchain_core.prompts import PromptTemplate
    from langchain.agents import create_react_agent, AgentExecutor

    template = """Answer the following questions as best you can.
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

    prompt   = PromptTemplate.from_template(template)
    agent    = create_react_agent(llm=llm, tools=ALL_TOOLS, prompt=prompt)
    executor = AgentExecutor(
        agent=agent,
        tools=ALL_TOOLS,
        verbose=True,
        max_iterations=8,
        max_execution_time=60,
        handle_parsing_errors=True,
    )

    logger.info("ReAct agent executor ready.")
    return executor


# ── Helpers ───────────────────────────────────────────────────────────────────

def _conflict_summary(conflict: OverlapConflict) -> str:
    lines = []
    for i, d in enumerate(conflict.conflicting_drawings[:5], 1):
        lines.append(
            f"  [{i}] {d.element_type} "
            f"bbox={[round(v,1) for v in d.bbox]} "
            f"filled={d.is_filled}"
        )
    return "\n".join(lines) if lines else "  (details unavailable)"


def _nearby_text(conflict, all_text_blocks, radius=100.0) -> str:
    nearby = []
    cx, cy = conflict.text_block.center
    for tb in all_text_blocks:
        if tb.block_id == conflict.text_block.block_id:
            continue
        tx, ty = tb.center
        if math.sqrt((tx - cx) ** 2 + (ty - cy) ** 2) < radius:
            nearby.append(f'"{tb.text}"@({tx:.0f},{ty:.0f})')
    return ", ".join(nearby[:5]) if nearby else "none"


def _zones_to_session_dicts(
    page_zones: List[PageZones],
) -> Tuple[Dict, Dict]:
    """Convert PageZones list to dicts keyed by page_no."""
    filled = {z.page_no: z.filled_zones for z in page_zones}
    free   = {z.page_no: z.free_zones   for z in page_zones}
    return filled, free


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_overlap_fixing_agent(
    detection_result: DetectionResult,
    parsed_doc      : ParsedDocument,
) -> List[dict]:
    """
    Zone-aware ReAct agent that fixes all detected overlaps.

    Key improvements over previous version:
    - Agent receives filled_zones and free_zones for each page
    - analyze_free_spaces only returns positions outside filled zones
    - Fallback also respects zone constraints
    """
    if not detection_result.has_overlaps:
        logger.info("No overlaps to fix.")
        return []

    logger.info(
        f"Agent starting: "
        f"{detection_result.total_conflicts} overlap(s) to fix."
    )

    # ── Prepare session ──────────────────────────────────────────
    page_dims      = [(p.width, p.height) for p in parsed_doc.pages]
    drawing_bboxes = [d.bbox for p in parsed_doc.pages for d in p.drawing_elements]
    text_bboxes    = [t.bbox for p in parsed_doc.pages for t in p.text_blocks]
    all_text       = parsed_doc.all_text_blocks

    filled_zones, free_zones = _zones_to_session_dicts(
        detection_result.page_zones
    )

    initialize_session(
        page_dimensions=page_dims,
        text_bboxes=text_bboxes,
        drawing_bboxes=drawing_bboxes,
        filled_zones=filled_zones,
        free_zones=free_zones,
    )

    # ── Build agent ──────────────────────────────────────────────
    agent_executor = None
    try:
        llm            = _get_llm()
        agent_executor = _build_agent_executor(llm)
    except Exception as e:
        logger.error(f"Agent setup failed: {e}")
        logger.warning("Using deterministic fallback for all conflicts.")

    # ── Process each conflict ────────────────────────────────────
    for idx, conflict in enumerate(detection_result.conflicts, 1):
        label = f"[{idx}/{detection_result.total_conflicts}]"
        logger.info(f"{label} Fixing: '{conflict.text}'")

        fixed = False

        if agent_executor:
            try:
                _run_agent_for_conflict(
                    executor=agent_executor,
                    conflict=conflict,
                    page_dims=page_dims,
                    all_text_blocks=all_text,
                    free_zones=free_zones,
                    filled_zones=filled_zones,
                )
                fixed = True
            except Exception as e:
                logger.warning(f"{label} Agent failed: {e} → fallback")

        if not fixed:
            _fallback_fix(conflict, free_zones)

        # Brief pause between Groq API calls
        if agent_executor and idx < detection_result.total_conflicts:
            time.sleep(1)

    results = get_committed_positions()
    logger.info(
        f"Agent complete: "
        f"{len(results)}/{detection_result.total_conflicts} fixed."
    )
    return results


def _run_agent_for_conflict(
    executor       : object,
    conflict       : OverlapConflict,
    page_dims      : List[Tuple],
    all_text_blocks: List,
    free_zones     : Dict,
    filled_zones   : Dict,
):
    """Build task and invoke agent for one conflict."""
    pn     = conflict.page_no
    pw, ph = page_dims[pn] if pn < len(page_dims) else (800, 600)
    orig   = conflict.current_bbox
    tw     = round(conflict.text_block.width,  1)
    th     = round(conflict.text_block.height, 1)
    cx     = round((orig[0] + orig[2]) / 2,    1)
    cy     = round((orig[1] + orig[3]) / 2,    1)

    fz_list = free_zones.get(pn, [])
    fz_str  = (
        str([list(fz) for fz in fz_list[:3]])
        if fz_list
        else "none found (use page margins)"
    )

    task = REPOSITION_PROMPT_TEMPLATE.format(
        text=conflict.text,
        page_no=pn,
        current_bbox=[round(v, 1) for v in orig],
        text_width=tw,
        text_height=th,
        font_size=conflict.text_block.font_size,
        page_width=round(pw),
        page_height=round(ph),
        conflict_type=conflict.conflict_type,
        conflict_details=_conflict_summary(conflict),
        nearby_text=_nearby_text(conflict, all_text_blocks),
        cx=cx,
        cy=cy,
    ) + f"\n\nKNOWN FREE ZONES ON PAGE {pn}: {fz_str}"

    result = executor.invoke({"input": task})
    logger.debug(f"  Agent: {str(result.get('output',''))[:150]}")


def _fallback_fix(
    conflict    : OverlapConflict,
    free_zones  : Dict,
):
    """
    Deterministic fallback: tries positions in free zones first,
    then tries offset candidates around original position.
    """
    from agent.tools import _session_state
    from shapely.geometry import box as shapely_box

    orig  = conflict.current_bbox
    tw    = orig[2] - orig[0]
    th    = orig[3] - orig[1]
    g     = 12
    pn    = conflict.page_no

    dims   = _session_state.get("page_dimensions", [])
    pw, ph = dims[pn] if pn < len(dims) else (800, 600)

    occupied = (
        [shapely_box(*b) for b in _session_state.get("all_drawing_bboxes", [])]
        + [
            shapely_box(*p["new_bbox"])
            for p in _session_state.get("committed_positions", [])
            if p.get("page_no") == pn
        ]
    )

    forbidden = [
        shapely_box(*b)
        for b in _session_state.get("filled_zones", {}).get(pn, [])
    ]

    def _is_safe(nx, ny):
        """Check if position is free and not in forbidden zone."""
        if nx < 2 or ny < 2 or (nx + tw) > pw - 2 or (ny + th) > ph - 2:
            return False
        probe = shapely_box(nx - 3, ny - 3, nx + tw + 3, ny + th + 3)
        if any(probe.intersects(fz) for fz in forbidden):
            return False
        if any(probe.intersects(occ) for occ in occupied):
            return False
        return True

    def _commit(nx, ny, strategy):
        _session_state["committed_positions"].append({
            "page_no"      : pn,
            "text"         : conflict.text,
            "original_bbox": list(orig),
            "new_bbox"     : [nx, ny, nx + tw, ny + th],
            "font_size"    : conflict.text_block.font_size,
            "color"        : list(conflict.text_block.color),
        })
        logger.info(
            f"  Fallback [{strategy}]: "
            f"'{conflict.text}' → ({nx:.0f},{ny:.0f})"
        )

    # ── Strategy 1: try positions inside known free zones ────────
    fz_list = free_zones.get(pn, [])
    for fz in fz_list:
        nx = fz[0] + g
        ny = fz[1] + g
        if _is_safe(nx, ny):
            _commit(nx, ny, "free_zone")
            return

    # ── Strategy 2: offsets around original ──────────────────────
    candidates = [
        (orig[0],           orig[1] - th - g),       # above
        (orig[0],           orig[3] + g),             # below
        (orig[2] + g,       orig[1]),                 # right
        (orig[0] - tw - g,  orig[1]),                 # left
        (orig[0],           orig[1] - 2*(th + g)),    # 2x above
        (orig[0] + 20,      orig[1] - th - g),        # above-right
        (orig[0] - 20,      orig[1] - th - g),        # above-left
        (orig[2] + g,       orig[3] + g),             # bottom-right
    ]

    for nx, ny in candidates:
        if _is_safe(nx, ny):
            _commit(nx, ny, "offset")
            return

    # ── Emergency: nudge up 20px ─────────────────────────────────
    logger.warning(f"  Emergency nudge for '{conflict.text}'")
    _commit(orig[0], orig[1] - 20, "emergency")
