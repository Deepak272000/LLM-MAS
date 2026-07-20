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

set -euo pipefail

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

# ── Run parameters ────────────────────────────────────────────────────────────
B3_RUNS="${B3_RUNS:-3}"
FAULT_MODE_ARG="${FAULT_MODE:-}"   # empty = all 10 fault modes
CFG="${CFG:-}"                     # empty = all 4 configs

# ── Banner ────────────────────────────────────────────────────────────────────
echo "========================================================================"
echo "  LLM-MAS — B3 FAULT INJECTION CAMPAIGN"
echo "  Model 14b  : ${LLAMA_MODEL} @ ${OLLAMA_URL}"
echo "  Model 3b   : ${MODEL_3B}   @ ${OLLAMA_URL}"
echo "  Runs/combo : ${B3_RUNS}"
echo "  Fault mode : ${FAULT_MODE_ARG:-ALL (10 modes)}"
echo "  Config     : ${CFG:-ALL (4 configs)}"
echo "  Job        : ${SLURM_JOB_ID:-local}   Node: ${SLURMD_NODENAME:-local}"
echo "  Started    : $(date)"
echo "========================================================================"

cd "${SRCDIR}"

# ── Start Ollama if not already running ───────────────────────────────────────
if ! curl -sf "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    echo "[ollama] starting ollama server..."
    OLLAMA_BIN="${SCRATCH}/tools/ollama/bin/ollama"
    export OLLAMA_MODELS="${SCRATCH}/ollama-models"
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
echo "[ollama] warming up inference for model: ${WARMUP_MODEL} ..."
WARMUP_RESP=$(curl -sf -X POST "${OLLAMA_URL}/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${WARMUP_MODEL}\",\"prompt\":\"ping\",\"stream\":false}" \
    --max-time 180 2>&1)
WARMUP_RC=$?
if [[ ${WARMUP_RC} -ne 0 ]]; then
    echo "ERROR: Model warmup failed (rc=${WARMUP_RC}): ${WARMUP_RESP}"
    exit 1
fi
echo "[ollama] model ${WARMUP_MODEL} is loaded and ready"

# ── Build b3_runner.py arguments ─────────────────────────────────────────────
B3_ARGS="--runs ${B3_RUNS}"

if [[ -n "${FAULT_MODE_ARG}" ]]; then
    B3_ARGS="${B3_ARGS} --fault-mode ${FAULT_MODE_ARG}"
else
    B3_ARGS="${B3_ARGS} --all"
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
