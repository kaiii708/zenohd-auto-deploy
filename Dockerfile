FROM eclipse/zenoh

# 1. Define the ARG with a default value (build-time variable)
ARG BINARY=zenoh/zenohd

# 2. Convert the ARG value into an ENV variable (runtime variable)
#    This makes the value available to your entrypoint.sh script.
ENV BINARY=${BINARY}

WORKDIR /

# COPY dependency/* /
# COPY zenohd zenohd

# RUN chmod +x zenohd
ENV RUST_LOG=\
zenoh::net::routing::dispatcher::resource=debug,\
zenoh::net::routing::dispatcher::pubsub=trace,\
zenoh::net::routing::dispatcher::face=trace,\
zenoh::net::routing::hat::router::pubsub=trace,\
zenoh::net::routing::hat::router=trace,\
zenoh::net::routing::hat::client::pubsub=trace,\
zenoh::net::routing::hat::client=trace,\
zenoh::api::session=trace,\
zenoh::net::runtime::orchestrator=trace