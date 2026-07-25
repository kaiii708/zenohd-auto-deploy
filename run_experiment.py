#!/usr/bin/env python3
"""
Experiment orchestration script.

Per-round steps:
  1. Cleanup previous state via launch_nodes.py -c
  2. Start launch_nodes.py -t {simTime+60} in the background (the timer is
     just a fallback — see step 5)
  3. Poll for launch_nodes.py's readiness marker (containers/TAP devices up)
  4. Run: sudo nice -n 20 taskset -c 0-10 ./ns3 run "nr-mec-3gpp-calibration ..."
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


def _kill_ns3(grace=5):
    """Send SIGINT, then SIGKILL after a grace period, to ns-3's whole
    process group (sudo -> nice -> taskset -> the ns3 wrapper script ->
    the compiled binary). The compiled binary is a real subprocess.run()
    child forked by the ns3 wrapper's run_step() (see ns-3-dev/ns3), which
    installs no signal handler of its own, so only a group-wide signal
    (sent as root, since the binary runs fully as root) reaches it.

    Deliberately does NOT call _ns3_proc.wait()/.poll() to confirm death:
    this runs inside the SIGINT/SIGTERM handler, itself invoked as a
    nested frame from run_round()'s blocking _ns3_proc.wait(). That outer
    call holds Popen's internal (non-reentrant) _waitpid_lock for its
    entire blocking duration, so any wait()/poll() from here would try to
    re-acquire a lock this same thread already holds -- the timed variant
    just burns its whole timeout unable to progress, and the untimed
    fallback deadlocks permanently. Reaping is left to the OS: sys.exit(0)
    right after this releases the lock as it unwinds, and init reaps
    whatever's left."""
    if _ns3_proc is None:
        return

    try:
        pgid = os.getpgid(_ns3_proc.pid)
    except ProcessLookupError:
        return

    def _signal_group(sig):
        result = subprocess.run(
            ["sudo", "-n", "kill", sig, "--", f"-{pgid}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and result.stderr.strip():
            print(f"  (sudo kill {sig} -{pgid} failed: {result.stderr.strip()})")

    _signal_group("-INT")
    time.sleep(grace)
    _signal_group("-KILL")


def _signal_handler(sig, frame):
    print(f"\nReceived signal {sig}, cleaning up...")
    _kill_ns3()
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


def run_round(round_num: int, config: dict) -> bool:
    global _launch_proc, _ns3_proc

    ns3_config = config['ns3']
    ns3_dir = config.get('ns3_dir', NS3_DIR)
    launch_wait = config.get('launch_wait', 12)
    sim_time = ns3_config['simTime']
    auto_terminate = int(sim_time) + 60

    print(f"\n{'='*50}")
    print(f"Round {round_num}: cleanup")
    print(f"{'='*50}")
    subprocess.run([sys.executable, LAUNCH_SCRIPT, '-c'], cwd=SCRIPT_DIR, check=True)

    print(f"\nRound {round_num}: launching nodes (auto-terminate in {auto_terminate}s)...")
    _launch_proc = subprocess.Popen(
        [sys.executable, LAUNCH_SCRIPT, '-t', str(auto_terminate)],
        cwd=SCRIPT_DIR,
    )

    print(f"Round {round_num}: waiting for containers/TAP devices to be ready (max {launch_wait}s)...")
    if not _wait_for_ready(_launch_proc, launch_wait):
        if _launch_proc.poll() is not None:
            print(f"Round {round_num}: ERROR — launch_nodes.py exited early (code {_launch_proc.returncode})")
        else:
            print(f"Round {round_num}: ERROR — timed out waiting for readiness marker after {launch_wait}s")
        return False

    ns3_args = _build_ns3_args(ns3_config)
    ns3_cmd = f'sudo nice -n 20 taskset -c 0-10 ./ns3 run "nr-mec-3gpp-calibration {ns3_args}"'
    print(f"\nRound {round_num}: starting ns-3")
    print(f"  {ns3_cmd}\n")
    _ns3_proc = subprocess.Popen(ns3_cmd, shell=True, cwd=ns3_dir, start_new_session=True)

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
        '--rounds', '--round', type=int, default=None,
        help='Override number of rounds from config',
    )
    args = parser.parse_args()

    with open(args.config) as f:
        config = json5.load(f)

    rounds = args.rounds if args.rounds is not None else config.get('rounds', 1)
    round_pause = config.get('round_pause', 5)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Cache sudo credentials upfront so background subprocesses in launch_nodes.py
    # (ip tuntap, ip link, iptables, ...) don't block waiting for a password prompt
    # print("Caching sudo credentials...")
    # subprocess.run(['sudo', '-v'], check=True)

    print(f"Starting experiment: {rounds} round(s)")
    print(f"  simTime={config['ns3']['simTime']}s, launch_wait={config.get('launch_wait', 12)}s")

    for i in range(1, rounds + 1):
        success = run_round(i, config)
        if not success:
            print(f"Round {i} failed — aborting.")
            sys.exit(1)
        if i < rounds:
            print(f"\nWaiting {round_pause}s before round {i + 1}...")
            time.sleep(round_pause)

    print(f"\nAll {rounds} round(s) complete.")


if __name__ == '__main__':
    main()
