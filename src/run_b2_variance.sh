#!/bin/bash
#SBATCH --job-name=b2_variance
#SBATCH --output=/speed-scratch/%u/logs/b2_variance_%j.log
#SBATCH --error=/speed-scratch/%u/logs/b2_variance_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=03:00:00
#SBATCH --partition=gpu
#SBATCH --gres=gpu:a100:1

# ─────────────────────────────────────────────────────────────────────────────
#  LLM-MAS — B2 Natural Variance Baseline (High-Temp Google-bench configs)
#
#  PURPOSE
#  -------
#  Runs the full checkout pipeline with FAULT_MODE=NONE and real LLM inference
#  for the NEW high-temperature 3b configs (3b_temp0.7, 3b_temp1.0).
#  Results establish the natural variance envelope needed before B3 fault runs.
#
#  Configs run (by default all 4; override with CFG= env var):
#    14b_temp0   qwen2.5-coder:14b  temp=0.0  (Retail-bench)
#    3b_temp0    qwen2.5:3b         temp=0.0  (Google-bench, deterministic)
#    3b_temp0.7  qwen2.5:3b         temp=0.7  (Google-bench, moderate variance)
#    3b_temp1.0  qwen2.5:3b         temp=1.0  (Google-bench, maximum variance)
#
#  Output
#  ------
#    src/results/b2/raw/b2_{cfg}_run{n}.json
#    src/results/b2/b2_{cfg}_variance_summary.json
#    src/results/b2/b2_full_variance_report.json
#
#  Usage on SPEED HPC
#  ------------------
#    # All 4 configs, 10 runs each (default):
#    sbatch src/run_b2_variance.sh
#
#    # High-temp only, custom run count:
#    sbatch --export=CFG=3b_temp0.7,B2_RUNS=10 src/run_b2_variance.sh
#    sbatch --export=CFG=3b_temp1.0,B2_RUNS=10 src/run_b2_variance.sh
#
#    # Watch live output:
#    tail -f /speed-scratch/$USER/logs/b2_variance_<jobid>.log
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}" "${SRCDIR}/results/b2/raw"

# ── Ollama / model config ─────────────────────────────────────────────────────
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export LLAMA_MODEL="${LLAMA_MODEL:-qwen2.5-coder:14b}"
export MODEL_3B="${MODEL_3B:-qwen2.5:3b}"
export FAULT_MODE=NONE

# ── Run parameters ────────────────────────────────────────────────────────────
B2_RUNS="${B2_RUNS:-10}"
CFG="${CFG:-}"       # empty = all configs; set to e.g. "3b_temp0.7" for one only

# ── Banner ────────────────────────────────────────────────────────────────────
echo "========================================================================"
echo "  LLM-MAS — B2 NATURAL VARIANCE RUNNER"
echo "  Model 14b : ${LLAMA_MODEL} @ ${OLLAMA_URL}"
echo "  Model 3b  : ${MODEL_3B}   @ ${OLLAMA_URL}"
echo "  Runs      : ${B2_RUNS} per config"
echo "  Config    : ${CFG:-all}"
echo "  Job       : ${SLURM_JOB_ID:-local}   Node: ${SLURMD_NODENAME:-local}"
echo "  Started   : $(date)"
echo "========================================================================"

cd "${SRCDIR}"

# ── Start Ollama if not already running ───────────────────────────────────────
if ! curl -sf "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    echo "[ollama] starting ollama server..."
    OLLAMA_BIN="${SCRATCH}/tools/ollama/bin/ollama"
    export OLLAMA_MODELS="${SCRATCH}/ollama-models"
    nohup "${OLLAMA_BIN}" serve > "${LOGDIR}/ollama_b2_${SLURM_JOB_ID:-0}.log" 2>&1 &
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

# ── Run B2 variance ───────────────────────────────────────────────────────────
if [[ -n "${CFG}" ]]; then
    echo ""
    echo "── Running single config: ${CFG} ──"
    "${PYTHON}" b2_variance_runner.py --cfg "${CFG}" --runs "${B2_RUNS}"
else
    echo ""
    echo "── Running all configs ──"
    "${PYTHON}" b2_variance_runner.py --all --runs "${B2_RUNS}"
fi

echo ""
echo "========================================================================"
echo "  B2 VARIANCE COMPLETE — $(date)"
echo "  Results: ${SRCDIR}/results/b2/"
echo "========================================================================"
