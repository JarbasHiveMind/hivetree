FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

# The mesh runs the latest published HiveMind alphas. To test an unreleased
# build instead, drop its wheel into a `wheels/` dir next to this Dockerfile and
# uncomment the COPY + reinstall lines below.
RUN uv pip install --system --prerelease=allow \
        hivemind-core hivemind-websocket-protocol hivemind-ovos-agent-plugin \
        ovos-messagebus ovos-utils ovos-bus-client
# COPY wheels/ /wheels/
# RUN uv pip install --system --prerelease=allow --reinstall /wheels/*.whl

COPY entrypoint.sh mock_agent.py /app/
RUN chmod +x /app/entrypoint.sh
WORKDIR /app

# node role + upstream wiring come from the environment (see docker-compose.yml)
ENTRYPOINT ["/app/entrypoint.sh"]
