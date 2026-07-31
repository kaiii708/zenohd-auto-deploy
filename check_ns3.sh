#!/usr/bin/env bash
#
# Verify the ns-3 side is in the state the experiment expects, before running it.
#
# ns-3 and the nr module are pinned by verification rather than vendored as
# submodules: contrib/nr is an active multi-branch workspace that gets switched
# between experiment variants, so a submodule pin would report it modified
# constantly and the signal would be ignored. A submodule also only constrains a
# fresh clone -- it cannot tell you whether the tree you are about to run from
# still has the sudo patch applied. This check runs at the moment that matters.
#
# Checks 1-3 and 5 block an experiment; only the ns-3 provenance check (4) warns,
# since that SHA legitimately changes on a rebase while the sudo patch -- the
# thing that actually has to be true -- is verified directly. Keeping that split
# is what makes a green result mean something.

set -uo pipefail

SCRIPT_DIR=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
cd "$SCRIPT_DIR" || exit 1

# --- expected state -----------------------------------------------------------
# nr carries the scenario, so its commit is pinned exactly.
NR_EXPECTED_COMMIT="b1bedf07b43842511f9f84bc6e7faacb1e9b9c28"
NR_EXPECTED_BRANCH="bg-ue-mac-contention"
# ns-3 is branch zenoh-experiment = tag ns-3.44 + this one commit. Advisory only:
# the sudo patch itself is the hard requirement, and this SHA legitimately
# changes if the branch is rebased onto a newer ns-3 release.
NS3_EXPECTED_COMMIT="9175b41fbc5eb0c4f8b9628ba40873a5d160d7fa"
NS3_EXPECTED_BRANCH="zenoh-experiment"
SCENARIO="contrib/nr/examples/nr-mec-3gpp-calibration.cc"

usage() {
  cat <<EOF
Usage: ${0##*/} [--dir PATH]

Verifies the ns-3 checkout used by run_experiment.py:
  1. the ns3 wrapper's root refusal is patched out   (FAIL)
  2. contrib/nr is at the pinned commit              (FAIL)
  3. the scenario source is present                  (FAIL)
  4. ns-3 is on the expected branch/commit           (warn)
  5. the build is optimized, scenario target built   (FAIL)

  --dir PATH   check this checkout instead of ns3_dir from EXPERIMENT_CONFIG.json5
EOF
}

NS3_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dir)     NS3_DIR="${2:-}"; shift 2 ;;
    *)         printf 'error: unknown argument: %s (try --help)\n' "$1" >&2; exit 1 ;;
  esac
done

