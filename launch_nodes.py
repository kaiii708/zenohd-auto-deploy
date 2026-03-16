#!/usr/bin/env python3
import argparse
import signal
import os
import sys
import subprocess
import json5
import time


def cleanup():
    # Cleanup clients first
    if 'client_list' in globals() and client_list:
        for client in client_list:
            try:
                if not client.is_localhost:
                    client.transfer_data_back()
                client.kill_session()
                print(f"Successfully killed tmux session for Client {client.id}\n")
            except Exception as e:
                print(f"An error occurred while performing the cleanup for Client {client.id}: {e}\n")

            try:
                client.cleanup_netns_veth()
            except Exception as e:
                print(f"An error occurred while performing the cleanup for veth Client {client.id}: {e}\n")

    # Then cleanup routers
    if 'router_list' in globals() and router_list:
        # print("Cleaning up tmux sessions for all routers...\n")
        # os.chdir("experiment_data")
        for router in router_list:
            try:
                if not router.is_localhost:
                    router.transfer_data_back()
                router.kill_session()
                print(f"Successfully killed tmux session for Router {router.id}\n")
                router.check_if_error_while_launch()
            except Exception as e:
                # print(f"Failed to kill tmux session for Router {router.id}: {e}\n")
                print(f"An error occurred while performing the cleanup for Router {router.id}: {e}\n")

            try:
                router.cleanup_netns_veth()
            except Exception as e:
                print(f"An error occurred while performing the cleanup for veth {router.id}: {e}\n")

def signal_handler(sig, frame):
    print(f"\nReceived signal:{sig}, leaving...\n")
    cleanup()
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.killpg(process_group_id, signal.SIGTERM)
    sys.exit(0)


