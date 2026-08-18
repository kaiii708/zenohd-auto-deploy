#!/usr/bin/env python3
"""
Experiment orchestration script.

Runs one or more settings sequentially. A "setting" is a self-contained
combination of ns-3 params, a network-config arm, and round/timing values,
declared in EXPERIMENT_CONFIG.json5's optional `settings` array. Without that
array the top-level ns3/rounds block is run as a single "default" setting
(back-compat). Each setting runs its `rounds` rounds; a failed round skips the
rest of that setting and moves to the next one.

Per-round steps:
  1. Cleanup previous state via launch_nodes.py -c
  2. Start launch_nodes.py -t {simTime+60} in the background (the timer is
     just a fallback — see step 5)
  3. Poll for launch_nodes.py's readiness marker (containers/TAP devices up)
  4. Run: sudo -n nice -n -10 taskset -c 0-10 ./ns3 run "nr-mec-3gpp-calibration ..."
  5. Wait for ns-3 to finish, then signal launch_nodes.py to clean up
  6. Pause round_pause seconds, then start the next round
"""
import argparse
import os
import signal
import subprocess
import sys
import time

import json5

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCH_SCRIPT = os.path.join(SCRIPT_DIR, 'launch_nodes.py')
NS3_DIR = os.path.expanduser('~/dev/ns-3-dev')
# Prefix for the privileged ns-3 invocation. "-n" makes sudo fail immediately
# rather than block on a password prompt -- which would otherwise hang the round
# after the containers and TAP devices are already up. Override with ZENOH_SUDO,
# same as launch_nodes.py.
SUDO = os.environ.get("ZENOH_SUDO", "sudo -n")
READY_MARKER = '/tmp/ns3_handover/nodes_ready'

# Kept at module level so the signal handler can reach them
_launch_proc = None
_ns3_proc = None


