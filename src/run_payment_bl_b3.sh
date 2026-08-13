#!/bin/bash
#SBATCH --job-name=pay_bl_b3
#SBATCH --output=/speed-scratch/%u/logs/pay_bl_b3_%j.log
#SBATCH --error=/speed-scratch/%u/logs/pay_bl_b3_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=02:00:00
#SBATCH --partition=pt
#SBATCH --gres=gpu:1

# ─────────────────────────────────────────────────────────────────────────────
#  PaymentAgent — B3 Campaign for Missing BL Faults
#
#  PURPOSE
#  -------
#  Runs B3 (live LLM temp=1.0, 3 runs) for the three payment-specific BL faults
#  that were missing from the original B3 campaign:
#    BL_TRANSACTION_LOST  — MongoDB save silently skipped
#    BL_DOUBLE_CHARGE     — duplicate charge injected into save payload
#    BL_CARD_DECLINED     — force CreditCardError before charge_payment
#
#  These faults are targeted to the "payment" agent only.
#  After the runs, re-runs parse_lkw_payment_evidence.py so the B3 columns
#  in Table B can be updated with real observed values.
#
#  Pre-requisite: Ollama must be running with qwen2.5:3b loaded.
#    Start with:  ollama serve &  (in a separate session or sbatch)
#
#  Usage:
#    sbatch src/run_payment_bl_b3.sh
#
#  To run only one fault:
#    sbatch --export=FAULT_MODE=BL_TRANSACTION_LOST src/run_payment_bl_b3.sh
#
#  Output:
#    src/results/b3/raw/b3_{FAULT}_3b_temp1.0_run{1,2,3}.json
#    src/results/lkw_payment_evidence.json  (updated)
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}" "${SRCDIR}/results/b3/raw"

export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export MODEL_3B="${MODEL_3B:-qwen2.5:3b}"

# Faults to run (override via --export)
FAULT_MODE="${FAULT_MODE:-}"   # empty = all 3 missing BL faults
B3_RUNS="${B3_RUNS:-3}"
CFG="${CFG:-3b_temp1.0}"

PAYMENT_BL_FAULTS=(
    "BL_TRANSACTION_LOST"
    "BL_DOUBLE_CHARGE"
    "BL_CARD_DECLINED"
)

echo "========================================================================"
echo "  PaymentAgent B3 Campaign — Missing BL Faults"
echo "  Host    : $(hostname)"
echo "  Date    : $(date)"
echo "  OllamaURL: ${OLLAMA_URL}"
echo "  Model 3b : ${MODEL_3B}"
echo "  Config   : ${CFG}"
echo "  Runs     : ${B3_RUNS}"
echo "========================================================================"

if [[ ! -x "${PYTHON}" ]]; then
    echo "ERROR: Python not found at ${PYTHON}"
    exit 1
fi

# ── Check Ollama is up ────────────────────────────────────────────────────────
echo "── Checking Ollama connectivity ─────────────────────────────────────────"
OLLAMA_OK=false
for attempt in 1 2 3 4 5; do
    if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
        OLLAMA_OK=true
        echo "  Ollama reachable at ${OLLAMA_URL} (attempt ${attempt})"
        break
    fi
    echo "  Waiting for Ollama... (attempt ${attempt}/5)"
    sleep 10
done

if [[ "${OLLAMA_OK}" != "true" ]]; then
    echo "ERROR: Ollama not reachable at ${OLLAMA_URL} after 5 attempts."
    echo "       Start Ollama before submitting this job, or pre-load model in sbatch prolog."
    exit 1
fi

# ── Determine which faults to run ─────────────────────────────────────────────
if [[ -n "${FAULT_MODE}" ]]; then
    FAULTS_TO_RUN=("${FAULT_MODE}")
    echo "── Running single fault: ${FAULT_MODE}"
else
    FAULTS_TO_RUN=("${PAYMENT_BL_FAULTS[@]}")
    echo "── Running all 3 missing payment BL faults"
fi

cd "${SRCDIR}"

# ── Run B3 for each fault ─────────────────────────────────────────────────────
for FAULT in "${FAULTS_TO_RUN[@]}"; do
    echo ""
    echo "── B3 campaign: ${FAULT} (targeted → payment, cfg=${CFG}, runs=${B3_RUNS}) ──"

    ${PYTHON} b3_runner.py \
        --fault-mode "${FAULT}" \
        --fault-agent payment \
        --cfg "${CFG}" \
        --runs "${B3_RUNS}" \
        2>&1

    EXIT_CODE=$?
    if [[ ${EXIT_CODE} -ne 0 ]]; then
        echo "WARNING: b3_runner.py exited ${EXIT_CODE} for ${FAULT}"
    else
        echo "  ${FAULT} complete."
    fi
done

# ── Re-parse evidence with new B3 data ────────────────────────────────────────
echo ""
echo "── Re-parsing LKW evidence (Table B update) ─────────────────────────────"
cd "${SRCDIR}/results"
${PYTHON} parse_lkw_payment_evidence.py

echo ""
echo "========================================================================"
echo "  B3 campaign complete.  Updated evidence: ${SRCDIR}/results/lkw_payment_evidence.json"
echo "  Review B3 column values for TC-PAY-06, TC-PAY-07, TC-PAY-09"
echo "  and update paper_updated.tex Table B accordingly."
echo "========================================================================"
