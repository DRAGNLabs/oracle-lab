"""Fixed locations inside the containers, shared by more than one module.

These are container paths, not host paths; `oracle_lab.paths` names the host's
output layout. Scratch files that one function writes and reads stay beside that
function, and each harness adapter keeps its own CLI's paths.
"""

# The non-root `oracle` user's home, created by `runtime.Dockerfile`.
ORACLE_HOME = "/home/oracle"
# Where `runtime.Dockerfile` copies `container/helpers/`.
HELPER_DIR = "/opt/oracle-lab/container-helpers"
# The privileged workspaces each LLM judge reads. The rubrics name these paths.
JUDGE_ROOT = "/oracle-judge"
CLASSIFICATION_ROOT = "/oracle-classification"
