# Zenoh Pre-Subscription Experiments

Build and run the pre-subscription evaluation: three experiments, each with a pre-subscription arm
and a baseline arm, over an ns-3 5G handover scenario with Zenoh routers in network namespaces.

Zenoh itself is vendored as a **git submodule**, so a clone of this repo pins the exact zenoh
commits the results came from.

```
zenohd-auto-deploy/
├── zenoh/                     git submodule -> kaiii708/zenoh (the six experiment branches)
├── build_experiments.sh       cross-builds each branch into zenoh/target/<branch>/
├── check_configs.sh           verifies configs match built binaries (run before experiments)
├── run_experiment.py          orchestrates N rounds of ns-3 + Zenoh
├── launch_nodes.py            brings up routers/clients in netns with TAP devices
├── configs/<branch>.json5     one network config per experiment arm
├── EXPERIMENT_CONFIG.json5    rounds, timings, ns-3 parameters, ns3_dir
└── experiment_data/           results, per experiment label
```

---

## The six branches

Each experiment is a pair: one pre-subscription arm, one baseline. The **config filename matches the
branch name exactly**, so the build command and the run command always name the same thing.

| Experiment | Arm | Branch / config name | Subscriber |
|---|---|---|---|
| Fixed-Window Effective Packet Loss Ratio | pre-sub | `pre-subscribe` | `z_pre_sub` |
| | baseline | `ablation-baseline` | `z_sub` |
| Zenoh Network Convergence Time | pre-sub | `presub-convergence-measurement` | `z_pre_sub` |
| | baseline | `baseline-convergence-measurement` | `z_sub` |
| Control-Plane Signaling Overhead & Scalability | pre-sub | `presub-overhead-evaluation` | `z_pre_sub` |
| | baseline | `baseline-overhead-evaluation` | `z_sub` |

