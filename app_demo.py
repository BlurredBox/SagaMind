"""
SagaMind Interactive Dashboard
===============================

A premium Streamlit visualizer to demonstrate transactional agents,
Z3 verification, and CLS memory consolidation.

Usage:
    streamlit run app_demo.py
"""

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import settings
from src.memory.decay import EbbinghausMemoryManager
from src.models import ActionPayload, MemoryNode, SagaStep
from src.orchestrator.coordinator import SagaTransactionCoordinator
from src.orchestrator.sandbox import WasmSandbox
from src.verifier.z3_prover import Z3Verifier

# Set page config with premium styling
st.set_page_config(page_title="SagaMind Control Center", page_icon="", layout="wide", initial_sidebar_state="expanded")

# Custom CSS for modern glassmorphic look and font settings
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Space+Grotesk:wght@400;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }
    h1, h2, h3 {
        font-family: 'Space Grotesk', sans-serif;
        font-weight: 700;
        letter-spacing: -0.5px;
    }
    .main-title {
        font-size: 3rem;
        background: linear-gradient(135deg, #FF6B6B 0%, #4D96FF 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0px;
        padding-bottom: 0px;
    }
    .tagline {
        color: #A0AEC0;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #1A202C;
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #2D3748;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .status-badge {
        font-size: 0.85rem;
        padding: 4px 10px;
        border-radius: 8px;
        font-weight: 600;
        display: inline-block;
    }
</style>
""",
    unsafe_allow_html=True,
)

# App Title & Header
st.markdown("<h1 class='main-title'>SagaMind</h1>", unsafe_allow_html=True)
st.markdown(
    "<p class='tagline'>Transaction-Safe Multi-Agent Runtime & Episodic Memory Co-Processor</p>",
    unsafe_allow_html=True,
)

# Initialize Session States
if "logs" not in st.session_state:
    st.session_state.logs = []
if "saga_status" not in st.session_state:
    st.session_state.saga_status = "IDLE"
if "memory_bank" not in st.session_state:
    # Build standard mock memory bank
    st.session_state.memory_bank = [
        MemoryNode(
            memory_id="m-101",
            created_at=datetime.now(timezone.utc) - timedelta(hours=28),
            last_retrieved_at=datetime.now(timezone.utc) - timedelta(hours=14),
            agent_role="Coder",
            summary="Refactored database pool size and connections limit to 20.",
            importance_score=0.9,
            retrieval_count=4,
            embedding=[0.85, 0.15, 0.0],
        ),
        MemoryNode(
            memory_id="m-102",
            created_at=datetime.now(timezone.utc) - timedelta(hours=12),
            last_retrieved_at=datetime.now(timezone.utc) - timedelta(hours=6),
            agent_role="Security",
            summary="Identified path traversal vulnerability in files read controller.",
            importance_score=1.0,
            retrieval_count=8,
            embedding=[0.1, 0.9, 0.0],
        ),
        MemoryNode(
            memory_id="m-103",
            created_at=datetime.now(timezone.utc) - timedelta(hours=48),
            last_retrieved_at=datetime.now(timezone.utc) - timedelta(hours=48),
            agent_role="DevOps",
            summary="Initialized backup Kubernetes state deployment specifications.",
            importance_score=0.4,
            retrieval_count=0,
            embedding=[0.0, 0.1, 0.9],
        ),
    ]

# Sidebar configuration controls
st.sidebar.markdown("### Control Panel")
decay_rate = st.sidebar.slider("Base Decay Time Constant (Hours)", 2.0, 48.0, 12.0)
retention_threshold = st.sidebar.slider("Eviction Threshold (τ)", 0.05, 0.5, 0.15)
st.sidebar.markdown("---")
st.sidebar.info("Use this control center to simulate LLM transactions, safety invariants, and cognitive sleep-cycles.")

# Build 3 column metric display
col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(
        f"""
    <div class='metric-card'>
        <p style='margin:0;color:#718096;font-size:0.9rem;'>Active Transaction State</p>
        <h2 style='margin:5px 0 0 0;color:#4D96FF;'>{st.session_state.saga_status}</h2>
    </div>
    """,
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        f"""
    <div class='metric-card'>
        <p style='margin:0;color:#718096;font-size:0.9rem;'>Saga Memory Nodes</p>
        <h2 style='margin:5px 0 0 0;color:#50E3C2;'>{len(st.session_state.memory_bank)} Active Traces</h2>
    </div>
    """,
        unsafe_allow_html=True,
    )
with col3:
    st.markdown(
        """
    <div class='metric-card'>
        <p style='margin:0;color:#718096;font-size:0.9rem;'>Z3 Symbolic Gate Status</p>
        <h2 style='margin:5px 0 0 0;color:#FF9F1C;'>Active (Enforcing)</h2>
    </div>
    """.format(),
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

# Layout division: LEFT: Saga Engine simulation, RIGHT: Memory Management
left_col, right_col = st.columns([1.2, 1])

with left_col:
    st.subheader("Transactional Agent Pipeline Simulation")
    st.write("Trigger agent actions. Watch the verification gate approve safe steps or trigger rollbacks on violation.")

    # Preset scenarios
    scenario = st.selectbox(
        "Choose Agent Execution Scenario:",
        ["Scenario A: Safe Code Rewrite (Expect Success)", "Scenario B: Path Traversal Exploit (Expect Saga Rollback)"],
    )

    if st.button("Trigger Simulation"):
        st.session_state.logs = []
        st.session_state.saga_status = "RUNNING"

        verifier = Z3Verifier()
        sandbox = WasmSandbox()
        coordinator = SagaTransactionCoordinator(verifier, sandbox)
        saga_id = str(uuid.uuid4())
        workspace = Path(settings.allowed_workspace_root) / "sagamind-demo" / saga_id
        target = workspace / "setup.py"
        path_invariant = f'(assert (str.prefixof "{workspace}" path))'

        if "Scenario A" in scenario:
            steps = [
                SagaStep(
                    step_id="1",
                    step_name="Create Setup Script",
                    action=ActionPayload(
                        "WRITE_FILE",
                        {"path": str(target), "content": "print('init')"},
                    ),
                    compensation=ActionPayload(
                        "RESTORE_FILE",
                        {"path": str(target), "existed": False, "previous": ""},
                    ),
                    invariants=path_invariant,
                ),
                SagaStep(
                    step_id="2",
                    step_name="Finalize Plan",
                    action=ActionPayload("NOOP", {}),
                    compensation=ActionPayload("NOOP", {}),
                    invariants="(assert true)",
                ),
            ]
        else:
            steps = [
                SagaStep(
                    step_id="1",
                    step_name="Create App Code",
                    action=ActionPayload(
                        "WRITE_FILE",
                        {"path": str(target), "content": "# App code"},
                    ),
                    compensation=ActionPayload(
                        "RESTORE_FILE",
                        {"path": str(target), "existed": False, "previous": ""},
                    ),
                    invariants=path_invariant,
                ),
                SagaStep(
                    step_id="2",
                    step_name="Inject Path Traversal Payload",
                    action=ActionPayload("WRITE_FILE", {"path": "/etc/passwd", "content": "hack"}),
                    compensation=ActionPayload("DELETE_FILE", {"path": "/etc/passwd"}),
                    invariants=path_invariant,
                ),
            ]

        def log_callback(step: SagaStep, status: str, error: str = ""):
            st.session_state.logs.append(
                {
                    "Step": step.step_name,
                    "Action": step.action.tool_name,
                    "State": status,
                    "Error Details": error,
                    "Time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                }
            )
            time.sleep(0.8)  # Simulate processing delay

        # Run the saga orchestrator
        coordinator.start_transaction_log(saga_id, scenario, "demo")
        res = coordinator.execute_saga(saga_id, steps, callback=log_callback)
        st.session_state.saga_status = "ROLLED_BACK" if not res else "COMMITTED"
        st.rerun()

    # Render Active Logs
    if st.session_state.logs:
        df_logs = pd.DataFrame(st.session_state.logs)
        st.dataframe(
            df_logs,
            use_container_width=True,
            column_config={
                "State": st.column_config.TextColumn(
                    "State", help="Active status of the transaction step", width="medium"
                )
            },
        )

with right_col:
    st.subheader("Cognitive Memory Decay (Ebbinghaus Curves)")
    st.write("Understand memory retention values. Memories decay exponentially over time unless reinforced.")

    # Memory decay chart visualization
    manager = EbbinghausMemoryManager(s_init=decay_rate, tau=retention_threshold)

    decay_data = []
    times = np.linspace(0, 72, 100)  # 3 days timeline

    # Calculate curves for three profiles
    for t in times:
        now_utc = datetime.now(timezone.utc)
        retrieved_at = now_utc - timedelta(hours=t)
        # 1. Low importance, no retrievals
        m1 = MemoryNode("", now_utc, retrieved_at, "", "", 0.3, 0, [])
        r1 = manager.calculate_retention(m1)
        # 2. High importance, no retrievals
        m2 = MemoryNode("", now_utc, retrieved_at, "", "", 0.9, 0, [])
        r2 = manager.calculate_retention(m2)
        # 3. High importance + highly reinforced
        m3 = MemoryNode("", now_utc, retrieved_at, "", "", 0.9, 5, [])
        r3 = manager.calculate_retention(m3)

        decay_data.append(
            {"Elapsed Hours": t, "Retention Score": r1, "Memory Profile": "Low Importance (0.3), No retrievals"}
        )
        decay_data.append(
            {"Elapsed Hours": t, "Retention Score": r2, "Memory Profile": "High Importance (0.9), No retrievals"}
        )
        decay_data.append(
            {"Elapsed Hours": t, "Retention Score": r3, "Memory Profile": "High Importance (0.9) + 5 Accesses"}
        )

    df_decay = pd.DataFrame(decay_data)
    fig = px.line(
        df_decay,
        x="Elapsed Hours",
        y="Retention Score",
        color="Memory Profile",
        title="Dynamic Memory Retention Over Time",
        color_discrete_sequence=["#FF6B6B", "#4D96FF", "#50E3C2"],
    )
    fig.add_hline(y=retention_threshold, line_dash="dash", line_color="orange", annotation_text="Eviction Threshold")
    fig.update_layout(template="plotly_dark", margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

    # Active memory list
    st.markdown("### Active Memory Table")
    mem_records = []
    for m in st.session_state.memory_bank:
        ret = manager.calculate_retention(m)
        status = "Active" if ret >= retention_threshold else "Evicted"
        mem_records.append(
            {
                "Summary": m.summary,
                "Role": m.agent_role,
                "Importance": m.importance_score,
                "Retrievals": m.retrieval_count,
                "Retention Value": f"{ret:.2f}",
                "State": status,
            }
        )
    st.table(pd.DataFrame(mem_records))