def _wait_for_ready(launch_proc, timeout):
    """Poll for the readiness marker launch_nodes.py touches once every
    router/client has finished tap/bridge/veth setup. Returns False if
    launch_proc exits early or the timeout is exceeded."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if launch_proc.poll() is not None:
            return False
        if os.path.exists(READY_MARKER):
            return True
        time.sleep(0.5)
    return False


def _signal_handler(sig, frame):
    print(f"\nReceived signal {sig}, cleaning up...")
    if _ns3_proc and _ns3_proc.poll() is None:
        _ns3_proc.terminate()
    if _launch_proc and _launch_proc.poll() is None:
        _launch_proc.terminate()
        _launch_proc.wait()
    sys.exit(0)


def _build_ns3_args(ns3_config: dict) -> str:
    """Convert config dict to a flat argument string for the ns3 run command."""
    parts = []
    for key, value in ns3_config.items():
        if isinstance(value, bool):
            parts.append(f'--{key}={str(value).lower()}')
        else:
            parts.append(f'--{key}={value}')
    return ' '.join(parts)


def run_round(setting: dict, round_num: int, global_config: dict) -> bool:
    """Run a single round of one setting. `setting` carries the resolved ns3
    params, networkConfig, and launch_wait; `global_config` carries the
    checkout locations (ns3_dir, zenoh_dir) shared by every setting."""
    global _launch_proc, _ns3_proc

    network_config = setting.get('networkConfig')
    # Forwarded to launch_nodes.py so both the cleanup and the launch act on the
    # same arm. Omitted entirely when unset, letting launch_nodes.py apply its
    # own default rather than duplicating that default here.
    net_args = ['-n', network_config] if network_config else []

    ns3_config = setting['ns3']
    # expanduser matters here: NS3_DIR is already expanded, but a value coming
    # from the config file is not, so a literal "~/dev/ns-3-dev" would be passed
    # to subprocess as cwd and fail with ENOENT.
    ns3_dir = os.path.expanduser(global_config.get('ns3_dir', NS3_DIR))

    # Forwarded to launch_nodes.py, which mounts the zenoh build output. When
    # "zenoh_dir" is absent we leave the environment alone, so launch_nodes.py
    # resolves the volume against the repo (the in-repo submodule) exactly as
    # before. When present it overrides the checkout location (see resolve_path
    # in launch_nodes.py); "~" is expanded there.
    launch_env = os.environ.copy()
    zenoh_dir = global_config.get('zenoh_dir')
    if zenoh_dir:
        launch_env['ZENOH_DIR'] = zenoh_dir

    # Forwarded to launch_nodes.py, which uses it as the experiment name only
    # when the network config's "experiment" field is empty -- so a single
    # network config reused across settings mirrors each setting's ns-3 output
    # dir in its zenoh experiment_data/ path.
    experiment_dir = ns3_config.get('experimentDir')
    if experiment_dir:
        launch_env['ZENOH_EXPERIMENT_DIR'] = experiment_dir

    launch_wait = setting.get('launch_wait', 12)
    sim_time = ns3_config['simTime']
    auto_terminate = int(sim_time) + 60

    print(f"\n{'='*50}")
    print(f"Round {round_num}: cleanup")
    print(f"{'='*50}")
    subprocess.run([sys.executable, LAUNCH_SCRIPT, '-c'] + net_args, cwd=SCRIPT_DIR, env=launch_env, check=True)

    print(f"\nRound {round_num}: launching nodes (auto-terminate in {auto_terminate}s)...")
    _launch_proc = subprocess.Popen(
        [sys.executable, LAUNCH_SCRIPT, '-t', str(auto_terminate)] + net_args,
        cwd=SCRIPT_DIR,
        env=launch_env,
    )

    print(f"Round {round_num}: waiting for containers/TAP devices to be ready (max {launch_wait}s)...")
    if not _wait_for_ready(_launch_proc, launch_wait):
        if _launch_proc.poll() is not None:
            print(f"Round {round_num}: ERROR — launch_nodes.py exited early (code {_launch_proc.returncode})")
        else:
            print(f"Round {round_num}: ERROR — timed out waiting for readiness marker after {launch_wait}s")
        return False

    ns3_args = _build_ns3_args(ns3_config)
    ns3_cmd = f'{SUDO} nice -n -10 taskset -c 0-10 ./ns3 run "nr-mec-3gpp-calibration {ns3_args}"'
    print(f"\nRound {round_num}: starting ns-3")
    print(f"  {ns3_cmd}\n")
    _ns3_proc = subprocess.Popen(ns3_cmd, shell=True, cwd=ns3_dir)

    ns3_exit = _ns3_proc.wait()
    if ns3_exit != 0:
        print(f"Round {round_num}: WARNING — ns-3 exited with code {ns3_exit}")

    print(f"Round {round_num}: ns-3 finished, signaling launch_nodes.py to clean up...")
    if _launch_proc.poll() is None:
        _launch_proc.terminate()
    _launch_proc.wait()

    print(f"Round {round_num}: done.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Run N rounds of Zenoh + ns-3 experiment'
    )
    parser.add_argument(
        '-c', '--config',
        default=os.path.join(SCRIPT_DIR, 'EXPERIMENT_CONFIG.json5'),
        help='Path to experiment config file (default: EXPERIMENT_CONFIG.json5)',
    )
    parser.add_argument(
        '-n', '--network-config', metavar='PATH', default=None,
        help='Network config selecting the experiment arm, e.g. '
             'configs/pre-subscribe.json5 (default: launch_nodes.py picks '
             'NETWORK_CONFIG.json5)',
    )
    parser.add_argument(
        '-e', '--ns3-experiment-dir', metavar='DIR', default=None,
        help='Override ns3.experimentDir — where ns-3 writes its output, '
             'relative to ns3_dir. Distinct from the network config\'s '
             '"experiment" field, which controls the Zenoh experiment_data/ '
             'path (default: from the config file)',
    )
    parser.add_argument(
        '--rounds', '--round', type=int, default=None,
        help='Override number of rounds from config (applied to every setting)',
    )
    args = parser.parse_args()

    with open(args.config) as f:
        config = json5.load(f)

    settings = _build_settings(config, args)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Cache sudo credentials upfront so background subprocesses in launch_nodes.py
    # (ip tuntap, ip link, iptables, ...) don't block waiting for a password prompt
    # print("Caching sudo credentials...")
    # subprocess.run(['sudo', '-v'], check=True)

    total_rounds = sum(s['rounds'] for s in settings)
    print(f"Starting experiment: {len(settings)} setting(s), {total_rounds} round(s) total")

    # (name, ok, rounds_completed, rounds_planned) per setting, for the summary
    results = []
    for si, setting in enumerate(settings, start=1):
        rounds = setting['rounds']
        round_pause = setting['round_pause']
        ns3 = setting['ns3']
        print(f"\n{'#'*60}")
        print(f"Setting {si}/{len(settings)}: {setting['name']}")
        print(f"  rounds={rounds}, net={setting.get('networkConfig') or '(default)'}")
        print(f"  simTime={ns3['simTime']}s, launch_wait={setting['launch_wait']}s")
        print(f"  ns-3 output: {ns3.get('experimentDir', '(unset)')}")
        print(f"{'#'*60}")

        completed = 0
        setting_ok = True
        for i in range(1, rounds + 1):
            if not run_round(setting, i, config):
                print(f"Setting '{setting['name']}' round {i} failed "
                      f"— skipping remaining rounds, moving to next setting.")
                setting_ok = False
                break
            completed += 1
            if i < rounds:
                print(f"\nWaiting {round_pause}s before round {i + 1}...")
                time.sleep(round_pause)

        results.append((setting['name'], setting_ok, completed, rounds))

        if si < len(settings):
            pause = settings[si]['round_pause']
            print(f"\nWaiting {pause}s before setting '{settings[si]['name']}'...")
            time.sleep(pause)

    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    for name, ok, completed, planned in results:
        status = "ok" if ok else "FAILED"
        print(f"  {name:<20} {status:<8} {completed}/{planned} rounds")

    if any(not ok for _, ok, _, _ in results):
        print("\nOne or more settings failed.")
        sys.exit(1)
    print(f"\nAll {len(settings)} setting(s) complete.")


def _build_settings(config: dict, args) -> list:
    """Return a list of normalized, self-contained setting dicts to run in order.

    Each returned setting carries: name, ns3, networkConfig, rounds, launch_wait,
    round_pause. When the config has no `settings` array we synthesize a single
    setting from the top-level fields (back-compat with the original format).
    Per-setting rounds/launch_wait/round_pause fall back to the top-level values,
    then to built-in defaults. networkConfig falls back to the -n CLI flag."""
    top_rounds = config.get('rounds', 1)
    top_launch_wait = config.get('launch_wait', 12)
    top_round_pause = config.get('round_pause', 5)

    raw_settings = config.get('settings')
    if raw_settings:
        settings = []
        for i, entry in enumerate(raw_settings, start=1):
            settings.append({
                'name': entry.get('name', f'setting-{i}'),
                'ns3': entry['ns3'],
                'networkConfig': entry.get('networkConfig', args.network_config),
                'rounds': entry.get('rounds', top_rounds),
                'launch_wait': entry.get('launch_wait', top_launch_wait),
                'round_pause': entry.get('round_pause', top_round_pause),
            })
    else:
        # Back-compat: one setting from the top-level ns3/rounds block.
        settings = [{
            'name': 'default',
            'ns3': config['ns3'],
            'networkConfig': args.network_config,
            'rounds': top_rounds,
            'launch_wait': top_launch_wait,
            'round_pause': top_round_pause,
        }]

    # --rounds overrides every setting's round count.
    if args.rounds is not None:
        for s in settings:
            s['rounds'] = args.rounds

    # --ns3-experiment-dir would collide across multiple settings (all writing
    # the same dir), so it only applies when there is exactly one setting.
    if args.ns3_experiment_dir is not None:
        if len(settings) == 1:
            settings[0]['ns3']['experimentDir'] = args.ns3_experiment_dir
        else:
            print(f"WARNING — ignoring --ns3-experiment-dir with {len(settings)} "
                  f"settings; experimentDir stays per-setting.")

    return settings


if __name__ == '__main__':
    main()
