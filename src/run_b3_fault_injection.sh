#!/bin/bash
#SBATCH --job-name=b3_fault
#SBATCH --output=/speed-scratch/%u/logs/b3_fault_%j.log
#SBATCH --error=/speed-scratch/%u/logs/b3_fault_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=08:00:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

# ─────────────────────────────────────────────────────────────────────────────
#  LLM-MAS — B3 Fault Injection Campaign (all fault modes × all configs)
#
#  PURPOSE
#  -------
#  Injects all 10 fault modes into the full checkout orchestrator pipeline and
#  applies the LKW/RIP oracle to classify each run as:
#    TP / PARTIAL_TP — fault detected (terminated mutant)
#    FN              — fault hidden in natural variance (live mutant)
#    INCONCLUSIVE    — infrastructure / timeout error
#
#  Fault modes (10 total):
#    General:  FM_3_1  FM_1_2  FM_2_2  FM_2_5
#    Business: BL_SHIPMENT_LOST  BL_INVENTORY_MISMATCH  BL_VENDOR_NEGOTIATION
#              BL_CUSTOMER_ESCALATION  BL_REFUND_REASONING  BL_COMPLIANCE_AMBIGUITY
#
#  Configs (4 total):
#    14b_temp0   qwen2.5-coder:14b  temp=0.0  (Retail-bench)
#    3b_temp0    qwen2.5:3b         temp=0.0  (Google-bench, deterministic)
#    3b_temp0.7  qwen2.5:3b         temp=0.7  (Google-bench, moderate variance)
#    3b_temp1.0  qwen2.5:3b         temp=1.0  (Google-bench, maximum variance)
#
#  Output
#  ------
#    src/results/b3/raw/b3_{fault_mode}_{cfg}_run{n}.json
#    src/results/b3/b3_{fault_mode}_{cfg}_summary.json
#    src/results/b3/b3_full_report.json
#
#  Usage on SPEED HPC
#  ------------------
#    # Full campaign — all 10 faults × 4 configs × 3 runs (default):
#    sbatch src/run_b3_fault_injection.sh
#
#    # Single fault mode, all configs:
#    sbatch --export=FAULT_MODE=FM_3_1,B3_RUNS=3 src/run_b3_fault_injection.sh
#
#    # Single config only (faster for testing):
#    sbatch --export=CFG=3b_temp0.7,B3_RUNS=3 src/run_b3_fault_injection.sh
#
#    # One fault × one config:
#    sbatch --export=FAULT_MODE=FM_3_1,CFG=3b_temp1.0,B3_RUNS=3 src/run_b3_fault_injection.sh
#
#    # Watch live output:
#    tail -f /speed-scratch/$USER/logs/b3_fault_<jobid>.log
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail   # -u: unset var error, -o pipefail; removed -e (set -e exits silently on curl subshell failure)

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}" "${SRCDIR}/results/b3/raw"

# ── Ollama / model config ─────────────────────────────────────────────────────
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export LLAMA_MODEL="${LLAMA_MODEL:-qwen2.5-coder:14b}"
export MODEL_3B="${MODEL_3B:-qwen2.5:3b}"

# MODEL_TAG namespaces result filenames for multi-model campaigns. Without it,
# swapping MODEL_3B keeps the '3b_*' labels and silently overwrites the
# existing qwen2.5:3b artifacts. Fail before any GPU work is done.
export MODEL_TAG="${MODEL_TAG:-}"
if [[ -z "${MODEL_TAG}" && "${MODEL_3B}" != "qwen2.5:3b" ]]; then
    echo "ERROR: MODEL_3B='${MODEL_3B}' is not the default, but MODEL_TAG is empty."
    echo "       Results would overwrite the existing qwen2.5:3b artifacts."
    echo "       Re-submit with a namespace, e.g.:"
    echo "         sbatch --export=ALL,MODEL_3B=${MODEL_3B},MODEL_TAG=llama32-3b ..."
    exit 1
fi

# ── Run parameters ────────────────────────────────────────────────────────────
B3_RUNS="${B3_RUNS:-3}"
FAULT_MODE_ARG="${FAULT_MODE:-}"   # empty = every mode in the selected scope
FAULT_SCOPE="${FAULT_SCOPE:-paper8}"  # paper8 = the 8 modes the paper reports; all = 13
CFG="${CFG:-}"                     # empty = all 4 configs

# Accept the documented CFG spelling (3b_temp0.7) even when the labels are
# namespaced by MODEL_TAG, so the usage examples above stay valid.
if [[ -n "${MODEL_TAG}" && "${CFG}" == 3b_* ]]; then
    CFG="${MODEL_TAG}_${CFG#3b_}"
fi

