"""
Restructure paper_updated.tex to match professor's requested section order:
  §1 Introduction
  §2 Related Work          (was near end)
  §3 Benchmarking Microservices and Agentification  (was §2 "System and Fault Model")
  §4 LKW and RIP Checkpoint Design  (was §5, with §4 Metrics folded in as subsection)
  §5 Fault Injection as Mutant Operators  (was §2.2 subsection + §3 section)
  §6+ Experimental Setup, Results, etc.  (unchanged)
"""

import re, shutil, os

SRC = r'e:\Summer ai Agent Project\LLM-MAS\src\results\paper_updated.tex'
OUT = r'e:\Summer ai Agent Project\LLM-MAS\src\results\paper_updated.tex'
BAK = SRC + '.bak'

shutil.copy2(SRC, BAK)
print(f"Backup saved: {BAK}")

with open(SRC, encoding='utf-8') as f:
    text = f.read()

# ─── Locate exact block boundaries using unique anchor strings ────────────────

# Pattern that marks the *start* of the separator + section block
SEP = '% ' + '═' * 77

def find(anchor):
    idx = text.find(anchor)
    if idx == -1:
        raise ValueError(f"Anchor not found: {repr(anchor[:60])}")
    return idx

# ── Find each block start ─────────────────────────────────────────────────────
# Introduction content ends with \end{description} just before §2 separator
# We keep everything before §2's separator as "preamble + intro"
intro_end_anchor    = '\n' + SEP + '\n\\section{System and Fault Model}'
related_work_anchor = '\n' + SEP + '\n\\section{Related Work}'
clearpage_anchor    = '\n\\clearpage  % flush all remaining floats'
fault_tax_anchor    = '\n\\subsection{Fault Taxonomy and Mutation Operators}'
fail_inject_anchor  = '\n' + SEP + '\n\\section{Failure Injection and Analysis Workflow}'
metrics_anchor      = '\n' + SEP + '\n\\section{Metrics and Definitions}'
lkw_anchor          = '\n' + SEP + '\n\\section{LKW Instrumentation Design}'
exp_setup_anchor    = '\n' + SEP + '\n\\section{Experimental Setup}'

# Positions (start of each anchor string)
p_intro_end     = find(intro_end_anchor)
p_related       = find(related_work_anchor)
p_clearpage     = find(clearpage_anchor)
p_fault_tax     = find(fault_tax_anchor)
p_fail_inject   = find(fail_inject_anchor)
p_metrics       = find(metrics_anchor)
p_lkw           = find(lkw_anchor)
p_exp_setup     = find(exp_setup_anchor)

print("Block start positions:")
print(f"  intro_end (before §2 sep):  {p_intro_end}")
print(f"  fault_taxonomy:             {p_fault_tax}")
print(f"  failure_injection:          {p_fail_inject}")
print(f"  metrics:                    {p_metrics}")
print(f"  lkw:                        {p_lkw}")
print(f"  exp_setup:                  {p_exp_setup}")
print(f"  related_work:               {p_related}")
print(f"  clearpage:                  {p_clearpage}")

# ─── Extract blocks ───────────────────────────────────────────────────────────

# Block 0: Everything up to (not including) §System and Fault Model separator
# = preamble + abstract + introduction
block_pre_s2 = text[:p_intro_end]

# Block 3 content: §System and Fault Model header + §Target Workflow subsection
# From start of §System separator to just before §Fault Taxonomy subsection
block_s2_header_and_target = text[p_intro_end : p_fault_tax]

# Block 5a content: §Fault Taxonomy subsection
# From §Fault Taxonomy to just before §Failure Injection separator
block_fault_taxonomy = text[p_fault_tax : p_fail_inject]

# Block 5b content: §Failure Injection section
# From §Failure Injection separator to just before §Metrics separator
block_failure_injection = text[p_fail_inject : p_metrics]

# Block 4a content: §Metrics section
# From §Metrics separator to just before §LKW separator
block_metrics = text[p_metrics : p_lkw]

# Block 4 content: §LKW section
# From §LKW separator to just before §Experimental Setup separator
block_lkw = text[p_lkw : p_exp_setup]

