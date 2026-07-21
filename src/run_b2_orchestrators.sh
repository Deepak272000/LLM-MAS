#!/bin/bash
#SBATCH --job-name=b2_orchestrators
#SBATCH --output=/speed-scratch/%u/logs/b2_orchestrators_%j.log
#SBATCH --error=/speed-scratch/%u/logs/b2_orchestrators_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

# =============================================================================
#  LLM-MAS — True B2 Baseline with Pure LLM Orchestrators
#
#  PURPOSE
#  -------
#  Runs the full checkout pipeline (ProductCatalog → Currency → Shipping →
#  Payment → Email) through the new pure-LLM ReAct orchestrators for ALL 6
#  services. FAULT_MODE=NONE. Captures natural LLM variance at every LKW
#  checkpoint as the True B2 baseline for B3 comparison.
#
#  What changed (vs old b2_variance.sh)
#  ──────────────────────────────────────
#  - ALL 6 services now use ReAct orchestrators (no gRPC mocks, no mock_client)
#  - Each co_helper calls the service's orchestrator.py directly
#  - ProductCatalog, Currency, Payment, Email all run the same ReAct pattern
#    as ShippingAgent (established in prior work)
#
#  Model configs run
#  ─────────────────
#    3b_temp0    qwen2.5:3b  temp=0.0  (deterministic baseline)
#    3b_temp0.7  qwen2.5:3b  temp=0.7  (moderate variance)
#    3b_temp1.0  qwen2.5:3b  temp=1.0  (maximum variance)
#
#  Output
#  ──────
#    src/results/b2/raw/b2_{cfg}_run{n}.json          (per-run traces)
#    src/results/b2/b2_{cfg}_variance_summary.json    (per-config stats)
#    src/results/b2/b2_full_variance_report.json      (combined report)
#
#  Usage on SPEED HPC
#  ------------------
#    # All 3 configs, 10 runs each (default):
#    sbatch src/run_b2_orchestrators.sh
#
#    # Single config, custom run count:
#    sbatch --export=CFG=3b_temp0.7,B2_RUNS=5 src/run_b2_orchestrators.sh
#
#    # Watch live output:
#    tail -f /speed-scratch/$USER/logs/b2_orchestrators_<jobid>.log
# =============================================================================

set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}" "${SRCDIR}/results/b2/raw"

# ── Ollama config ─────────────────────────────────────────────────────────────
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export MODEL_3B="${MODEL_3B:-qwen2.5:3b}"
export FAULT_MODE=NONE

# ── Run parameters ────────────────────────────────────────────────────────────
B2_RUNS="${B2_RUNS:-10}"
CFG="${CFG:-}"    # empty = all configs

# ── Banner ────────────────────────────────────────────────────────────────────
echo "========================================================================"
echo "  LLM-MAS — TRUE B2 ORCHESTRATOR BASELINE"
echo "  Model 3b  : ${MODEL_3B} @ ${OLLAMA_URL}"
echo "  Runs      : ${B2_RUNS} per config"
echo "  Config    : ${CFG:-all (3b_temp0, 3b_temp0.7, 3b_temp1.0)}"
echo "  Job       : ${SLURM_JOB_ID:-local}   Node: ${SLURMD_NODENAME:-local}"
echo "  Started   : $(date)"
echo "========================================================================"

cd "${SRCDIR}"

# ── Ollama setup (reuse from prior job if already running) ────────────────────
OLLAMA_BIN="${SCRATCH}/tools/ollama/bin/ollama"

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

echo "[ollama] Status: $(curl -sf ${OLLAMA_URL}/api/tags | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get("models",[])), "models loaded")' 2>/dev/null || echo 'offline')"

# ── Pull model if not present ─────────────────────────────────────────────────
echo "[model] Ensuring ${MODEL_3B} is available..."
OLLAMA_MODELS="${SCRATCH}/tools/ollama/models" \
    "${OLLAMA_BIN}" pull "${MODEL_3B}" 2>&1 | tail -3

# ── Python environment check ──────────────────────────────────────────────────
echo ""
echo "[python] Using: ${PYTHON}"
"${PYTHON}" --version
"${PYTHON}" -c "import requests, json; print('[python] requests OK')"

# ── Verify all co_helper orchestrators are present ───────────────────────────
echo "[check] Verifying co_helper files..."
for f in co_helper_productcatalog.py co_helper_currency.py \
          co_helper_payment.py co_helper_email.py co_helper_shipping.py; do
    if [ -f "${SRCDIR}/${f}" ]; then
        echo "  OK  ${f}"
    else
        echo "  MISSING ${f} — aborting"
        exit 1
    fi
done

# ── Run B2 ────────────────────────────────────────────────────────────────────
echo ""
echo "[b2] Starting True B2 baseline runs..."
echo ""

run_config() {
    local cfg="$1"
    local temp="$2"
    echo "──────────────────────────────────────────────────────"
    echo "  Config: ${cfg}  temp=${temp}  runs=${B2_RUNS}"
    echo "──────────────────────────────────────────────────────"
    OLLAMA_URL="${OLLAMA_URL}" \
    FAULT_MODE=NONE \
        "${PYTHON}" "${SRCDIR}/b2_systematic_runner.py" \
            --fault-mode NONE \
            --runs "${B2_RUNS}" \
            --cfg "${cfg}" \
            2>&1
    echo "  Done: ${cfg} @ $(date)"
    echo ""
}

if [ -z "${CFG}" ]; then
    run_config "3b_temp0"   "0.0"
    run_config "3b_temp0.7" "0.7"
    run_config "3b_temp1.0" "1.0"
else
    # Determine temperature from config label
    case "${CFG}" in
        3b_temp0)   run_config "${CFG}" "0.0" ;;
        3b_temp0.7) run_config "${CFG}" "0.7" ;;
        3b_temp1.0) run_config "${CFG}" "1.0" ;;
        *)
            echo "[ERROR] Unknown config '${CFG}'. Use: 3b_temp0, 3b_temp0.7, 3b_temp1.0"
            exit 1
            ;;
    esac
fi

# ── Results summary ───────────────────────────────────────────────────────────
echo "========================================================================"
echo "  B2 TRUE ORCHESTRATOR RUNS COMPLETE"
echo "  Results: ${SRCDIR}/results/b2/"
ls -lh "${SRCDIR}/results/b2/"*.json 2>/dev/null | awk '{print "  "$NF, $5}' || true
echo "  Finished: $(date)"
echo "========================================================================"
