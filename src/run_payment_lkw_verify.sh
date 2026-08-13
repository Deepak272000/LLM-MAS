#!/bin/bash
#SBATCH --job-name=pay_lkw_verify
#SBATCH --output=/speed-scratch/%u/logs/pay_lkw_verify_%j.log
#SBATCH --error=/speed-scratch/%u/logs/pay_lkw_verify_%j.err
#SBATCH --ntasks=1
#SBATCH --mem=4G
#SBATCH --time=00:10:00
#SBATCH --partition=pg

# ─────────────────────────────────────────────────────────────────────────────
#  PaymentAgent — LKW DU-Pair Verification (Table B)
#
#  PURPOSE
#  -------
#  Re-runs B2 fault injection tests and parses all LKW evidence for PaymentAgent.
#  Confirms that the TC-PAY-01..09 values in paper_updated.tex Table B match
#  the actual execution artifacts on the Speed cluster.
#
#  No GPU required — this is read-only verification (no LLM inference).
#
#  Usage:
#    sbatch src/run_payment_lkw_verify.sh
#
#  Output:
#    /speed-scratch/$USER/logs/pay_lkw_verify_<jobid>.log
#    src/results/lkw_payment_evidence.json  (regenerated from Speed data)
#
#  After the job completes:
#    squeue -u $USER                       # check job status
#    tail /speed-scratch/$USER/logs/pay_lkw_verify_<jobid>.log
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

SCRATCH="/speed-scratch/${USER}"
SRCDIR="${SCRATCH}/LLM-MAS/src"
VENV="${SCRATCH}/LLM-MAS/src/shippingservice/.venv"
PYTHON="${VENV}/bin/python"
LOGDIR="${SCRATCH}/logs"
mkdir -p "${LOGDIR}"

echo "========================================================================"
echo "  PaymentAgent LKW DU-Pair Verification — Table B"
echo "  Host  : $(hostname)"
echo "  Date  : $(date)"
echo "  User  : ${USER}"
echo "  Python: ${PYTHON}"
echo "========================================================================"

# ── 0. Sanity check ───────────────────────────────────────────────────────────
if [[ ! -x "${PYTHON}" ]]; then
    echo "ERROR: Python not found at ${PYTHON}"
    exit 1
fi

cd "${SRCDIR}"

# ── 1. Re-generate B2 fault results ──────────────────────────────────────────
echo ""
echo "── Step 1: Re-run B2 fault injection (no LLM, direct agent.run()) ──────"
cd "${SRCDIR}/paymentagent"
${PYTHON} -m pip install -q python-dotenv motor pymongo 2>/dev/null || true
${PYTHON} test_fault_injection.py
B2_EXIT=$?
if [[ ${B2_EXIT} -ne 0 ]]; then
    echo "WARNING: test_fault_injection.py exited with code ${B2_EXIT}"
else
    echo "B2 regeneration complete."
fi

# ── 2. Verify B3 raw file inventory ──────────────────────────────────────────
echo ""
echo "── Step 2: B3 raw file inventory ───────────────────────────────────────"
cd "${SRCDIR}/results"
${PYTHON} - << 'PYEOF'
import os

faults = [
    'FM_3_1','FM_2_2','FM_2_5','FM_1_2',
    'BL_TRANSACTION_LOST','BL_DOUBLE_CHARGE',
    'BL_CARD_DECLINED','BL_AMOUNT_TAMPERING'
]
temps = ['temp0', 'temp0.7', 'temp1.0']

print("  {:<30}  {}".format("Fault", "  ".join(t.ljust(18) for t in temps)))
print("  " + "-"*75)
for fault in faults:
    row = "  {:<30}".format(fault)
    for t in temps:
        found = [os.path.exists('b3/raw/b3_{}_{}_run{}.json'.format(fault, '3b_'+t, r)) for r in [1,2,3]]
        status = '[' + ','.join('Y' if f else '-' for f in found) + ']'
        row += "  " + status.ljust(18)
    print(row)
PYEOF

# ── 3. Parse all LKW evidence and verify against paper values ─────────────────
echo ""
echo "── Step 3: Parse LKW evidence (Table B verification) ───────────────────"
cd "${SRCDIR}/results"
${PYTHON} parse_lkw_payment_evidence.py

