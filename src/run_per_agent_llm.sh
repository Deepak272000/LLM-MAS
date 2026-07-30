#!/bin/bash
#SBATCH --job-name=per_agent_llm
#SBATCH --output=/speed-scratch/%u/logs/per_agent_llm_%j.log
#SBATCH --error=/speed-scratch/%u/logs/per_agent_llm_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=06:00:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

# =============================================================================
#  LLM-MAS — Per-Agent LLM Fault Injection Runner
#
#  PURPOSE
#  -------
#  Addresses professor's core requirement:
#    "Without LLM inference, the work has no meaning"
#
#  Runs each Python agent INDIVIDUALLY with real Ollama LLM
#  inference (or its live graph/orchestrator path) for:
#    B2: NONE mode × 10 runs → natural LLM variance per agent
#    B3: each fault mode × 3 runs → mutation detection per agent
#
#  Agents (all Python agent roles currently in scope):
#    productcatalog, currency, payment, email, recommendation,
#    adservice, shipping_quote, ship_order, cart, checkout
#
#  Model configs:
#    14b_temp0   qwen2.5-coder:14b  temp=0.0  (Retail-bench)
#    3b_temp0    qwen2.5:3b         temp=0.0  (Google-bench, deterministic)
#    3b_temp0.7  qwen2.5:3b         temp=0.7  (Google-bench, moderate variance)
#    3b_temp1.0  qwen2.5:3b         temp=1.0  (Google-bench, maximum variance)
#
#  Output:
#    src/results/per_agent_llm/b2/          (raw per-run B2 traces)
#    src/results/per_agent_llm/b3/          (raw per-run B3 traces)
#    src/results/per_agent_llm/per_agent_b2_variance.json
#    src/results/per_agent_llm/per_agent_b3_mutations.json
#    src/results/per_agent_llm/per_agent_llm_report.json
#
#  Usage on SPEED HPC:
#  -------------------
#    # Full run (B2 + B3, all agents, all configs):
#    sbatch src/run_per_agent_llm.sh
#
#    # Single agent, single config:
#    sbatch --export=AGENT=currency,CFG=14b_temp0 src/run_per_agent_llm.sh
#
#    # B2 only (calibrate variance before B3):
#    sbatch --export=PHASE=b2 src/run_per_agent_llm.sh
#
#    # B3 only (after B2 is done):
#    sbatch --export=PHASE=b3 src/run_per_agent_llm.sh
#
#    # Single fault mode across all agents:
#    sbatch --export=FAULT_MODE=FM_3_1 src/run_per_agent_llm.sh
#
#    # Watch live output:
#    tail -f /speed-scratch/$USER/logs/per_agent_llm_<JOBID>.log
# =============================================================================

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}" "${SRCDIR}/results/per_agent_llm/b2" "${SRCDIR}/results/per_agent_llm/b3"

source "${SRCDIR}/live_service_stack.sh"

# ── Ollama / model config ─────────────────────────────────────────────────────
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export LLAMA_MODEL="${LLAMA_MODEL:-qwen2.5-coder:14b}"
export MODEL_3B="${MODEL_3B:-qwen2.5:3b}"

# ── Run parameters (override with --export on sbatch) ─────────────────────────
AGENT="${AGENT:-}"           # empty = all agents
CFG="${CFG:-}"               # empty = all 4 configs
PHASE="${PHASE:-both}"       # b2 | b3 | both
FAULT_MODE="${FAULT_MODE:-}" # empty = all 10 fault modes
B2_RUNS="${B2_RUNS:-10}"
B3_RUNS="${B3_RUNS:-3}"

# ── Banner ────────────────────────────────────────────────────────────────────
echo "========================================================================"
echo "  LLM-MAS — PER-AGENT LLM FAULT INJECTION"
echo "  Real Ollama inference — app.orchestrator (ReAct loop)"
echo "  14b model : ${LLAMA_MODEL} @ ${OLLAMA_URL}"
echo "  3b  model : ${MODEL_3B}"
echo "  Agent     : ${AGENT:-ALL}"
echo "  Config    : ${CFG:-ALL}"
echo "  Phase     : ${PHASE}"
echo "  B2 runs   : ${B2_RUNS}"
echo "  B3 runs   : ${B3_RUNS} per fault mode"
echo "  Job       : ${SLURM_JOB_ID}   Node: ${SLURMD_NODENAME}"
echo "  GPU       : $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
echo "  Started   : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"
echo ""

cd "${SRCDIR}"

# ── Check Ollama ──────────────────────────────────────────────────────────────
echo "  Checking Ollama at ${OLLAMA_URL} ..."
if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    echo "  Ollama: READY"
else
    echo "  WARNING: Ollama not reachable — LLM calls will fail."
    echo "  Start Ollama before submitting: ollama serve &"
fi
echo ""

# ── Start live backend services when this agent needs them ───────────────────
if [ -z "${AGENT}" ]; then
    echo "  Starting live backend services for full campaign ..."
    start_required_live_services all
elif [ "${AGENT}" = "recommendation" ]; then
    echo "  Starting live backend services for recommendation ..."
    start_required_live_services recommendation
elif [ "${AGENT}" = "adservice" ]; then
    echo "  Starting live backend services for adservice ..."
    start_required_live_services adservice
elif [ "${AGENT}" = "checkout" ]; then
    # checkout orchestrator calls sub-helpers (productcatalog, currency, payment,
    # email, shipping) as Python subprocesses — no live gRPC services needed.
    echo "  checkout: sub-helpers are Python subprocesses — no live gRPC startup needed."
elif [ "${AGENT}" = "cart" ]; then
    # cart agent calls Ollama directly — no gRPC services needed.
    echo "  cart: direct Ollama call — no live gRPC startup needed."
fi
echo ""

# ── Build args ────────────────────────────────────────────────────────────────
ARGS=""
[ -n "${AGENT}"      ] && ARGS="${ARGS} --agent ${AGENT}"
[ -n "${CFG}"        ] && ARGS="${ARGS} --cfg ${CFG}"
[ -n "${FAULT_MODE}" ] && ARGS="${ARGS} --fault-mode ${FAULT_MODE}"
[ "${PHASE}" = "b2"  ] && ARGS="${ARGS} --b2-only"
[ "${PHASE}" = "b3"  ] && ARGS="${ARGS} --b3-only"
ARGS="${ARGS} --b2-runs ${B2_RUNS} --b3-runs ${B3_RUNS}"

# ── Run ───────────────────────────────────────────────────────────────────────
echo "  Running: python per_agent_llm_runner.py ${ARGS}"
echo ""
$PYTHON per_agent_llm_runner.py ${ARGS}
STATUS=$?

echo ""
echo "========================================================================"
if [ $STATUS -eq 0 ]; then
    echo "  PER-AGENT LLM RUN COMPLETE"
    echo "  Results:"
    echo "    ${SRCDIR}/results/per_agent_llm/per_agent_llm_report.json"
    echo "    ${SRCDIR}/results/per_agent_llm/per_agent_b2_variance.json"
    echo "    ${SRCDIR}/results/per_agent_llm/per_agent_b3_mutations.json"
else
    echo "  ERROR: per_agent_llm_runner.py exited with code ${STATUS}"
fi
echo "  Finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"
exit $STATUS
