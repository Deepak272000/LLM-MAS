#!/bin/bash
#SBATCH --job-name=adservice_b2_llm
#SBATCH --output=/speed-scratch/%u/logs/adservice_b2_llm_%j.log
#SBATCH --error=/speed-scratch/%u/logs/adservice_b2_llm_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=4G
#SBATCH --time=00:45:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

# AdServiceAgent B2 live-LLM run  (USE_LLM=true, FAULT_MODE=NONE, 10 runs)

set -uo pipefail

SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}"

export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
MODEL="${MODEL:-qwen2.5:3b}"
OLLAMA_BIN="${SCRATCH}/tools/ollama/bin/ollama"

echo "========================================================================"
echo "  AdService B2 live-LLM runner"
echo "  Model: ${MODEL}   Ollama: ${OLLAMA_URL}"
echo "  Job: ${SLURM_JOB_ID:-local}   Node: ${SLURMD_NODENAME:-local}"
echo "  Started: $(date)"
echo "========================================================================"

cd "${SRCDIR}"

# Pull latest
git -C "${SCRATCH}/LLM-MAS" pull origin deepak/fault-injection 2>/dev/null || true

# Start Ollama if not running
if ! curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    echo "[ollama] Starting Ollama daemon..."
    OLLAMA_MODELS="${SCRATCH}/tools/ollama/models" \
    OLLAMA_HOST="${OLLAMA_URL#http://}" \
        nohup "${OLLAMA_BIN}" serve > "${LOGDIR}/ollama_${SLURM_JOB_ID:-local}.log" 2>&1 &
    echo "[ollama] Waiting for Ollama to be ready..."
    for i in $(seq 1 30); do
        sleep 2
        curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1 && break
        echo "  ... retry $i/30"
    done
fi

echo "[ollama] $(curl -sf ${OLLAMA_URL}/api/tags | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get("models",[])), "models loaded")' 2>/dev/null || echo 'offline')"

# Ensure model is pulled
echo "[model] Ensuring ${MODEL} is available..."
OLLAMA_MODELS="${SCRATCH}/tools/ollama/models" \
    "${OLLAMA_BIN}" pull "${MODEL}" 2>&1 | tail -3

# Run
echo ""
"${PYTHON}" run_adservice_b2_live_llm.py \
    --runs 10 \
    --model "${MODEL}" \
    --temp 0.0 \
    --ollama-url "${OLLAMA_URL}"

echo "=== Done ==="
date