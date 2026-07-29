#!/usr/bin/env bash
#
# Cross-build the six experiment branches into per-branch target directories.
#
# Each branch is checked out in place, built, and the starting branch is restored
# at the end (including on Ctrl-C). Artifacts land in:
#
#   target/<branch>/x86_64-unknown-linux-musl/release/zenohd
#   target/<branch>/x86_64-unknown-linux-musl/release/examples/{z_pre_sub|z_sub, my_z_pub}
#
# CARGO_TARGET_DIR is set per branch because the baseline branches commit a
# .cargo/config.toml with `target-dir = "target/baseline"`, which would otherwise
# make all three of them clobber each other. The env var takes precedence over
# that config, and `cross` propagates it through the container boundary.
#
# This script lives in the zenohd-auto-deploy repo and builds the `zenoh`
# submodule beside it. That separation matters: the script performs `git checkout`
# inside zenoh/, and bash reads a script incrementally as it executes -- so a
# script living *inside* the tree it checks out risks being replaced mid-run.
# Here it is tracked and versioned in the parent, safely outside that churn.
#
# PORTABILITY: the repo path is auto-detected (see detect_repo), so a fresh
# `git clone --recurse-submodules` works with no configuration on any machine.
# Set ZENOH_DIR only to build a zenoh checkout other than the submodule.

set -euo pipefail

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Build target. Fixed by design -- the experiment deploys into x86_64 musl
# containers regardless of what the build host is -- but overridable if that
# ever changes.
TRIPLE="${ZENOH_BUILD_TARGET:-x86_64-unknown-linux-musl}"

# Directory holding this script. readlink -f resolves a symlinked script back to
# its real location, so `ln -s` into ~/bin still finds the right repo.
SCRIPT_DIR=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")

# Locate the zenoh checkout to build:
#   1. the `zenoh` submodule beside this script -- the normal case
#   2. the git toplevel containing this script, so the script still works if
#      dropped directly into a zenoh checkout (its previous home)
# Note that (2) alone would be wrong from the parent repo root: there
# `rev-parse --show-toplevel` returns zenohd-auto-deploy, not zenoh.
detect_repo() {
  if git -C "$SCRIPT_DIR/zenoh" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$SCRIPT_DIR/zenoh" rev-parse --show-toplevel
    return 0
  fi
  git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true
}