# A tagged run with no CFG still expands to all four configs -- including the
# untagged 14b_temp0 -- and would overwrite the published retail-bench
# artifacts. Tagged campaigns must pin exactly one config.
if [[ -n "${MODEL_TAG}" && -z "${CFG}" ]]; then
    echo "ERROR: MODEL_TAG='${MODEL_TAG}' is set but CFG is empty."
    echo "       An untargeted tagged run also executes the untagged 14b_temp0"
    echo "       config and would overwrite the published retail-bench artifacts."
    echo "       Pin a single config, e.g.:"
    echo "         sbatch --export=ALL,MODEL_3B=${MODEL_3B},MODEL_TAG=${MODEL_TAG},CFG=3b_temp0.7 ..."
    exit 1
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo "========================================================================"
echo "  LLM-MAS — B3 FAULT INJECTION CAMPAIGN"
echo "  Model 14b  : ${LLAMA_MODEL} @ ${OLLAMA_URL}"
echo "  Model 3b   : ${MODEL_3B}   @ ${OLLAMA_URL}"
echo "  Model tag  : ${MODEL_TAG:-<none> (labels stay 3b_*)}"
echo "  Runs/combo : ${B3_RUNS}"
echo "  Fault mode : ${FAULT_MODE_ARG:-ALL in scope}"
echo "  Scope      : ${FAULT_SCOPE}"
echo "  Config     : ${CFG:-ALL (4 configs)}"
echo "  Job        : ${SLURM_JOB_ID:-local}   Node: ${SLURMD_NODENAME:-local}"
echo "  Started    : $(date)"
echo "========================================================================"

cd "${SRCDIR}"

# ── Ollama binary + models dir — set unconditionally so pull/list always ──────
# use scratch even when ollama is already running from a prior job on this node.
OLLAMA_BIN="${SCRATCH}/tools/ollama/bin/ollama"
export OLLAMA_MODELS="${SCRATCH}/ollama-models"
mkdir -p "${OLLAMA_MODELS}"

# ── Start Ollama if not already running ───────────────────────────────────────
if ! curl -sf "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    echo "[ollama] starting ollama server..."
    nohup "${OLLAMA_BIN}" serve > "${LOGDIR}/ollama_b3_${SLURM_JOB_ID:-0}.log" 2>&1 &
    OLLAMA_PID=$!
    echo "[ollama] pid=${OLLAMA_PID}, waiting for ready..."
    for i in $(seq 1 30); do
        sleep 2
        curl -sf "${OLLAMA_URL}/api/tags" >/dev/null 2>&1 && break
        echo "[ollama] waiting... (${i}/30)"
    done
    curl -sf "${OLLAMA_URL}/api/tags" >/dev/null 2>&1 || { echo "ERROR: Ollama failed to start"; exit 1; }
    echo "[ollama] ready"
fi

# ── Warm up model inference (loads model into GPU before Python starts) ──────
WARMUP_MODEL="${MODEL_3B}"
if [[ "${CFG:-}" == "14b"* ]]; then
    WARMUP_MODEL="${LLAMA_MODEL}"
fi
# Pull model if not already present (safe to re-run, idempotent)
echo "[ollama] ensuring model is pulled: ${WARMUP_MODEL}..."
"${OLLAMA_BIN}" pull "${WARMUP_MODEL}" 2>&1 || echo "[ollama] pull returned non-zero (may already be cached)"
echo "[ollama] models available:"
"${OLLAMA_BIN}" list 2>&1 || true

# Actual inference warmup — use || to capture rc without triggering pipefail exit
echo "[ollama] running inference warmup for: ${WARMUP_MODEL}..."
WARMUP_RC=0
WARMUP_RESP=$(curl -s -X POST "${OLLAMA_URL}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${WARMUP_MODEL}\",\"prompt\":\"ping\",\"stream\":false}" \
    --max-time 300 2>&1) || WARMUP_RC=$?
echo "[ollama] warmup curl rc=${WARMUP_RC}"
if [[ ${WARMUP_RC} -ne 0 ]]; then
    echo "ERROR: Model warmup failed (rc=${WARMUP_RC})"
    echo "Warmup response: ${WARMUP_RESP}"
    exit 1
fi
echo "[ollama] model ${WARMUP_MODEL} is loaded and ready"

# ── Build b3_runner.py arguments ─────────────────────────────────────────────
B3_ARGS="--runs ${B3_RUNS}"

if [[ -n "${FAULT_MODE_ARG}" ]]; then
    B3_ARGS="${B3_ARGS} --fault-mode ${FAULT_MODE_ARG}"
else
    B3_ARGS="${B3_ARGS} --all --fault-scope ${FAULT_SCOPE}"
fi

if [[ -n "${CFG}" ]]; then
    B3_ARGS="${B3_ARGS} --cfg ${CFG}"
fi

# ── Run B3 fault injection ────────────────────────────────────────────────────
echo ""
echo "── Running: python b3_runner.py ${B3_ARGS} ──"
"${PYTHON}" b3_runner.py ${B3_ARGS}

echo ""
echo "========================================================================"
echo "  B3 FAULT INJECTION COMPLETE — $(date)"
echo "  Full report: ${SRCDIR}/results/b3/b3_full_report.json"
echo "========================================================================"
