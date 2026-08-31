# hivetree — a three-tier HiveMind test mesh

A disposable multi-node HiveMind deployment for exercising the parts of the
protocol a single hub can not reach: multi-hop routing, cross-subtree delivery,
and relay-loss resilience.

```
            m0   root master + echo agent      host :6700
           /  \
         r1    r2   relays, upstream -> m0      host :6701 / :6702
        /  \   /  \
   sat-1a 1b 2a 2b   leaf probe clients (not containers)
```

Each node runs one container: a local OVOS messagebus plus `hivemind-core`. The
root also runs a small echo agent that answers an utterance with `echo <text>`,
so a message that travels up the tree produces a `speak` that routes back — no
skill model load, the tree forms in seconds. Relays carry an empty bus; their
downstream traffic escalates to the root.

Upstream links are pre-wired: each relay holds a fixed client credential on the
root (see `.env`), so `docker compose up` forms the tree with no manual copy
step. Every client is granted the full routing message-type whitelist
(`hivemind-core` is deny-by-default).

## Run

```
cp .env.example .env               # throwaway test credentials for the tree
docker compose --env-file .env build
docker compose --env-file .env up -d
docker compose logs -f r1 r2        # look for "Upstream master: m0:5678" + a Noise session
```

## Probe

From the docker host, against the published ports:

```
~/hivemind/.venv/bin/python -u probes/tree_probe.py all
```

Tests: `escalate` (leaf → root agent → back, two hops), `propagate` (fan-out
across subtrees), `intercom` (targeted cross-subtree delivery), `query`
(participation binding: the answer returns only to the asking leaf, and a forged
`is_response` from another subtree is dropped).

Relay-loss check (the emit path must keep serving other peers when one relay
dies):

```
docker stop hivetree-r1     # then re-run a probe on an r2 leaf; it must not stall
```

## Tear down

```
docker compose down -v
```

The image installs the latest HiveMind alphas plus the pinned `hivemind-core`
wheel in `wheels/`, so the mesh runs the exact build under test.
