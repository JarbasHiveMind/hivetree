#!/usr/bin/env bash
# One hivemind node = a local (empty) OVOS messagebus + hivemind-core, plus a
# mock echo agent on the root. Role and upstream wiring come from the env.
set -uo pipefail

ROLE="${NODE_ROLE:-root}"
CFG="$HOME/.config/hivemind-core"
mkdir -p "$CFG"

agent_and_net='"agent_protocol": {"module": "hivemind-ovos-agent-plugin",
                     "hivemind-ovos-agent-plugin": {"host": "127.0.0.1", "port": 8181}},
  "network_protocol": {"module": "hivemind-websocket-plugin",
                       "hivemind-websocket-plugin": {"host": "0.0.0.0", "port": 5678, "ssl": false}}'

if [ "$ROLE" = "relay" ]; then
  cat > "$CFG/server.json" <<EOF
{
  $agent_and_net,
  "upstream": {"enabled": true, "host": "${UPSTREAM_HOST}", "port": 5678,
               "key": "${UPSTREAM_KEY}", "password": "${UPSTREAM_PWD}",
               "ssl": false, "self_signed": true}
}
EOF
else
  cat > "$CFG/server.json" <<EOF
{
  $agent_and_net
}
EOF
fi

# local OVOS messagebus (the node's agent bus)
ovos-messagebus > /var/log/messagebus.log 2>&1 &
for i in $(seq 1 60); do
  python -c "import socket; socket.create_connection(('127.0.0.1',8181),1)" 2>/dev/null && break
  sleep 1
done

# only the root answers utterances/queries
if [ "$ROLE" = "root" ]; then
  sleep 3
  python -u /app/mock_agent.py > /var/log/mock_agent.log 2>&1 &
fi

# provision clients: CLIENTS = space-separated  name:access_key:password
allow_all() {
  # allowed_types is a whitelist that gates BOTH the HiveMessage type and, for
  # a BUS/QUERY/ESCALATE payload, the inner OVOS message type injected to the
  # agent. Grant the routing types plus the utterance/answer and probe types.
  for t in bus broadcast propagate escalate intercom query cascade rendezvous ping \
           recognizer_loop:utterance speak \
           hivetree.propagate.probe hivetree.intercom.probe; do
    hivemind-core allow-msg "$t" "$1" > /dev/null 2>&1
  done
}
for spec in ${CLIENTS:-}; do
  IFS=':' read -r cname ckey cpwd <<< "$spec"
  hivemind-core add-client --name "$cname" --access-key "$ckey" \
      --password "$cpwd" --allow-weak-password > /dev/null 2>&1
  allow_all "$ckey"
done

echo "[entrypoint] role=$ROLE upstream=${UPSTREAM_HOST:-none} clients=[${CLIENTS:-none}] — starting listener on :5678"
exec hivemind-core listen
