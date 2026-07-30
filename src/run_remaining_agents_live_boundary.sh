#!/bin/bash
#SBATCH --job-name=remain_live_boundary
#SBATCH --output=/speed-scratch/%u/logs/remain_live_boundary_%j.log
#SBATCH --error=/speed-scratch/%u/logs/remain_live_boundary_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

set -euo pipefail

SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}" "${SRCDIR}/results"

source "${SRCDIR}/live_service_stack.sh"

export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export MODEL_3B="${MODEL_3B:-qwen2.5:3b}"
export LLAMA_MODEL="${LLAMA_MODEL:-${MODEL_3B}}"

echo "========================================================================"
echo "  LLM-MAS — REMAINING AGENTS LIVE BOUNDARY EVIDENCE"
echo "  Model     : ${LLAMA_MODEL}"
echo "  Ollama    : ${OLLAMA_URL}"
echo "  Job       : ${SLURM_JOB_ID}   Node: ${SLURMD_NODENAME}"
echo "  Started   : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"
echo ""

cd "${SRCDIR}"

echo "  Checking Ollama at ${OLLAMA_URL} ..."
if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
    echo "  Ollama: READY"
else
    echo "  WARNING: Ollama not reachable — live boundary runs will fail."
fi
echo ""

echo "  Starting live backend services for boundary evidence ..."
start_required_live_services boundary
echo ""

echo "  Running: python remaining_agents_live_boundary.py"
echo ""
"${PYTHON}" remaining_agents_live_boundary.py
STATUS=$?

echo ""
echo "========================================================================"
if [ ${STATUS} -eq 0 ]; then
    echo "  LIVE BOUNDARY EVIDENCE COMPLETE"
    echo "  Results: ${SRCDIR}/results/remaining_agents_live_boundary.json"
else
    echo "  ERROR: remaining_agents_live_boundary.py exited with code ${STATUS}"
fi
echo "  Finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"
exit ${STATUS}