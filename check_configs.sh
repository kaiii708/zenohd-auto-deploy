#!/usr/bin/env bash
#
# Verify every per-arm config in configs/ is internally consistent and backed by
# real binaries.
#
# The failure this guards against: `volume` and `excutable` are independent
# fields, so a config can name the pre-subscription build directory while
# launching the baseline subscriber (or vice versa). Nothing at runtime notices
# -- the experiment runs, produces plausible numbers, and silently measures the
# wrong arm. Run this after building and before trusting any results.

set -uo pipefail

SCRIPT_DIR=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
cd "$SCRIPT_DIR" || exit 1

# Which subscriber example each branch is supposed to use. Must agree with
# build_experiments.sh.
declare -A EXPECTED=(
  [pre-subscribe]=z_pre_sub
  [presub-convergence-measurement]=z_pre_sub
  [presub-overhead-evaluation]=z_pre_sub
  [ablation-baseline]=z_sub
  [baseline-convergence-measurement]=z_sub
  [baseline-overhead-evaluation]=z_sub
)

fail=0
printf '%-36s %-10s %-8s %-8s %s\n' "CONFIG" "EXECUTABLE" "ARM-OK" "BUILT" "VOLUME"
printf '%.0s-' {1..108}; printf '\n'

for cfg in configs/*.json5; do
  name=$(basename "$cfg" .json5)

  volume=$(grep -oP '(?<=volume: ")[^"]+' "$cfg" | head -1)
  exe=$(grep -oP '(?<=excutable: ")[^"]+' "$cfg" | head -1)

  # Arm check: does the executable match what this branch is supposed to run?
  want="${EXPECTED[$name]:-}"
  if [[ -z "$want" ]]; then
    arm="UNKNOWN"; fail=1
  elif [[ "$exe" == "$want" ]]; then
    arm="ok"
  else
    arm="WRONG"; fail=1
  fi

  # Path check: the volume should point at this branch's build directory.
  [[ "$volume" == *"/target/$name/"* ]] || { arm="MISMATCH"; fail=1; }

  # Build check: are the binaries actually there?
  missing=()
  for bin in "$volume/zenohd" "$volume/examples/$exe" "$volume/examples/my_z_pub"; do
    [[ -f "$bin" ]] || missing+=("$(basename "$bin")")
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    built="yes"
  else
    built="NO"; fail=1
  fi

  printf '%-36s %-10s %-8s %-8s %s\n' "$name" "$exe" "$arm" "$built" "$volume"
  [[ ${#missing[@]} -gt 0 ]] && printf '%-36s   missing: %s\n' "" "${missing[*]}"
done

printf '\n'
if [[ $fail -ne 0 ]]; then
  printf '\033[1;31mFAIL\033[0m -- fix the above before running experiments.\n'
  printf 'Missing binaries are usually just an unbuilt arm: ./build_experiments.sh <branch>\n'
  exit 1
fi
printf '\033[1;32mAll configs consistent and backed by built binaries.\033[0m\n'
