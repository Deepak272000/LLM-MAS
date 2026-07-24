#!/bin/bash
#SBATCH --job-name=all_agents_fi
#SBATCH --output=all_agents_%j.log
#SBATCH --error=all_agents_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=4G
#SBATCH --time=00:20:00

# ─────────────────────────────────────────────────────────────────────────────
#  LLM-MAS — All-Agent Fault Injection + HITL Tier Classification
#  Runs all Python-agent fault injection suites (3 runs each for RQ5 stability),
#  then classifies every fault mode into HITL Tier 1 / 2 / 3 automatically.
#
#  Before submitting:
#    git pull origin deepak/fault-injection
#    sbatch src/run_all_agents.sh
#
#  Output files written to:
#    all_agents_<JOBID>.log
#    src/results/stability_matrix_<agent>.json   (one per agent)
#    src/results/stability_summary.json
#    src/results/hitl_classification_report.json
# ─────────────────────────────────────────────────────────────────────────────

VENV=/speed-scratch/$USER/LLM-MAS/src/shippingservice/.venv
PYTHON=$VENV/bin/python
SRCDIR=/speed-scratch/$USER/LLM-MAS/src

cd "$SRCDIR"

echo "========================================================================"
echo "  LLM-MAS AGENT FAULT INJECTION + HITL CLASSIFICATION"
echo "  Python agents x fault modes x 3 stability runs"
echo "  Job: $SLURM_JOB_ID   Node: $SLURMD_NODENAME"
echo "  Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"
echo ""

# Install any missing lightweight deps (no-op if already present)
$PYTHON -m pip install -q python-dotenv

# ── Step 1: Fault injection stability sweep (all 6 agents, 3 runs each) ──────
echo "  [1/2] Running stability analysis — Python agents x fault modes x 3 runs each"
echo "        (generates *_fault_results.json + stability_matrix_*.json)"
echo ""
$PYTHON stability_analysis.py
STATUS_STABILITY=$?

if [ $STATUS_STABILITY -ne 0 ]; then
    echo ""
    echo "  [ERROR] stability_analysis.py exited with code $STATUS_STABILITY"
    echo "  Aborting HITL classification."
    exit $STATUS_STABILITY
fi

echo ""
echo "  [1/2] Stability analysis complete."
echo ""

# ── Step 2: HITL automated tier classification ────────────────────────────────
echo "  [2/2] Running HITL tier classifier on fault results..."
echo ""
$PYTHON hitl_detector.py
STATUS_HITL=$?

if [ $STATUS_HITL -ne 0 ]; then
    echo ""
    echo "  [ERROR] hitl_detector.py exited with code $STATUS_HITL"
    exit $STATUS_HITL
fi

echo ""
echo "========================================================================"
echo "  ALL DONE"
echo "  Results: $SRCDIR/results/"
echo "    stability_summary.json"
echo "    stability_matrix_<agent>.json  (x6)"
echo "    hitl_classification_report.json"
echo "  Finished: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "========================================================================"
