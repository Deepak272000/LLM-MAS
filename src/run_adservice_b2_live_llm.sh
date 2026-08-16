#!/bin/bash
#SBATCH --job-name=adservice_b2_llm
#SBATCH --output=adservice_b2_llm_%j.log
#SBATCH --error=adservice_b2_llm_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=4G
#SBATCH --time=00:45:00
#SBATCH --gres=gpu:1

# AdServiceAgent B2 live-LLM run  (USE_LLM=true, FAULT_MODE=NONE, 10 runs)
# Requires Ollama running with qwen2.5:3b loaded on this node.

VENV=/speed-scratch/$USER/LLM-MAS/src/shippingservice/.venv
PYTHON=$VENV/bin/python
SRC=/speed-scratch/$USER/LLM-MAS/src

# Pull latest so new script is present
git -C /speed-scratch/$USER/LLM-MAS pull origin deepak/fault-injection 2>/dev/null || true

# Ensure Ollama is running; if not, start it in background
if ! curl -sf http://localhost:11434 > /dev/null 2>&1; then
    echo "Starting ollama..."
    ollama serve &
    OLLAMA_PID=$!
    sleep 8
fi

# Make sure qwen2.5:3b is pulled
ollama pull qwen2.5:3b

echo "=== AdService B2 live-LLM runner starting ==="
echo "Python: $PYTHON"
echo "SLURM job: $SLURM_JOB_ID"
date

$PYTHON -m pip install -q python-dotenv 2>/dev/null

cd $SRC
$PYTHON run_adservice_b2_live_llm.py \
    --runs 10 \
    --model qwen2.5:3b \
    --temp 0.0 \
    --ollama-url http://localhost:11434

echo "=== Done ==="
date