# Resolve ns3_dir from EXPERIMENT_CONFIG.json5 so this agrees with what
# run_experiment.py actually uses, rather than duplicating the default.
if [[ -z "$NS3_DIR" ]]; then
  PY=""
  for candidate in "$SCRIPT_DIR/venv/bin/python3" python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import json5" 2>/dev/null; then
      PY="$candidate"; break
    fi
  done
  if [[ -n "$PY" ]]; then
    NS3_DIR=$("$PY" -c "
import json5, os, sys
try:
    with open('$SCRIPT_DIR/EXPERIMENT_CONFIG.json5') as f:
        print(os.path.expanduser(json5.load(f).get('ns3_dir', '~/dev/ns-3-dev')))
except Exception:
    sys.exit(1)
" 2>/dev/null)
  fi
  [[ -n "$NS3_DIR" ]] || NS3_DIR="$HOME/dev/ns-3-dev"
fi

fail=0
warn=0
note() { printf '  %s\n' "$*"; }
row()  { printf '%-34s %-6s %s\n' "$1" "$2" "$3"; }

printf '\nns-3 checkout: %s\n\n' "$NS3_DIR"

if [[ ! -d "$NS3_DIR" ]]; then
  printf '\033[1;31merror:\033[0m ns-3 directory does not exist: %s\n' "$NS3_DIR" >&2
  printf 'Set ns3_dir in EXPERIMENT_CONFIG.json5, or pass --dir PATH.\n' >&2
  exit 1
fi

printf '%-34s %-6s %s\n' "CHECK" "RESULT" "DETAIL"
printf '%.0s-' {1..96}; printf '\n'

# --- 1. sudo patch (FAIL) -----------------------------------------------------
# Read the working file, not git: a stash, bad rebase, or upstream merge can
# revert this while the commit still appears in history.
if [[ ! -f "$NS3_DIR/ns3" ]]; then
  row "ns3 sudo patch" "FAIL" "no ns3 wrapper at $NS3_DIR/ns3"
  fail=1
elif grep -qE '^\s*#\s*if\s+username\s*==\s*"root"' "$NS3_DIR/ns3"; then
  row "ns3 sudo patch" "ok" "refuse_run_as_root() is disabled"
elif grep -qE '^\s*if\s+username\s*==\s*"root"' "$NS3_DIR/ns3"; then
  row "ns3 sudo patch" "FAIL" "refuse_run_as_root() is ACTIVE -- sudo ./ns3 run will abort"
  note "run_experiment.py runs 'sudo ... ./ns3 run', which this blocks."
  note "Expected branch $NS3_EXPECTED_BRANCH (ns-3.44 + 'Allow ns3 to run under sudo')."
  fail=1
else
  row "ns3 sudo patch" "warn" "could not find refuse_run_as_root() -- ns-3 version changed?"
  warn=1
fi

# --- 2. nr commit (FAIL) ------------------------------------------------------
nr_dir="$NS3_DIR/contrib/nr"
if [[ ! -d "$nr_dir/.git" ]]; then
  row "contrib/nr commit" "FAIL" "not a git checkout: $nr_dir"
  fail=1
else
  nr_head=$(git -C "$nr_dir" rev-parse HEAD 2>/dev/null)
  nr_branch=$(git -C "$nr_dir" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [[ "$nr_branch" == "HEAD" ]] && nr_branch="detached"
  if [[ "$nr_head" == "$NR_EXPECTED_COMMIT" ]]; then
    row "contrib/nr commit" "ok" "${nr_head:0:9} ($nr_branch)"
  else
    row "contrib/nr commit" "FAIL" "${nr_head:0:9} ($nr_branch)"
    note "expected ${NR_EXPECTED_COMMIT:0:9} ($NR_EXPECTED_BRANCH)"
    note "fix: git -C $nr_dir checkout $NR_EXPECTED_BRANCH"
    fail=1
  fi
fi

# --- 3. scenario source (FAIL) ------------------------------------------------
if [[ -f "$NS3_DIR/$SCENARIO" ]]; then
  row "scenario source" "ok" "$SCENARIO"
else
  row "scenario source" "FAIL" "missing: $SCENARIO"
  note "run_experiment.py runs 'nr-mec-3gpp-calibration'; without this it cannot build."
  fail=1
fi

# --- 4. ns-3 provenance (warn) ------------------------------------------------
if [[ ! -d "$NS3_DIR/.git" ]]; then
  row "ns-3 provenance" "warn" "not a git checkout"
  warn=1
else
  ns3_head=$(git -C "$NS3_DIR" rev-parse HEAD 2>/dev/null)
  ns3_branch=$(git -C "$NS3_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [[ "$ns3_head" == "$NS3_EXPECTED_COMMIT" ]]; then
    row "ns-3 provenance" "ok" "${ns3_head:0:9} ($ns3_branch)"
  elif git -C "$NS3_DIR" merge-base --is-ancestor "$NS3_EXPECTED_COMMIT" HEAD 2>/dev/null; then
    row "ns-3 provenance" "ok" "${ns3_head:0:9} ($ns3_branch) -- contains the pinned commit"
  else
    row "ns-3 provenance" "warn" "${ns3_head:0:9} ($ns3_branch)"
    note "expected ${NS3_EXPECTED_COMMIT:0:9} ($NS3_EXPECTED_BRANCH), or a descendant of it."
    note "Not fatal: check 1 verifies what actually matters. Update the pin here if rebased."
    warn=1
  fi
fi

# --- 5. build profile + scenario target (FAIL) --------------------------------
# Both read from .lock-ns3_linux_build, which ns-3 rewrites on every configure.
# That is equivalent to `./ns3 show targets | grep nr` but instant, since it
# avoids spawning ns-3's CMake front end.
#
# The profile matters for results, not just speed: a debug build carries
# assertions and no optimisation, so timing-sensitive measurements (convergence,
# packet loss under handover) are not comparable with optimized-build numbers.
# Running the experiment against a debug build silently produces wrong figures,
# so this fails rather than warns.
lock="$NS3_DIR/.lock-ns3_linux_build"
if [[ ! -f "$lock" ]]; then
  row "ns-3 build" "FAIL" "not configured -- no .lock-ns3_linux_build"
  note "run: ./ns3 configure -d optimized --enable-examples && ./ns3 build"
  fail=1
else
  profile=$(grep -oP "BUILD_PROFILE = '\K[^']+" "$lock" 2>/dev/null)
  if [[ "$profile" == "optimized" ]]; then
    row "ns-3 build profile" "ok" "optimized"
  else
    row "ns-3 build profile" "FAIL" "${profile:-unknown} -- measurements will not be comparable"
    note "run: ./ns3 configure -d optimized --enable-examples && ./ns3 build"
    fail=1
  fi

  if grep -q "nr-mec-3gpp-calibration" "$lock" 2>/dev/null; then
    row "scenario target built" "ok" "nr-mec-3gpp-calibration"
  else
    row "scenario target built" "FAIL" "nr-mec-3gpp-calibration not among built targets"
    note "verify with: (cd $NS3_DIR && ./ns3 show targets | grep nr)"
    note "if absent, configure with --enable-examples and rebuild."
    fail=1
  fi
fi

printf '\n'
if [[ $fail -ne 0 ]]; then
  printf '\033[1;31mFAIL\033[0m -- fix the above before running experiments.\n'
  exit 1
fi
if [[ $warn -ne 0 ]]; then
  printf '\033[1;33mOK with warnings\033[0m -- safe to run, but review the notes above.\n'
  exit 0
fi
printf '\033[1;32mns-3 checkout matches the expected experiment state.\033[0m\n'