ZENOH_DIR="${ZENOH_DIR:-$(detect_repo)}"
[[ -n "$ZENOH_DIR" ]] || die "cannot locate the zenoh checkout. Expected a 'zenoh' submodule next
to this script -- run 'git submodule update --init' -- or set ZENOH_DIR:
  ZENOH_DIR=/path/to/zenoh ${0##*/}"

# Logs live beside the script in the parent repo, deliberately NOT inside the
# submodule: build logs belong with the experiment data, and writing them into
# zenoh/ would add untracked files to a tree this script needs to keep clean.
LOG_DIR="$SCRIPT_DIR/logs"

# Ordered so each experiment's two arms build back to back.
ALL_BRANCHES=(
  pre-subscribe                     ablation-baseline
  presub-convergence-measurement    baseline-convergence-measurement
  presub-overhead-evaluation        baseline-overhead-evaluation
)

# The only thing that differs between the two arms: which subscriber example to build.
declare -A SUB_EXAMPLE=(
  [pre-subscribe]=z_pre_sub
  [presub-convergence-measurement]=z_pre_sub
  [presub-overhead-evaluation]=z_pre_sub
  [ablation-baseline]=z_sub
  [baseline-convergence-measurement]=z_sub
  [baseline-overhead-evaluation]=z_sub
)

declare -A STATUS=()
declare -A DETAIL=()
ORIG_REF=""
RUN_START=""

usage() {
  cat <<EOF
Usage: ${0##*/} [branch ...]

Cross-builds each branch into target/<branch>/. With no arguments, builds all six.

Zenoh checkout: $ZENOH_DIR
Build logs:     $LOG_DIR
  (auto-detected; override the checkout with ZENOH_DIR=/path)

Branches:
EOF
  local b
  for b in "${ALL_BRANCHES[@]}"; do
    printf '  %-34s (subscriber example: %s)\n' "$b" "${SUB_EXAMPLE[$b]}"
  done
  cat <<EOF

Examples:
  ${0##*/}                                          # all six
  ${0##*/} pre-subscribe                            # one arm
  ${0##*/} presub-overhead-evaluation baseline-overhead-evaluation
EOF
}

# ---------------------------------------------------------------- arguments --

BRANCHES=()
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    -*)        die "unknown option: $arg (try --help)" ;;
    *)
      [[ -v SUB_EXAMPLE[$arg] ]] || {
        printf '\033[1;31merror:\033[0m unknown branch: %s\n\nValid branches:\n' "$arg" >&2
        printf '  %s\n' "${ALL_BRANCHES[@]}" >&2
        exit 1
      }
      BRANCHES+=("$arg")
      ;;
  esac
done
[[ ${#BRANCHES[@]} -gt 0 ]] || BRANCHES=("${ALL_BRANCHES[@]}")

# ---------------------------------------------------------------- preflight --
# Everything here runs before the first checkout, so a bad run aborts having
# changed nothing.

cd "$ZENOH_DIR" || die "cannot cd to $ZENOH_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$ZENOH_DIR is not a git work tree"

# Untracked files (your notes, scratch examples) are expected and must not block
# the run; modified *tracked* files would be carried onto the next branch.
dirty=$(git status --porcelain --untracked-files=no)
[[ -z "$dirty" ]] || die "working tree has modified tracked files -- commit or stash first:
$dirty"

if ORIG_REF=$(git symbolic-ref --quiet --short HEAD 2>/dev/null); then
  :
else
  ORIG_REF=$(git rev-parse HEAD)
  warn "HEAD is detached; will restore to $ORIG_REF"
fi

# A fresh `git clone --recurse-submodules` checks out only the default branch, so
# the six experiment branches exist purely as origin/<branch> remote refs. Create
# the local tracking branches on demand -- otherwise this script would work only
# on a machine where they had been checked out by hand at some point, which is
# exactly the portability problem it exists to solve.
for b in "${BRANCHES[@]}"; do
  git rev-parse --verify --quiet "refs/heads/$b" >/dev/null && continue
  if git rev-parse --verify --quiet "refs/remotes/origin/$b" >/dev/null; then
    log "Creating local branch $b tracking origin/$b"
    git branch --quiet --track "$b" "refs/remotes/origin/$b" \
      || die "could not create local branch: $b"
  else
    die "branch not found locally or on origin: $b
Try 'git -C $ZENOH_DIR fetch origin' if it was pushed recently."
  fi
done

command -v cross >/dev/null 2>&1 || die "cross not found in PATH"
docker info >/dev/null 2>&1 || die "docker daemon not reachable (cross needs it)"

mkdir -p "$LOG_DIR"

# ------------------------------------------------------------ restore guard --

restore_branch() {
  local rc=$?
  trap - EXIT INT TERM
  [[ -n "$RUN_START" ]] && rm -f "$RUN_START"
  if [[ -n "$ORIG_REF" ]]; then
    local current
    current=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || git rev-parse HEAD)
    if [[ "$current" != "$ORIG_REF" ]]; then
      log "Restoring original ref: $ORIG_REF"
      git checkout --quiet "$ORIG_REF" || warn "FAILED to restore $ORIG_REF -- you are on $current"
    fi
  fi
  exit "$rc"
}
trap restore_branch EXIT INT TERM

# ----------------------------------------------------------------- building --

# mtime of a file, or "-" if missing.
mtime_of() {
  [[ -f "$1" ]] && date -r "$1" '+%Y-%m-%d %H:%M:%S' || echo "-"
}

build_branch() {
  local branch="$1"
  local example="${SUB_EXAMPLE[$branch]}"
  local out="$ZENOH_DIR/target/$branch"
  local rel="$out/$TRIPLE/release"
  local logfile="$LOG_DIR/build-$branch-$(date +%Y%m%d-%H%M%S).log"

  log "Building $branch -> target/$branch  (log: ${logfile#$ZENOH_DIR/})"
  git checkout --quiet "$branch" || { DETAIL[$branch]="checkout failed"; return 1; }

  local -a builds=(
    ""                        # workspace: zenohd + plugins
    "--example $example"
    "--example my_z_pub"
  )
  local extra
  for extra in "${builds[@]}"; do
    printf '\n--- cross build --release --target %s %s ---\n' "$TRIPLE" "$extra" | tee -a "$logfile"
    # Word splitting on $extra is intentional: it is a fixed literal above.
    # shellcheck disable=SC2086
    if ! CARGO_TARGET_DIR="$out" cross build --release --target "$TRIPLE" $extra 2>&1 | tee -a "$logfile"; then
      DETAIL[$branch]="cross build failed${extra:+ ($extra)}; see ${logfile#$ZENOH_DIR/}"
      return 1
    fi
  done

  # Verify the expected binaries actually landed. z_pre_sub and my_z_pub are not
  # declared in examples/Cargo.toml -- they come from Cargo's auto-discovery of
  # examples/examples/*.rs -- so a missing source file is a realistic failure mode
  # that must not be mistaken for a stale binary from a previous build.
  local -a expected=(
    "$rel/zenohd"
    "$rel/examples/$example"
    "$rel/examples/my_z_pub"
  )
  local missing=() f
  for f in "${expected[@]}"; do
    [[ -f "$f" ]] || missing+=("${f#$out/}")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    DETAIL[$branch]="missing binaries: ${missing[*]}"
    return 1
  fi

  # Cargo legitimately skips relinking when nothing changed, so a stale mtime is
  # informational rather than an error.
  for f in "${expected[@]}"; do
    [[ "$f" -nt "$RUN_START" ]] || warn "$branch: ${f#$out/} not relinked this run (mtime $(mtime_of "$f"))"
  done

  DETAIL[$branch]="zenohd $(mtime_of "$rel/zenohd") | $example $(mtime_of "$rel/examples/$example") | my_z_pub $(mtime_of "$rel/examples/my_z_pub")"
  return 0
}

RUN_START=$(mktemp) # reference file for the -nt freshness comparison

for b in "${BRANCHES[@]}"; do
  if build_branch "$b"; then
    STATUS[$b]=PASS
  else
    STATUS[$b]=FAIL
    warn "$b FAILED: ${DETAIL[$b]:-unknown} -- continuing with remaining branches"
  fi
done

rm -f "$RUN_START"

# ------------------------------------------------------------------ summary --

failed=0
printf '\n\033[1m%-34s %-8s %s\033[0m\n' "BRANCH" "STATUS" "OUTPUT / BINARIES"
printf '%.0s-' {1..110}; printf '\n'
for b in "${BRANCHES[@]}"; do
  st="${STATUS[$b]}"
  if [[ "$st" == PASS ]]; then
    colour='\033[1;32m'
  else
    colour='\033[1;31m'
    failed=1
  fi
  printf "%-34s ${colour}%-8s\033[0m target/%s\n" "$b" "$st" "$b"
  printf '%-34s %-8s   %s\n' "" "" "${DETAIL[$b]:-}"
done
printf '\n'

if [[ $failed -ne 0 ]]; then
  die "one or more branches failed to build"
fi
log "All ${#BRANCHES[@]} branch(es) built successfully."