The publisher (`my_z_pub`) is the same in every arm.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker | Runs both the `cross` build containers and the router containers |
| [`cross`](https://github.com/cross-rs/cross) | `cargo install cross` — cross-compiles to `x86_64-unknown-linux-musl` |
| ns-3 with `nr-mec-3gpp-calibration` | ~1.5 GB, installed separately; path set via `ns3_dir` |
| Python ≥ 3.8 + `json5` | `pip install json5` |
| `tmux`, `rsync`, `sudo` | Session management, data transfer, netns/TAP setup |

ns-3 is deliberately **not** a submodule — it is large and independently maintained. Point
`EXPERIMENT_CONFIG.json5`'s `ns3_dir` at your checkout (`~` is expanded).

---

## First-time setup

```bash
git clone --recurse-submodules git@github.com:kaiii708/zenohd-auto-deploy.git
cd zenohd-auto-deploy
```

Already cloned without submodules:

```bash
git submodule update --init
```

No further configuration is needed — `build_experiments.sh` finds the submodule relative to its own
location, and the configs use repo-relative paths, so any clone path works on any machine.

---

## 1. Build

```bash
./build_experiments.sh                                  # all six branches
./build_experiments.sh pre-subscribe                    # one arm
./build_experiments.sh presub-overhead-evaluation baseline-overhead-evaluation
./build_experiments.sh --help
```

Each branch produces three binaries:

```
zenoh/target/<branch>/x86_64-unknown-linux-musl/release/
├── zenohd
└── examples/
    ├── z_pre_sub   (or z_sub on baseline branches)
    └── my_z_pub
```

The script checks out each branch inside `zenoh/`, builds, and **restores the submodule's original
ref at the end — including on Ctrl-C**. It prints a PASS/FAIL summary and exits non-zero if any
branch failed; a failing branch does not abort the others. Build logs go to `logs/build-<branch>-<timestamp>.log`.

**The submodule's tracked tree must be clean.** The script refuses to start otherwise, since
`git checkout` would carry your modifications onto the next branch. Untracked files are fine.

Branches are created automatically from `origin/<branch>` on first use, so a fresh clone works
without any manual `git checkout`.

### Expected timings

Measured on a 20-thread i7-12700H, 15 GB RAM:

| | |
|---|---|
| One branch, cold | ~5–6 min |
| All six, cold | ~28–34 min |
| Re-run, nothing changed | ~5 min per branch (see note) |

Builds run **sequentially**. Cargo already saturates all cores within a single build, and concurrent
musl release builds are memory-hungry, so parallelism gains little here.

> **Note on rebuilds:** because branches are checked out in one working tree, `git checkout` rewrites
> the mtimes of files differing between branches, invalidating Cargo's fingerprints. Returning to an
> already-built branch therefore costs close to a full rebuild (measured: 5m13s versus 6m00s cold).
> This is inherent to the checkout approach; a git-worktree-per-branch variant reduces it to ~4s, at
> the cost of locking branches out of the main checkout.

---

## 2. Verify before running

```bash
./check_configs.sh
```

Confirms, for every config: the executable matches the arm its branch is supposed to run, the volume
points at that branch's build directory, and all three binaries exist.

This guards a specific silent failure. `volume` and `excutable` are independent fields, so a config
can name the pre-subscription build directory while launching the baseline subscriber. Nothing at
runtime notices — the experiment completes and produces plausible-looking numbers for the wrong arm.
**Run this after building and before trusting any results.**

---

## 3. Run

```bash
./run_experiment.py -n configs/pre-subscribe.json5
./run_experiment.py -n configs/ablation-baseline.json5 --rounds 1
```

| Flag | Meaning |
|---|---|
| `-n, --network-config` | Which arm to run (`configs/<branch>.json5`) |
| `-c, --config` | Experiment config (default `EXPERIMENT_CONFIG.json5`) |
| `--rounds N` | Override the round count from the config |

Each round: clean up previous state → launch nodes → wait for the readiness marker → run ns-3 → tear
down on ns-3 exit → pause → repeat.

`launch_nodes.py` can also be driven directly, with the same `-n` flag:

```bash
./launch_nodes.py -n configs/pre-subscribe.json5      # launch
./launch_nodes.py -n configs/pre-subscribe.json5 -c   # clean up only
```

---

## Output

Results land in `experiment_data/<experiment>/<timestamp>/`, where `<experiment>` is the config's
`experiment` field:

| Config | `experiment` label |
|---|---|
| `pre-subscribe` / `ablation-baseline` | `packet_loss/presub`, `packet_loss/baseline` |
| `presub-convergence-measurement` / `baseline-…` | `convergence/presub`, `convergence/baseline` |
| `presub-overhead-evaluation` / `baseline-…` | `overhead/presub`, `overhead/baseline` |

Per node: `zenohd_<id>.log` (stdout) and `zenohd_<id>_err.log` (stderr).

---

## Design notes

**Why per-branch target directories.** The three baseline branches each commit a `.cargo/config.toml`
containing `target-dir = "target/baseline"`. Built naively they would overwrite one another, and an
ablation study whose two arms silently share a build directory is worthless. `build_experiments.sh`
sets `CARGO_TARGET_DIR` per branch, which takes precedence over that committed setting; `cross`
propagates it through the container boundary.

**Why the submodule pin drifts.** Building checks out branches inside `zenoh/`, so after most runs
`git status` shows the submodule moved off its recorded commit. This is expected — do not commit the
churn. Run `git add zenoh` only to deliberately record "these are the commits the results came from."
`.gitmodules` sets `ignore = dirty` so build artifacts inside `zenoh/` stay quiet while commit moves
remain visible.

**Why paths are repo-relative.** `launch_nodes.py` resolves config paths against its own directory
(`resolve_path`), not the current working directory, so `zenoh/target/...` means the same thing
whether the script is run directly, from another directory, or spawned by `run_experiment.py`.

**`ZENOH_DIR`** overrides which zenoh checkout gets built. It is a debugging escape hatch: the
configs point at the submodule, so building elsewhere leaves them referencing stale or missing
binaries. Leave it unset for real runs.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `working tree has modified tracked files` | The submodule has uncommitted changes; commit or stash inside `zenoh/` |
| `branch not found locally or on origin` | `git -C zenoh fetch origin` |
| `cross not found in PATH` | `cargo install cross` |
| `docker daemon not reachable` | Start Docker; confirm with `docker info` |
| `check_configs.sh` reports `BUILT: NO` | That arm is not built: `./build_experiments.sh <branch>` |
| `check_configs.sh` reports `WRONG` | A config's `excutable` disagrees with its branch — fix the config |
| Experiment runs but numbers look like the other arm | Ran with the wrong `-n`; `check_configs.sh` plus matching names prevent this |

---

## Reference

### TAP device naming

`launch_nodes.py` creates TAP devices for the simulator to attach to:

| Node type | TAP name | Example |
|---|---|---|
| Router | `tap_edge{id}` | `tap_edge1`, `tap_edge2` |
| Client | `tap_{executable}` | `tap_z_sub`, `tap_z_pre_sub` |

### Network config fields

Full field documentation is in the comments of `NETWORK_CONFIG_DEFAULT.json5`. The fields that vary
between arms are only these three:

| Field | Purpose |
|---|---|
| `experiment` | Output directory label under `experiment_data/` |
| `volume` | Build directory mounted into the containers at `/zenoh` |
| `clients."1".excutable` | Subscriber binary: `z_pre_sub` or `z_sub` |

Everything else — the seven routers, their ZIDs, endpoints, and connection topology — is identical
across all six configs.

### Other scripts

`launch_routers.py` and `launch_examples.sh` are earlier entry points for router-only deployments
without ns-3. They are not used by `run_experiment.py` and are kept for reference.
