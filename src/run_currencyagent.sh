#!/bin/bash
#SBATCH --job-name=currencyagent_fi
#SBATCH --output=currencyagent_%j.log
#SBATCH --error=currencyagent_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=2G
#SBATCH --time=00:05:00

VENV=/speed-scratch/$USER/LLM-MAS/src/shippingservice/.venv
PYTHON=$VENV/bin/python
export PYTHONIOENCODING=utf-8

echo "========================================"
echo "  LKW B2 FAULT INJECTION — CurrencyAgent"
echo "  Job: ${SLURM_JOB_ID:-local}  Node: ${SLURMD_NODENAME:-local}"
echo "  Started: $(date)"
echo "========================================"

# ── Step 1: run true B2 (no LLM, mocked gRPC, all fault modes) ───────────────
cd /speed-scratch/$USER/LLM-MAS/src/currencyagent
$PYTHON -m pip install -q python-dotenv
$PYTHON test_fault_injection.py

# ── Step 2: map results to DU-pair LKW evidence table ────────────────────────
cd /speed-scratch/$USER/LLM-MAS/src/results
$PYTHON parse_lkw_currency_evidence.py

echo "========================================"
echo "  Done: $(date)"
echo "========================================"
