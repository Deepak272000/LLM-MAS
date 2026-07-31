#!/bin/bash
#SBATCH --job-name=agentracer_attribution
#SBATCH --output=/speed-scratch/%u/logs/agentracer_%j.log
#SBATCH --error=/speed-scratch/%u/logs/agentracer_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

# =============================================================================
#  LLM-MAS × AgenTracer — All-Phase Attribution Pipeline
#
#  PURPOSE
#  -------
#  Runs all 4 phases of the AgenTracer integration for all 9 agents and
#  all fault modes:
#
#  Phase 1  Convert LKW checkpoints → AgenTracer trajectory files
#           Sources: hitl_classification_report.json (6 agents × 9 modes)
#                    stability_matrix_shippingagent.json (11 modes)
#                    cross_agent_propagation.json (2 chains)
#
#  Phase 2a Rule-based attribution (deterministic, no LLM)
#  Phase 2b LLM-based attribution  (qwen2.5:3b via Ollama)
#
#  Phase 3  Evaluate both attributors vs ground truth
#           Metrics: agent-level accuracy, step-level accuracy,
#                    per-tier breakdown, cross-agent chain accuracy
#
#  Phase 4  Write attribution_report.json + attribution_summary.json
#
#  Ground truth source: fault injection is deterministic — we KNOW which
#  agent was injected (FAULT_MODE env var) and at which step (infection_point
#  from HITL report). No counterfactual construction needed.
#
#  OUTPUTS
#  -------
#  src/results/agentracer/trajectories/single_agent/  ← Phase 1
#  src/results/agentracer/trajectories/cross_agent/   ← Phase 1
#  src/results/agentracer/trajectories_index.json      ← Phase 1
#  src/results/agentracer/attribution_report.json      ← Phase 3+4
#  src/results/agentracer/attribution_summary.json     ← Phase 4 (paper-ready)
#
#  SUBMISSION
#  ----------
#  sbatch run_attribution.sh
# =============================================================================

set -e

echo "================================================================"
echo "  LLM-MAS x AgenTracer Attribution Pipeline"
echo "  Job: $SLURM_JOB_ID  Node: $(hostname)  $(date)"
echo "================================================================"

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRATCH="/speed-scratch/$USER"
REPO="$SCRATCH/LLM-MAS"
VENV="$SCRATCH/venv"
SRC="$REPO/src"
LOGS="$SCRATCH/logs"
mkdir -p "$LOGS"

# ── Activate venv ─────────────────────────────────────────────────────────────
source "$VENV/bin/activate"
echo "[setup] Python: $(python --version)"
echo "[setup] Working dir: $SRC"

# ── Start Ollama in background ────────────────────────────────────────────────
echo "[setup] Starting Ollama…"
ollama serve &
OLLAMA_PID=$!
sleep 8   # wait for Ollama to initialise

# Pull model if not already cached
ollama pull qwen2.5:3b 2>/dev/null || true
echo "[setup] Ollama ready (PID $OLLAMA_PID)"

export OLLAMA_URL="http://localhost:11434"
export LLAMA_MODEL="qwen2.5:3b"

cd "$SRC"

# ── Phase 1+2+3+4: full pipeline with LLM attribution ────────────────────────
echo ""
echo "[pipeline] Running full 4-phase attribution pipeline…"
python agentracer_adapter/run_attribution_pipeline.py \
    --ollama-url "$OLLAMA_URL" \
    --model      "$LLAMA_MODEL"

echo ""
echo "[pipeline] Checking outputs…"
ls -lh "$SRC/results/agentracer/"

# ── Cleanup Ollama ────────────────────────────────────────────────────────────
kill $OLLAMA_PID 2>/dev/null || true

echo ""
echo "================================================================"
echo "  DONE — $(date)"
echo "  Results: $SRC/results/agentracer/"
echo "================================================================"