class Router():
    def __init__(self, id, config):
        self.id = id
        self.config = config

        self.launch_ip = config.get('ssh')
        self.docker = config.get('docker')
        self.mode = config.get('mode')
        if config.get('zid').get('set'):
            self.zid = config.get('zid').get('value')
        else:
            self.zid = False
        self.listen_endpoint = config.get('listen_endpoint')
        self.is_localhost = "localhost" in self.launch_ip
        self.listen_port = self.listen_endpoint.split("/")[-1].split(":")[-1]
        self.session_name = f"zenohd_{self.id}"
        self.volume = self.config.get('volume') or network_config.get('volume')
        self.ns3_handover_dir = network_config.get('ns3_handover_dir')
        self.connect_endpoint = self.config.get('connect_endpoint') or None
        if self.connect_endpoint:
            self.port_expose = self.config.get('port_expose')
        self.default_route = self.config.get('default_route')

        # Get cfg options (list of key:value strings for --cfg arguments)
        self.cfg_options = config.get('cfg', [])

        self.launch_zenohd()
        time.sleep(1)
        self.setup_netns_veth()
        time.sleep(1)

    def run_shell_command(self, command):
        print(f"Running command: {command}\n")
        subprocess.run(command, shell=True, check=True)

    def kill_session(self):
        kill_session_command = f"tmux kill-session -t {self.session_name}"
        if self.is_localhost:
            command = kill_session_command
        else:
            command = f"ssh {user_name}@{self.launch_ip} \"{kill_session_command}\""
        self.run_shell_command(command)

    def transfer_data_back(self):
        command = f"rsync -avP {user_name}@{self.launch_ip}:~/{base_dir} ./experiment_data"
        self.run_shell_command(command)

    def cleanup_netns_veth(self):
        name = f"edge{self.id}"
        print(f"Cleaning up for Router {self.id}...\n")
        self.run_shell_command(f"sudo iptables -D FORWARD -m physdev --physdev-is-bridged -i br_{name} -j ACCEPT 2>/dev/null || true")

        self.run_shell_command(f"sudo ip link del internal_{name} 2>/dev/null || true")
        self.run_shell_command(f"sudo ip link del br_{name} 2>/dev/null || true")
        self.run_shell_command(f"sudo ip link del tap_{name} 2>/dev/null || true")
        # self.run_shell_command(f"sudo ip link del internal_{name} 2>/dev/null || true")
        # self.run_shell_command(f"sudo ip link del external_{name} 2>/dev/null || true")

    # def cleanup(self):

    #     for idx in range(len(self.listen_endpoints)):
    #         self.cleanup_netns_veth(idx)
    #     self.run_shell_command("sudo rm -f /var/run/netns/* 2>/dev/null || true")
    #     # self.run_shell_command(f"docker container rm -f {self.session_name} 2>/dev/null || true")
    #     self.run_shell_command(f"tmux kill-session -t {self.session_name} 2>/dev/null || true")


    def launch_zenohd(self):
        print(f"Launching zenohd for Router {self.id}...\n")

        kill_session_command = f"tmux kill-session -t {self.session_name} 2>/dev/null || true && "
        chdir_command = f"mkdir -p {base_dir} && cd {base_dir} && "
        if self.docker:
            volume_arg = ""
            if self.volume:
                host_path = os.path.abspath(self.volume)
                volume_arg = f"-v {host_path}:/zenoh"
            if self.ns3_handover_dir:
                handover_path = os.path.abspath(self.ns3_handover_dir)
                volume_arg += f" -v {handover_path}:/mnt"

            docker_run_cmd = f"docker run --cpuset-cpus='11-19' --init --name {self.session_name} --network none --rm {volume_arg}"
            # docker_run_cmd = f"docker run --init --name {self.session_name} --network none --rm {volume_arg}"
            if self.connect_endpoint:
                docker_run_cmd += f" -p {self.port_expose}:7447/tcp"
            docker_run_cmd += f" {image}"
            if image_clean:
                clean_image = f"docker rmi {image} 2>/dev/null || true && "
                zenohd_launch = clean_image + chdir_command + docker_run_cmd
            else:
                zenohd_launch = chdir_command + docker_run_cmd
        else:
            zenohd_launch = chdir_command + "zenohd"
        
        if self.is_localhost:
            base_command = kill_session_command +f"tmux new-session -d -s {self.session_name} && tmux send-keys -t {self.session_name} '{zenohd_launch}"
        else:
            base_command = f"ssh {user_name}@{self.launch_ip} \"{kill_session_command}tmux new-session -d -s {self.session_name} && tmux send-keys -t {self.session_name} '{zenohd_launch}"

        # Add mode-specific options
        if self.zid:
            base_command += f" -i {self.zid}"
        if self.mode == "l":
            base_command += f" -l tcp/0.0.0.0:7447"
        elif self.mode == "e":
            connect_points = self.config['connect']
            for remote_id in connect_points:
                base_command += f" -e {routers.get(str(remote_id)).get('listen_endpoint')}"

        # Add cfg options (e.g., connect/exit_on_failure:false, connect/timeout_ms:-1)
        for cfg_option in self.cfg_options:
            base_command += f" --cfg '{cfg_option}'"

        base_command += f" > >(tee ./zenohd_{self.id}.log) 2> >(tee ./zenohd_{self.id}_err.log >&2)"
        base_command += "; echo \$? > /tmp/exit_code' C-m"
        if not self.is_localhost:
            base_command += "\""

        self.run_shell_command(base_command)

    def setup_netns_veth(self):
        addr = self.listen_endpoint.split('/')[1].split(':')[0]
        name = f"edge{self.id}"
        self.run_shell_command(f"sudo ip tuntap add tap_{name} mode tap")
        self.run_shell_command(f"sudo ip link set tap_{name} promisc on up")

        self.run_shell_command(f"sudo ip link add name br_{name} type bridge")
        self.run_shell_command(f"sudo ip link set br_{name} up")
        self.run_shell_command(f"sudo ip link set tap_{name} master br_{name}")

        self.run_shell_command(f"sudo iptables -I FORWARD -m physdev --physdev-is-bridged -i br_{name}  -j ACCEPT")

        pid = subprocess.check_output(f"docker inspect --format '{{{{ .State.Pid }}}}' {self.session_name}", shell=True).decode().strip()

        self.run_shell_command("sudo mkdir -p /var/run/netns")
        self.run_shell_command(f"sudo ln -sf /proc/{pid}/ns/net  /var/run/netns/{pid}")

        self.run_shell_command(f"sudo ip link add internal_{name}  type veth peer name external_{name}")
        self.run_shell_command(f"sudo ip link set internal_{name}  master br_{name}")
        self.run_shell_command(f"sudo ip link set internal_{name}  up")
        self.run_shell_command(f"sudo ip link set external_{name}  netns {pid}")

        self.run_shell_command(f"sudo ip netns exec {pid}  ip link set dev external_{name} name eth0")
        self.run_shell_command(f"sudo ip netns exec {pid}  ip link set eth0 up")
        self.run_shell_command(f"sudo ip netns exec {pid}  ip addr add {addr}/24 dev eth0")

        if self.default_route:
            self.run_shell_command(f"sudo ip netns exec {pid} ip route add default via {self.default_route}")

    def check_if_error_while_launch(self):
        if self.is_localhost:
            command = "cat /tmp/exit_code"
            clean = "rm /tmp/exit_code"
        else:
            command = f"ssh {user_name}@{self.launch_ip} \"cat /tmp/exit_code\""
            clean = f"ssh {user_name}@{self.launch_ip} \"rm /tmp/exit_code\""
        try:
            result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            exit_code = result.stdout.strip()
            if exit_code != b'0' and exit_code != b'130':
                print(f"Error: An error occurred while launching Router {self.id} on {self.launch_ip}. Exit code: {exit_code}")
                subprocess.run(clean, shell=True)
                sys.exit(0)
            else:
                print(f"Router {self.id} launched successfully on {self.launch_ip}.")
                subprocess.run(clean, shell=True)
        except subprocess.CalledProcessError as _:
            pass