# Block 6+: §Experimental Setup through end (but WITHOUT Related Work)
# = exp_setup to related_work + clearpage_to_end
block_exp_and_rest_before_rw = text[p_exp_setup : p_related]
block_rw_onwards = text[p_related : p_clearpage]          # = Related Work text
block_clearpage_to_end = text[p_clearpage:]               # \clearpage + Conclusion

# ─── Rename and restructure headers ──────────────────────────────────────────

# §3 rename: "System and Fault Model" → "Benchmarking Microservices and Agentification"
block_s3 = block_s2_header_and_target.replace(
    '\\section{System and Fault Model}',
    '\\section{Benchmarking Microservices and Agentification}\n\\label{sec:benchmarks}'
)

# ─── Build §4: LKW and RIP Checkpoint Design ─────────────────────────────────
# metrics block starts with "\n% SEP\n\section{Metrics...}\n% SEP\n"
# Extract just the body after the section header line
metrics_body_start = block_metrics.find('\n', block_metrics.find('\\section{Metrics')) + 1
# skip the trailing SEP line of the section header
metrics_body_start2 = block_metrics.find('\n', metrics_body_start) + 1
metrics_content = block_metrics[metrics_body_start2:]  # body: bullets + equations

# lkw block starts with "\n% SEP\n\section{LKW Instrumentation Design}\n\label{sec:lkw-design}\n% SEP\n"
# Extract just the subsections (everything after the opening SEP + section header block)
lkw_body_start = block_lkw.find('\\subsection{Data Flow')
lkw_content = block_lkw[lkw_body_start:]  # all LKW subsections

block_s4 = (
    '\n' + SEP + '\n'
    '\\section{LKW and RIP Checkpoint Design}\n'
    '\\label{sec:lkw-design}\n'
    + SEP + '\n\n'
    '\\subsection{RIP Metrics and Definitions}\n\n'
    + metrics_content
    + '\n'
    + lkw_content
)

# ─── Build §5: Fault Injection as Mutant Operators ───────────────────────────
# fault_taxonomy block starts with "\n\subsection{Fault Taxonomy...}"
# We keep the subsection heading and body, just wrap in a new section
taxonomy_body = block_fault_taxonomy  # includes \n\subsection{...} and full content

# failure_injection block starts with "\n% SEP\n\section{Failure Injection...}\n% SEP\n"
# Extract body after its section header
fi_header_end = block_failure_injection.find('\\section{Failure Injection')
fi_after_sep  = block_failure_injection.find('\n', fi_header_end) + 1
fi_after_sep2 = block_failure_injection.find('\n', fi_after_sep) + 1  # skip trailing SEP line
fi_content    = block_failure_injection[fi_after_sep2:]

block_s5 = (
    '\n' + SEP + '\n'
    '\\section{Fault Injection as Mutant Operators}\n'
    '\\label{sec:fault-injection}\n'
    + SEP + '\n'
    + taxonomy_body
    + '\n\\subsection{Fault Injection Pipeline}\n'
    + fi_content
)

# ─── Reassemble ───────────────────────────────────────────────────────────────

new_text = (
    block_pre_s2                    # §1 Introduction (+ preamble)
    + block_rw_onwards              # §2 Related Work  (moved from end)
    + block_s3                      # §3 Benchmarking Microservices
    + block_s4                      # §4 LKW and RIP Checkpoint Design
    + block_s5                      # §5 Fault Injection as Mutant Operators
    + block_exp_and_rest_before_rw  # §6+ Experimental Setup + all results
    + block_clearpage_to_end        # \clearpage + Conclusion + bibliography
)

# ─── Write output ─────────────────────────────────────────────────────────────
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(new_text)

print(f"\nDone. Output written to: {OUT}")
print(f"Original lines: {text.count(chr(10))}")
print(f"New lines:      {new_text.count(chr(10))}")

# Sanity checks
for label in ['fig:architecture', 'fig:workflow', 'tab:fault-mapping',
              'tab:service-coverage', 'sec:lkw-design', 'sec:b1-oracle',
              'sec:related', 'tab:per-agent-ckpts']:
    count = new_text.count('\\label{' + label + '}')
    if count != 1:
        print(f"WARNING: \\label{{{label}}} appears {count} times (expected 1)")
    else:
        print(f"  OK: \\label{{{label}}}")
