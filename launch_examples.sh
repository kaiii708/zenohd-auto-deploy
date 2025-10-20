#!/bin/bash

set -e

# This script runs a specific Zenoh example inside a Docker container.
# It overrides the default entrypoint to execute the
# example binary provided as an argument.

# --- Configuration ---
# The local path containing your compiled Zenoh binaries.
# IMPORTANT: Update this path to match your system.
ZENOH_TARGET_PATH="/home/vivian/dev/zenoh/target/x86_64-unknown-linux-musl/release"

# The name of the Docker image.
DOCKER_IMAGE="kaiii708/zenoh:volume"
EXPOSE_PORT=7447
# --- Script Logic ---

# Check if an argument (the example binary name) was provided.
if [ -z "$1" ]; then
  echo "Error: You must provide the name of the example binary to run."
  echo "Usage: ./launch_examples.sh <example_name>"
  exit 1
fi

EXAMPLE_BINARY=$1

# This is the command that will be executed inside the container.
# 'exec ./${EXAMPLE_BINARY}': Executes the example binary. Using 'exec' is a
# good practice as it replaces the shell, ensuring signals like Ctrl+C
# are passed directly to your program.
COMMAND_TO_RUN="exec /zenoh/examples/${EXAMPLE_BINARY} --mode client --no-multicast-scouting"

echo "--- Starting Zenoh example '${EXAMPLE_BINARY}' in Docker ---"

# Execute the Docker command.
# --init: Ensures proper process management.
# -it: Runs in interactive mode so you can see the output and use Ctrl+C.
# --rm: Automatically removes the container when it exits.
# --entrypoint: Overrides the default command with a basic shell.
# -v: Mounts your local build directory into the container at /zenoh.
# The final '-c "${COMMAND_TO_RUN}"' passes our custom command string to the shell.
docker run \
  --init \
  -it \
  --rm \
  --entrypoint /bin/sh \
  -v "${ZENOH_TARGET_PATH}:/zenoh" \
  "${DOCKER_IMAGE}" \
  -c "${COMMAND_TO_RUN}"

echo "--- Docker container exited. ---"






# ---------------

# #!/bin/sh

# set -e

# if [ -z "$1" ]; then
#   echo "Usage: $0 <example_binary_name>"
#   exit 1
# fi

# EXAMPLE_TO_RUN=$1

# docker run --init -it --rm --entrypoint /bin/sh \
#   -p 7447:7447/tcp \
#   -p 8000:8000/tcp \
#   -v /home/vivian/dev/zenoh/target/x86_64-unknown-linux-musl/release:/zenoh \
#   eclipse/zenoh -c "cd /zenoh/examples && ./$EXAMPLE_TO_RUN"