class Client():
    def __init__(self, id, config):
        self.id = id
        self.config = config

        self.launch_ip = config.get('ssh')
        self.executable = config.get('excutable')  # Note: typo in config
        self.tap_br_name = config.get('tap_br_name')  # Use tap_br_name from config for bridge naming
        self.is_localhost = "localhost" in self.launch_ip
        self.session_name = f"client_{self.executable}_{self.id}"
        self.listen_ip = config.get('listen_ip')
        self.volume = network_config.get('volume')
        self.ns3_handover_dir = network_config.get('ns3_handover_dir')
        self.no_multicast_scouting = not config.get('enabled_multicast_scounting', False)

        # Get zid if set
        if config.get('zid', {}).get('set'):
            self.zid = config.get('zid').get('value')
        else:
            self.zid = None

        # Get connect router endpoints
        self.connect_routers = config.get('connect_router', [])
        self.default_route = config.get('default_route')

        # Get cfg options (list of key:value strings for --cfg arguments)
        self.cfg_options = config.get('cfg', [])

        self.launch_client()
        time.sleep(1)
        self.setup_netns_veth()

    def run_shell_command(self, command):
        print(f"Running command: {command}\n")
        subprocess.run(command, shell=True, check=True)

    def kill_session(self):
        kill_session_command = f"tmux kill-session -t {self.session_name}"
        if self.is_localhost:
            command = kill_session_command
        else:
            command = f"ssh {user_name}@{self.launch_ip} \"{kill_session_command}\""
        self.run_shell_command(command)

    def transfer_data_back(self):
        command = f"rsync -avP {user_name}@{self.launch_ip}:~/{base_dir} ./experiment_data"
        self.run_shell_command(command)

    def cleanup_netns_veth(self):
        name = self.tap_br_name
        veth_name = f"c{self.id}"
        print(f"Cleaning up for Client {self.id} ({self.executable})...\n")
        self.run_shell_command(f"sudo iptables -D FORWARD -m physdev --physdev-is-bridged -i br_{name} -j ACCEPT 2>/dev/null || true")

        self.run_shell_command(f"sudo ip link del int_{veth_name} 2>/dev/null || true")
        self.run_shell_command(f"sudo ip link del br_{name} 2>/dev/null || true")
        self.run_shell_command(f"sudo ip link del tap_{name} 2>/dev/null || true")

    def launch_client(self):
        print(f"Launching client {self.executable} for Client {self.id}...\n")

        kill_session_command = f"tmux kill-session -t {self.session_name} 2>/dev/null || true && "
        chdir_command = f"mkdir -p {base_dir} && cd {base_dir} && "

        # Build volume argument
        volume_arg = ""
        if self.volume:
            host_path = os.path.abspath(self.volume)
            volume_arg = f"-v {host_path}:/zenoh"
        if self.ns3_handover_dir:
            handover_path = os.path.abspath(self.ns3_handover_dir)
            volume_arg += f" -v {handover_path}:/mnt"

        # Build the docker command - using --network none like Router
        docker_run_cmd = f"docker run --cpuset-cpus='11-19' --init --name {self.session_name} --network none --rm --entrypoint /bin/sh {volume_arg} {image}"
        # docker_run_cmd = f"docker run --init --name {self.session_name} --network none --rm --entrypoint /bin/sh {volume_arg} {image}"

        # Build the client executable command
        client_cmd = f"sleep 10 && /zenoh/examples/{self.executable}"

        # Add zid if set
        if self.zid:
            client_cmd += f" -i {self.zid}"

        # Add connect endpoints for each router
        for router_id in self.connect_routers:
            router_endpoint = routers.get(str(router_id), {}).get('listen_endpoint')
            if router_endpoint:
                client_cmd += f" -e {router_endpoint}"

        # Add client mode options
        client_cmd += " --mode client"
        if self.no_multicast_scouting:
            client_cmd += " --no-multicast-scouting"

        # Add cfg options (e.g., connect/exit_on_failure:false, connect/timeout_ms:-1)
        for cfg_option in self.cfg_options:
            client_cmd += f" --cfg '{cfg_option}'"

        # Full docker command with shell execution, redirection outside docker (in tmux bash)
        # Use double quotes for -c argument to avoid breaking outer single quotes in tmux send-keys
        full_docker_cmd = f'{docker_run_cmd} -c \"{client_cmd}\" > >(tee ./{self.executable}_{self.id}.log) 2> >(tee ./{self.executable}_{self.id}_err.log >&2)'

        if image_clean:
            clean_image = f"docker rmi {image} 2>/dev/null || true && "
            client_launch = clean_image + chdir_command + full_docker_cmd
        else:
            client_launch = chdir_command + full_docker_cmd

        if self.is_localhost:
            base_command = kill_session_command + f"tmux new-session -d -s {self.session_name} && tmux send-keys -t {self.session_name} '{client_launch}' C-m"
        else:
            base_command = f"ssh {user_name}@{self.launch_ip} \"{kill_session_command}tmux new-session -d -s {self.session_name} && tmux send-keys -t {self.session_name} '{client_launch}' C-m\""

        self.run_shell_command(base_command)

    def setup_netns_veth(self):
        addr = self.listen_ip
        name = self.tap_br_name
        # Use shorter names for veth pairs (Linux limit is 15 chars)
        # Use client id for veth to keep it short: int_c1, ext_c1
        veth_name = f"c{self.id}"
        self.run_shell_command(f"sudo ip tuntap add tap_{name} mode tap")
        self.run_shell_command(f"sudo ip link set tap_{name} promisc on up")

        self.run_shell_command(f"sudo ip link add name br_{name} type bridge")
        self.run_shell_command(f"sudo ip link set br_{name} up")
        self.run_shell_command(f"sudo ip link set tap_{name} master br_{name}")

        self.run_shell_command(f"sudo iptables -I FORWARD -m physdev --physdev-is-bridged -i br_{name}  -j ACCEPT")

        pid = subprocess.check_output(f"docker inspect --format '{{{{ .State.Pid }}}}' {self.session_name}", shell=True).decode().strip()

        self.run_shell_command("sudo mkdir -p /var/run/netns")
        self.run_shell_command(f"sudo ln -sf /proc/{pid}/ns/net  /var/run/netns/{pid}")

        self.run_shell_command(f"sudo ip link add int_{veth_name} type veth peer name ext_{veth_name}")
        self.run_shell_command(f"sudo ip link set int_{veth_name} master br_{name}")
        self.run_shell_command(f"sudo ip link set int_{veth_name} up")
        self.run_shell_command(f"sudo ip link set ext_{veth_name} netns {pid}")

        self.run_shell_command(f"sudo ip netns exec {pid} ip link set dev ext_{veth_name} name eth0")
        self.run_shell_command(f"sudo ip netns exec {pid} ip link set eth0 up")
        self.run_shell_command(f"sudo ip netns exec {pid} ip addr add {addr}/24 dev eth0")

        if self.default_route:
            self.run_shell_command(f"sudo ip netns exec {pid} ip route add default via {self.default_route}")


