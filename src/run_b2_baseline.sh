#!/bin/bash
#SBATCH --job-name=b2_baseline
#SBATCH --output=b2_baseline_%j.log
#SBATCH --error=b2_baseline_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=01:30:00
#SBATCH --partition=gpu
#SBATCH --gres=gpu:a100:1

# ─────────────────────────────────────────────────────────────────────────────
#  LLM-MAS — B2 Natural Variance Baseline Collection
#
#  PURPOSE
#  -------
#  Runs all agents with FAULT_MODE=NONE N times to capture the natural LLM
#  output variance (B2 baseline).  Equivalence tolerances derived here are
#  locked in BEFORE any B3 (fault-injected) runs to prevent data snooping.
#
#  Agents covered
#  ──────────────
#  Deterministic (USE_LLM=false):
#    paymentagent, currencyagent, emailserviceagent,
#    productcatalogagent, recommendationagent, adserviceagent
#  Live-LLM (USE_LLM=true):
#    shippingservice  →  qwen2.5-coder:14b via Ollama
#
#  Output
#  ──────
#    src/results/b2_variance_report.json        (per-checkpoint raw stats)
#    src/results/b2_equivalence_thresholds.json (calibrated δ_ε / S / Σ)
#
#  Usage on SPEED HPC
#  ──────────────────
#    git pull origin deepak/fault-injection
#    sbatch src/run_b2_baseline.sh
#    # or with custom run count:
#    sbatch --export=B2_RUNS=5 src/run_b2_baseline.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Environment ───────────────────────────────────────────────────────────────
VENV=/speed-scratch/$USER/LLM-MAS/src/shippingservice/.venv
PYTHON=$VENV/bin/python
SRCDIR=/speed-scratch/$USER/LLM-MAS/src

# Ollama config — qwen2.5-coder:14b must already be pulled on this node
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
SHIP_MODEL="${SHIP_MODEL:-qwen2.5-coder:14b}"
B2_RUNS="${B2_RUNS:-3}"

export OLLAMA_URL SHIP_MODEL FAULT_MODE=NONE

echo "========================================================================"
echo "  LLM-MAS B2 NATURAL VARIANCE BASELINE"
echo "  Agents:  6 deterministic + ShippingService (live Ollama)"
echo "  Runs:    $B2_RUNS per agent"
echo "  Model:   $SHIP_MODEL @ $OLLAMA_URL"
echo "  Job:     $SLURM_JOB_ID   Node: $SLURMD_NODENAME"
echo "  GPU:     $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'n/a')"
echo "  Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"

# ── Install any missing lightweight deps ─────────────────────────────────────
$PYTHON -m pip install -q python-dotenv requests

# ── Check Ollama is reachable ─────────────────────────────────────────────────
echo ""
echo "  Checking Ollama endpoint: $OLLAMA_URL ..."
if curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
    echo "  Ollama reachable."
    SKIP_SHIPPING=""
else
    echo "  WARNING: Ollama not reachable at $OLLAMA_URL"
    echo "  ShippingService B2 runs will be SKIPPED."
    echo "  To include ShippingService, start Ollama before submitting:"
    echo "    ollama serve &"
    echo "    ollama pull $SHIP_MODEL"
    SKIP_SHIPPING="--skip-shipping"
fi

# ── Check Ollama has the required model ──────────────────────────────────────
if [ -z "$SKIP_SHIPPING" ]; then
    MODEL_LIST=$(curl -sf "$OLLAMA_URL/api/tags" | python3 -c "
import sys, json
data = json.load(sys.stdin)
names = [m['name'] for m in data.get('models', [])]
print('\n'.join(names))
" 2>/dev/null || echo "")
    if echo "$MODEL_LIST" | grep -q "$SHIP_MODEL"; then
        echo "  Model $SHIP_MODEL available."
    else
        echo "  WARNING: Model $SHIP_MODEL not found in Ollama."
        echo "  Available: $MODEL_LIST"
        echo "  Run: ollama pull $SHIP_MODEL   (may take 10-20 min on first pull)"
        echo "  Skipping ShippingService for this job."
        SKIP_SHIPPING="--skip-shipping"
    fi
fi

cd "$SRCDIR"

# ── Run B2 baseline collection ────────────────────────────────────────────────
echo ""
echo "  Running B2 baseline runner ..."
echo ""

$PYTHON b2_baseline_runner.py \
    --runs "$B2_RUNS" \
    $SKIP_SHIPPING

STATUS=$?

echo ""
echo "========================================================================"
if [ $STATUS -eq 0 ]; then
    echo "  B2 baseline collection COMPLETE."
    echo "  Results:"
    echo "    $SRCDIR/results/b2_variance_report.json"
    echo "    $SRCDIR/results/b2_equivalence_thresholds.json"
    echo ""
    echo "  Next step: commit results and begin B3 fault injection runs."
    echo "    git add src/results/b2_*.json"
    echo "    git commit -m 'data: B2 natural variance baseline — all agents'"
    echo "    sbatch src/run_all_agents.sh"
else
    echo "  ERROR: b2_baseline_runner.py exited with code $STATUS"
    echo "  Check b2_baseline_${SLURM_JOB_ID}.log for details."
fi
echo "  Finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"

exit $STATUS
