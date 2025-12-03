
# Zenohd Auto Deploy

## Overview

This repository automates the deployment and management of **Zenoh Daemon (zenohd)** networks for experimental purposes. It simplifies launching, monitoring, and cleaning up Zenoh routers while ensuring automatic data collection for analysis.

---

## Features

- **Automated Deployment**
  - Deploy Zenoh routers locally or remotely based on configuration files.
  - Supports Docker and non-Docker environments.
- **Flexible Configuration**
  - Define listening endpoints and router connections.
  - Assign unique ZIDs for each router instance.
- **Experiment Management**
  - Logs stored under `experiment_data/<your_experiment_name>`.
  - Automatic error checking during deployment.
  - Transfers experiment data back from remote hosts.

---

## Requirements

Ensure the following dependencies are installed before running:

- **Python** ≥ 3.8
- **Docker** (required for Docker-based deployments)
- **tmux** (manages router sessions)
- **rsync** (for transferring data from remote hosts)
- **SSH access** to remote routers (passwordless login recommended)
- Python library:
  - `json5`  
    Install with:
    ```bash
    pip install json5
    ```

---

## Usage

### 1. Prepare the Configuration

Create a configuration file (`NETWORK_CONFIG.json5`) in the repository root:
> 📌 **Note:** Detailed field explanations can be found in `NETWORK_CONFIG_DEFAULT.json5` comments.
```json5
{
    "experiment": "<your_experiment_name>",
    "docker_image": {
        "tag": "eclipse/zenoh:<version>",
        "clean_first": true
    },
    "user_name": "<your_ssh_username>",
    "routers": {
        // Define your routers here. Copy and edit the router config block below
        // to set up as many routers as you need for your Zenoh network.
        // "1": {
        //     ...
        // },
        // "2": {
        //     ...
        // },
        "1": {
            "ssh": "<host_ip_or_hostname>",
            "docker": true,
            "zid": {
                "set": true,
                "value": "<unique_hexadecimal_id>"
            },
            "mode": "l",
            "listen_endpoint": "<proto>/<address>:<port>",
            "connect": []
        }
    }
}
```

---

### 2. Make the Script Executable

Before running, ensure the script has execute permissions:
```bash
chmod +x launch_routers.py
```

---

### 3. Launch Routers

Run the script from the repository root:
```bash
./launch_routers.py
```

---

### 4. Terminate Experiment

Press `Ctrl+C` to gracefully shut down all Zenoh routers and collect experiment data.

---

## Output

- Experiment data is stored under:  
  `experiment_data/<your_experiment_name>`
- Logs:
  - `zenohd_<id>.log`: Standard output
  - `zenohd_<id>_err.log`: Standard error

---

## Network Simulator Integration (launch_nodes.py)

For experiments requiring network simulation (e.g., ns-3), use `launch_nodes.py` instead. This script launches both routers and clients with network namespace setup.

### Usage

```bash
chmod +x launch_nodes.py
./launch_nodes.py
```

### TAP Device Naming

The script creates TAP devices that can be connected to network simulators:

| Node Type | TAP Name | Example |
|-----------|----------|---------|
| Router | `tap_edge{id}` | `tap_edge1`, `tap_edge2` |
| Client | `tap_{executable}` | `tap_z_sub`, `tap_z_pub` |

### Additional Configuration Options

- **`default_route`**: Set default gateway for routers and clients (required for ns-3 routing)
- **`clients`**: Define client nodes with executable, connect endpoints, and multicast scouting options