def cleanup_only():
    """Run cleanup for all nodes defined in config without launching them."""
    print("Running cleanup only mode...\n")

    # Cleanup clients first
    for client_id, client_config in clients.items():
        try:
            # Create a minimal client object just for cleanup
            executable = client_config.get('excutable')
            name = client_config.get('tap_br_name')
            veth_name = f"c{client_id}"
            session_name = f"client_{executable}_{client_id}"
            launch_ip = client_config.get('ssh')
            is_localhost = "localhost" in launch_ip

            print(f"Cleaning up Client {client_id} ({executable})...\n")

            # Kill tmux session
            kill_session_command = f"tmux kill-session -t {session_name} 2>/dev/null || true"
            if is_localhost:
                subprocess.run(kill_session_command, shell=True)
            else:
                subprocess.run(f"ssh {user_name}@{launch_ip} \"{kill_session_command}\"", shell=True)

            # Cleanup network resources
            subprocess.run(f"sudo iptables -D FORWARD -m physdev --physdev-is-bridged -i br_{name} -j ACCEPT 2>/dev/null || true", shell=True)
            subprocess.run(f"sudo ip link del int_{veth_name} 2>/dev/null || true", shell=True)
            subprocess.run(f"sudo ip link del br_{name} 2>/dev/null || true", shell=True)
            subprocess.run(f"sudo ip link del tap_{name} 2>/dev/null || true", shell=True)

            print(f"Cleanup complete for Client {client_id}\n")
        except Exception as e:
            print(f"Error cleaning up Client {client_id}: {e}\n")

    # Then cleanup routers
    for router_id, router_config in routers.items():
        try:
            name = f"edge{router_id}"
            session_name = f"zenohd_{router_id}"
            launch_ip = router_config.get('ssh')
            is_localhost = "localhost" in launch_ip

            print(f"Cleaning up Router {router_id}...\n")

            # Kill tmux session
            kill_session_command = f"tmux kill-session -t {session_name} 2>/dev/null || true"
            if is_localhost:
                subprocess.run(kill_session_command, shell=True)
            else:
                subprocess.run(f"ssh {user_name}@{launch_ip} \"{kill_session_command}\"", shell=True)

            # Cleanup network resources
            subprocess.run(f"sudo iptables -D FORWARD -m physdev --physdev-is-bridged -i br_{name} -j ACCEPT 2>/dev/null || true", shell=True)
            subprocess.run(f"sudo ip link del internal_{name} 2>/dev/null || true", shell=True)
            subprocess.run(f"sudo ip link del br_{name} 2>/dev/null || true", shell=True)
            subprocess.run(f"sudo ip link del tap_{name} 2>/dev/null || true", shell=True)

            print(f"Cleanup complete for Router {router_id}\n")
        except Exception as e:
            print(f"Error cleaning up Router {router_id}: {e}\n")

    print("Cleanup only mode complete.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Launch Zenoh nodes with network namespace support')
    parser.add_argument('-c', '--clean', action='store_true',
                        help='Only run cleanup for all nodes without launching them')
    args = parser.parse_args()

    # Create our own process group so killpg() in signal handler only affects
    # this process and its children, not the parent shell script
    os.setpgrp()
    process_group_id = os.getpgid(os.getpid())
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Load configuration from the JSON5 file
    with open('NETWORK_CONFIG.json5', 'r') as config_file:
        network_config = json5.load(config_file)
    experiment_name = network_config.get('experiment')
    image_config = network_config.get('docker_image')
    image = image_config.get('tag')
    image_clean = image_config.get('clean_first')
    user_name = network_config.get('user_name')
    routers = network_config.get('routers', {})

    base_dir = f"experiment_data/{experiment_name}"
    # os.makedirs(base_dir, exist_ok=False)
    # os.chdir(base_dir)

    router_list = []
    client_list = []
    clients = network_config.get('clients', {})

    # If --clean flag is set, only run cleanup and exit
    if args.clean:
        cleanup_only()
        sys.exit(0)

    try:
        # Ensure ns3_handover directory exists before launching routers
        subprocess.run("mkdir -p /tmp/ns3_handover", shell=True, check=True)
        subprocess.run("rm -f /tmp/ns3_handover/ns3_handover.json", shell=True, check=True)
        subprocess.run("rm -f /tmp/ns3_handover/ns3_handover_event.json", shell=True, check=True)
        subprocess.run("touch /tmp/ns3_handover/ns3_handover_event.json", shell=True, check=True)

        # Launch routers first
        for router_id, router_config in routers.items():
            router_list.append(Router(router_id, router_config))
            # time.sleep(1)

        print("All routers have been launched.\n")

        # Launch clients after routers
        for client_id, client_config in clients.items():
            client_list.append(Client(client_id, client_config))
            # time.sleep(1)

        if client_list:
            print("All clients have been launched.\n")

    except Exception as e:
        print(f"An unhandled exception occurred: {e}", file=sys.stderr)
        cleanup()
        sys.exit(1)

    signal.pause()