# ── 4. Diff check: expected paper values vs observed ─────────────────────────
echo ""
echo "── Step 4: Table B consistency check ───────────────────────────────────"
${PYTHON} - << 'PYEOF'
import json

EXPECTED = {
    "TC-PAY-01": {"b1": 10,     "b2": None,  "b3": None,           "verdict": "PASS"},
    "TC-PAY-02": {"b1": 10,     "b2": 30,    "b3_run3": 114,       "verdict": "FAIL"},
    "TC-PAY-03": {"b1": "UUID", "b2": None,  "b3": None,           "verdict": "PATH_INFEASIBLE"},
    "TC-PAY-04": {"b1": False,  "b2": True,  "b3": [True,True,True],"verdict": "FAIL"},
    "TC-PAY-05": {"b1": False,  "b2": True,  "b3": [True,True,True],"verdict": "FAIL"},
    "TC-PAY-06": {"b1": False,  "b2": True,  "b3": "NOT_MEASURED", "verdict": "FAIL"},
    "TC-PAY-07": {"b1": False,  "b2": True,  "b3": "NOT_MEASURED", "verdict": "FAIL"},
    "TC-PAY-08": {"b1": 10,     "b2": 30,    "b3_run3": 240,       "verdict": "FAIL"},
    "TC-PAY-09": {"b1": "reached","b2": None,"b3": "NOT_MEASURED", "verdict": "PATH_INFEASIBLE"},
}

with open('lkw_payment_evidence.json') as f:
    data = json.load(f)

passed = 0
failed = 0
for rec in data.get('evidence', []):
    tc = rec['tc_id']
    exp = EXPECTED.get(tc, {})
    if not exp:
        continue

    ok = True
    notes = []

    # B1 oracle check (skip UUID and reached cases)
    if exp['b1'] not in ('UUID', 'reached'):
        if rec.get('b1_oracle') != exp['b1']:
            ok = False
            notes.append('B1 oracle mismatch: got={} expected={}'.format(rec.get('b1_oracle'), exp['b1']))

    # B2 verdict
    b2_obs = rec.get('b2_observed')
    if exp['b2'] is None:  # expect PATH_INFEASIBLE
        if b2_obs is not None:
            ok = False
            notes.append('B2: expected PATH_INFEAS but got {}'.format(b2_obs))
    elif exp['b2'] is True:
        if b2_obs is not True:
            ok = False
            notes.append('B2: expected True but got {}'.format(b2_obs))
    elif isinstance(exp['b2'], int):
        if b2_obs != exp['b2']:
            ok = False
            notes.append('B2: expected {} but got {}'.format(exp['b2'], b2_obs))

    # B3 run3 spot-check where expected
    if 'b3_run3' in exp:
        b3_obs = rec.get('b3_observed', [])
        if isinstance(b3_obs, list) and len(b3_obs) >= 3:
            b3_r3 = b3_obs[2]
            if b3_r3 != exp['b3_run3']:
                ok = False
                notes.append('B3 run3: expected {} but got {}'.format(exp['b3_run3'], b3_r3))

    verdict_ok = exp['verdict'] in (rec.get('lkw_verdict', ''), rec.get('b2_verdict', ''))

    status = 'PASS' if (ok and verdict_ok) else 'FAIL'
    if status == 'PASS':
        passed += 1
    else:
        failed += 1
        if not ok:
            for n in notes:
                print('  [MISMATCH] {} : {}'.format(tc, n))
        if not verdict_ok:
            print('  [VERDICT]  {} : expected={} got={}'.format(tc, exp['verdict'], rec.get('lkw_verdict')))

    print('  {} {} {}'.format(status, tc, '' if (ok and verdict_ok) else '<<<'))

print()
print('  Summary: {}/{} test cases match paper Table B'.format(passed, passed+failed))
if failed == 0:
    print('  ALL PASS — Table B values are consistent with Speed cluster data')
else:
    print('  ATTENTION: {} case(s) differ — check paper_updated.tex Table B'.format(failed))
PYEOF

echo ""
echo "========================================================================"
echo "  PaymentAgent LKW verification complete."
echo "  Log: /speed-scratch/${USER}/logs/pay_lkw_verify_${SLURM_JOB_ID}.log"
echo "========================================================================"
