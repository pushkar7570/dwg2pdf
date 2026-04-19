# frontend/app.py

import streamlit as st
import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://localhost:8000")

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CAD Text Overlap Fixer",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
        padding: 2rem;
        border-radius: 10px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
    }
    .status-card {
        padding: 1rem;
        border-radius: 8px;
        margin: 0.5rem 0;
    }
    .metric-box {
        background: #f0f2f6;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .step-active {
        color: #2a5298;
        font-weight: bold;
    }
    .step-done {
        color: #28a745;
    }
    .step-pending {
        color: #6c757d;
    }
</style>
""", unsafe_allow_html=True)


# ── Header ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>🏗️ CAD Text Overlap Fixer</h1>
    <p>Agentic AI Pipeline for 2D Building Drawing Cleanup</p>
    <small>Powered by LangChain ReAct Agent + Gemini Flash</small>
</div>
""", unsafe_allow_html=True)


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

    api_url = st.text_input("API URL", value=API_URL)

    st.divider()
    st.subheader("ℹ️ How It Works")
    st.markdown("""
    **Pipeline Stages:**
    
    1️⃣ **Ingest** - Accept DWG/DXF/PDF  
    2️⃣ **Convert** - DWG/DXF → PDF  
    3️⃣ **Parse** - Extract text + drawings  
    4️⃣ **Detect** - Find text-drawing overlaps  
    5️⃣ **AI Agent** - ReAct agent repositions text  
    6️⃣ **Rebuild** - Output corrected PDF  
    
    **Supported Files:**
    - `.dwg` - AutoCAD Drawing
    - `.dxf` - Drawing Exchange Format  
    - `.pdf` - Direct PDF input
    """)

    st.divider()
    st.subheader("🔑 API Status")

    # Check backend health
    try:
        r = requests.get(f"{api_url}/health", timeout=3)
        if r.status_code == 200:
            health = r.json()
            st.success("✅ Backend Online")
            st.json({
                "LLM": health.get("llm_provider", "unknown"),
                "Gemini": "✅" if health.get("gemini_configured") else "❌",
                "Groq": "✅" if health.get("groq_configured") else "❌"
            })
    except Exception:
        st.error("❌ Backend Offline")
        st.caption("Start backend: `uvicorn main:app --reload`")


# ── Main Content ─────────────────────────────────────────────────────────────
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📁 Upload CAD File")

    uploaded_file = st.file_uploader(
        "Choose your CAD drawing",
        type=["dwg", "dxf", "pdf"],
        help="Upload DWG, DXF (recommended), or PDF file"
    )

    if uploaded_file:
        file_size_kb = len(uploaded_file.getvalue()) / 1024

        st.markdown(f"""
        <div class="metric-box">
            <b>📄 {uploaded_file.name}</b><br>
            <small>Size: {file_size_kb:.1f} KB | Type: {uploaded_file.type}</small>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        # Mode selection
        mode = st.radio(
            "Select Mode",
            ["🔧 Fix Overlaps", "🔍 Detect Only"],
            help=(
                "Fix: Full pipeline with AI correction | "
                "Detect: Just analyze, no changes"
            )
        )

        process_btn = st.button(
            "▶️ Start Processing",
            type="primary",
            use_container_width=True
        )

with col2:
    st.subheader("📊 Processing Status")

    if not uploaded_file:
        st.info("👆 Upload a file to get started")
        st.markdown("""
        **What this tool fixes:**
        - Text labels overlapping wall lines
        - Room numbers on top of structural elements  
        - Dimensions crossing drawing paths
        - Annotations buried under drawing details
        """)

    elif "process_btn" in dir() and process_btn:

        # ── Processing UI ────────────────────────────────────────────────────
        status_container = st.empty()
        progress_bar = st.progress(0)
        log_container = st.empty()

        logs = []

        def update_status(step: str, progress: int, log: str = ""):
            status_container.markdown(f"**Status:** {step}")
            progress_bar.progress(progress)
            if log:
                logs.append(f"• {log}")
                log_container.markdown("\n".join(logs[-10:]))  # Show last 10 logs

        try:
            # Determine endpoint
            endpoint = (
                f"{api_url}/detect-only"
                if mode == "🔍 Detect Only"
                else f"{api_url}/process"
            )

            update_status("📤 Uploading file...", 10, f"Sending {uploaded_file.name}")
            time.sleep(0.3)

            update_status("⚙️ Converting to PDF...", 25, "Running DWG/DXF → PDF conversion")

            start_time = time.time()

            response = requests.post(
                endpoint,
                files={"file": (uploaded_file.name, uploaded_file.getvalue())},
                timeout=180
            )

            elapsed = time.time() - start_time

            if response.status_code == 200:
                update_status("✅ Complete!", 100)

                if mode == "🔍 Detect Only":
                    # Show detection results as JSON
                    result = response.json()
                    st.success("🔍 Detection Complete!")

                    detection = result.get("detection", {})
                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        st.metric("Text Blocks", result.get("total_text_blocks", 0))
                    with col_b:
                        st.metric("Drawing Elements", result.get("total_drawing_elements", 0))
                    with col_c:
                        overlaps = detection.get("total_conflicts", 0)
                        st.metric("Overlaps Found", overlaps, delta="⚠️" if overlaps > 0 else "✅")

                    if detection.get("has_overlaps"):
                        st.warning(f"⚠️ {detection.get('summary', '')}")
                        with st.expander("View Conflict Details"):
                            st.json(detection.get("conflicts", []))
                    else:
                        st.success("✅ " + detection.get("summary", "No overlaps!"))

                else:
                    # Fix mode - download corrected PDF
                    headers = response.headers
                    status = headers.get("X-Status", "unknown")
                    overlap_fixed = headers.get("X-Overlap-Fixed", "false") == "true"

                    if status == "no_overlap_found":
                        st.success("✅ No overlaps found! Drawing is clean.")
                        st.info("Returning the converted PDF as-is.")
                    else:
                        n_fixed = headers.get("X-Overlaps-Fixed", "?")
                        st.success(f"✅ Fixed {n_fixed} overlapping text block(s)!")

                    st.markdown(f"⏱️ Processing time: **{elapsed:.1f}s**")

                    output_name = f"corrected_{uploaded_file.name.rsplit('.', 1)[0]}.pdf"

                    st.download_button(
                        label="📥 Download Corrected PDF",
                        data=response.content,
                        file_name=output_name,
                        mime="application/pdf",
                        use_container_width=True
                    )

            else:
                try:
                    error_detail = response.json().get("detail", "Unknown error")
                except Exception:
                    error_detail = response.text[:300]

                st.error(f"❌ Processing failed (HTTP {response.status_code})")
                st.code(error_detail)

        except requests.exceptions.Timeout:
            st.error("⏱️ Request timed out (180s). Try a smaller file.")
        except requests.exceptions.ConnectionError:
            st.error(
                "🔌 Cannot connect to backend.\n"
                "Make sure the FastAPI server is running on port 8000."
            )
        except Exception as e:
            st.error(f"❌ Unexpected error: {str(e)}")


# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.markdown("""
<div style="text-align: center; color: #6c757d; font-size: 0.85rem;">
    🤖 Powered by LangChain ReAct Agent · Gemini 1.5 Flash · PyMuPDF · ezdxf · Shapely
    <br>Built for GitHub Codespaces
</div>
""", unsafe_allow_html=True)