#!/usr/bin/env python3
import signal
import os
import sys
import subprocess
import json5
import time


def cleanup():
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
        self.connect_endpoint = self.config.get('connect_endpoint') or None
        if self.connect_endpoint:
            self.port_expose = self.config.get('port_expose')

        self.launch_zenohd()
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
        name = f"{self.id}"
        print(f"Cleaning up for Node {self.id}...\n")
        self.run_shell_command(f"sudo iptables -D FORWARD -m physdev --physdev-is-bridged -i br_{name} -j ACCEPT 2>/dev/null || true")

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

            docker_run_cmd = f"docker run --init --name {self.session_name} --network none --rm {volume_arg}"
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
        
        base_command += f" > >(tee ./zenohd_{self.id}.log) 2> >(tee ./zenohd_{self.id}_err.log >&2)"
        base_command += "; echo \$? > /tmp/exit_code' C-m"
        if not self.is_localhost:
            base_command += "\""

        self.run_shell_command(base_command)

    def setup_netns_veth(self):
        addr = self.listen_endpoint.split('/')[1].split(':')[0]
        name = f"{self.id}"
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

# class Client():
#     def __init__


if __name__ == "__main__":
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
    try:
        for router_id, router_config in routers.items():

            router_list.append(Router(router_id, router_config))
            time.sleep(1)
        # for router in router_list:
        #     router.check_if_error_while_launch()

        print("All routers have been launched.\n")

    except Exception as e:
        print(f"An unhandled exception occurred: {e}", file=sys.stderr)
        cleanup()
        sys.exit(1)
    # client_list = []
    # for client_id, 
    signal.pause()