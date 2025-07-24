#!/usr/bin/env python3
import signal
import os
import sys
import subprocess
import json5


def signal_handler(sig, frame):
    print(f"\nReceived signal:{sig}, leaving...\n")
    
    if router_list:
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
        self.session_name = f"zenohd_{self.listen_port}"
        self.volume = self.config.get('volume') or network_config.get('volume')

        self.launch_zenohd()

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

    def launch_zenohd(self):
        print(f"Launching zenohd for Router {self.id}...\n")

        kill_session_command = f"tmux kill-session -t {self.session_name} 2>/dev/null || true && "
        chdir_command = f"mkdir -p {base_dir} && cd {base_dir} && "
        if self.docker:
            volume_arg = ""
            if self.volume:
                host_path = os.path.abspath(self.volume)
                volume_arg = f"-v {host_path}:/zenohd"

            docker_run_cmd = f"docker run --init -e RUST_LOG=trace --rm {volume_arg} -p {self.listen_port}:7447/tcp {image}"
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
            base_command += f" -l tcp/0.0.0.0:{self.listen_port}"
        elif self.mode == "e":
            connect_points = self.config['connect']
            for remote_id in connect_points:
                base_command += f" -e {routers.get(str(remote_id)).get('listen_endpoint')}"
        
        base_command += f" > >(tee ./zenohd_{self.id}.log) 2> >(tee ./zenohd_{self.id}_err.log >&2)"
        base_command += "; echo \$? > /tmp/exit_code' C-m"
        if not self.is_localhost:
            base_command += "\""

        self.run_shell_command(base_command)
    
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
    for router_id, router_config in routers.items():
        
        router_list.append(Router(router_id, router_config))
        for router in router_list:
            router.check_if_error_while_launch()

    print("All routers have been launched.\n")

    # client_list = []
    # for client_id, 
    signal.pause()