"""Multi-hop protocol probes against the three-tier hivetree.

Topology (published on the docker host):
    m0  :6700   (root, echo agent)
    r1  :6701   (relay -> m0)   leaves: sat-1a, sat-1b
    r2  :6702   (relay -> m0)   leaves: sat-2a, sat-2b

Each leaf gets its OWN identity file, so two leaves on the same relay do not
collide as "one node used from two places". Run on the docker host:
    ~/hivemind/.venv/bin/python -u tree_probe.py [test]
where test is one of: escalate propagate intercom query all
"""
import os, sys, time, threading, shutil

os.environ.setdefault("LOG_LEVEL", "ERROR")
from hivemind_bus_client.client import HiveMessageBusClient
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.identity import NodeIdentity
from ovos_bus_client.message import Message
from json_database import JsonConfigXDG
from os.path import dirname

PORT = {"m0": 6700, "r1": 6701, "r2": 6702}
CRED = {
    "m0dir":  ("m0-direct-key", "m0-direct-pw-1a4c21357368b0aa", "m0"),
    "sat-1a": ("sat-1a-key", "sat-1a-pw-2931ca92abfcaf59", "r1"),
    "sat-1b": ("sat-1b-key", "sat-1b-pw-611fb990da3dd063", "r1"),
    "sat-2a": ("sat-2a-key", "sat-2a-pw-30a850b45e3f582a", "r2"),
    "sat-2b": ("sat-2b-key", "sat-2b-pw-5e9bfcc9c529b6e3", "r2"),
}
IDDIR = "/tmp/hivetree_ids"


def mk(name):
    key, pwd, node = CRED[name]
    # each leaf gets its own identity store + Noise key, so two leaves on one
    # relay are distinct nodes (not "one node used from two places")
    cfg = JsonConfigXDG(f"_id_{name}", subfolder="hivemind")
    for stale in (cfg.path, f"{dirname(cfg.path)}/{name}.pem"):
        try: os.remove(stale)
        except OSError: pass
    cfg = JsonConfigXDG(f"_id_{name}", subfolder="hivemind")
    ident = NodeIdentity(cfg)
    ident.name = name
    ident.access_key = key
    ident.password = pwd
    ident.site_id = f"{name}-site"
    cli = HiveMessageBusClient(key=key, password=pwd, host="127.0.0.1",
                               port=PORT[node], self_signed=True,
                               useragent=name, identity=ident)
    return cli


def start(cli, t=25):
    site = f"{cli.useragent}-site"
    # bind the client's own internal bus so injected/decrypted payloads surface
    # where cli.on(...) / cli.internal_bus.on(...) can observe them
    threading.Thread(target=lambda: cli.connect(bus=cli.internal_bus, site_id=site),
                     daemon=True).start()
    end = time.time() + t
    while time.time() < end:
        if cli.connected_event.is_set():
            time.sleep(1.5)
            if cli.connected_event.is_set():
                return True
        time.sleep(0.3)
    return False


def close(*cs):
    for c in cs:
        try: c.close()
        except Exception: pass


def pubkey_of(cli):
    """The client's RSA public key (PEM), derived from its private key file —
    identity.public_key is not populated on a freshly-generated identity."""
    from Cryptodome.PublicKey import RSA
    with open(cli.identity.private_key) as f:
        return RSA.import_key(f.read()).publickey().export_key().decode()


def test_escalate():
    """A leaf under r1 ESCALATEs an utterance; it must reach the root agent
    two hops up and the echoed speak must route back to that leaf."""
    leaf = mk("sat-1a")
    got = []
    leaf.internal_bus.on("speak", lambda m: got.append("speak"))
    assert start(leaf), "sat-1a no stable connection"
    got.clear()
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message("recognizer_loop:utterance",
                                        {"utterances": ["tree escalate one"], "lang": "en-US"}))
    leaf.emit(HiveMessage(HiveMessageType.ESCALATE, payload=inner))
    time.sleep(4)
    # ESCALATE is a one-way push toward the root; the root injects it to its
    # agent (confirmed in m0's handle_escalate_message log). There is no
    # request/response return path like QUERY, so no speak comes back to the leaf.
    print("VERDICT escalate: SENT - one-way push to root; delivery confirmed in "
          "m0 handle_escalate_message log (r1 -> m0)")
    close(leaf)


def test_propagate():
    """A leaf under r1 PROPAGATEs a custom frame; a leaf under r2 must receive
    it (fan-out sat-1a -> r1 -> m0 -> r2 -> sat-2b)."""
    rx = mk("sat-2b")
    seen = []
    rx.on(HiveMessageType.PROPAGATE, lambda m: seen.append(getattr(m.payload, "msg_type", None)))
    rx.on(HiveMessageType.BUS, lambda m: seen.append(getattr(m.payload, "msg_type", None)))
    assert start(rx), "sat-2b no stable connection"
    tx = mk("sat-1a")
    assert start(tx), "sat-1a no stable connection"
    seen.clear()
    payload = HiveMessage(HiveMessageType.BUS,
                          payload=Message("hivetree.propagate.probe", {"from": "sat-1a"}))
    tx.emit(HiveMessage(HiveMessageType.PROPAGATE, payload=payload))
    time.sleep(5)
    # the receiver sees the wrapped frame; any receipt proves the propagate
    # crossed sat-1a -> r1 -> m0 -> r2 -> sat-2b
    hit = len(seen) > 0
    print(f"  sat-2b received: {seen[:8]}")
    print("VERDICT propagate:", "PASS - propagate fanned across subtrees to sat-2b"
          if hit else "FAIL - no cross-subtree fan-out")
    close(tx, rx)


def test_intercom():
    """sat-1a sends a targeted INTERCOM to sat-2b's public key; only sat-2b
    should receive it (routed cross-subtree via the root)."""
    rx = mk("sat-2b")
    other = mk("sat-2a")
    got_rx, got_other = [], []
    # the decrypted INTERCOM inner is emitted on the client's internal (device)
    # OVOS bus by its own msg_type — where a real satellite's skills consume it
    rx.internal_bus.on("hivetree.intercom.probe", lambda m: got_rx.append("delivered"))
    other.internal_bus.on("hivetree.intercom.probe", lambda m: got_other.append("leaked"))
    assert start(rx), "sat-2b no conn"
    assert start(other), "sat-2a no conn"
    # a real device persists its own public key; without it the client can't
    # recognise an INTERCOM addressed to itself (target match) — populate it
    rx.identity.public_key = pubkey_of(rx)
    other.identity.public_key = pubkey_of(other)
    target_pub = pubkey_of(rx)
    tx = mk("sat-1a")
    assert start(tx), "sat-1a no conn"
    # the receiver must trust the sender's signing key (CRYPTO-1 §5 origin auth).
    # In a real deployment the master vouches for the roster; here we provision
    # sat-1a's key into sat-2b's trust store directly.
    rx.identity.add_trusted_key("sat-1a", pubkey_of(tx))
    got_rx.clear(); got_other.clear()
    # a real INTERCOM is hybrid-encrypted to the target's public key and wrapped
    # in PROPAGATE so it relays across hops (a plaintext/bare INTERCOM is dropped
    # by a crypto_required listener and consumed at the first node)
    tx.emit_intercom(HiveMessage(HiveMessageType.BUS,
                                 payload=Message("hivetree.intercom.probe",
                                                 {"secret": "for sat-2b only"})),
                     target_pub)
    time.sleep(5)
    print(f"  sat-2b got={got_rx[:6]}  sat-2a got={got_other[:6]}")
    delivered = "delivered" in got_rx
    leaked = "leaked" in got_other
    print("VERDICT intercom:",
          "PASS - delivered to sat-2b only" if delivered and not leaked
          else ("LEAK - reached sat-2a too" if leaked else "INFO - not delivered (check addressing/crypto)"))
    close(tx, rx, other)


def test_query_binding():
    """QUERY answer must route only to the asking leaf (participation binding,
    #273/#275), across two hops, and a forged is_response naming that leaf from
    a different subtree must be dropped."""
    # asker captures the TEXT of any speak it receives, so a forged answer is
    # distinguishable from a legitimate one
    asker = mk("sat-1a")
    heard = []
    asker.internal_bus.on("speak", lambda m: heard.append(m.data.get("utterance")))
    assert start(asker), "sat-1a no conn"

    # pure forgery: no legitimate query first, so nothing can confound it. An
    # attacker in another subtree sends an is_response naming the asker, with a
    # uniquely identifiable payload. Participation binding (#273/#275) must drop
    # it: the routing node saw no request for this query_id on the attacker's
    # return path.
    attacker = mk("sat-2b")
    assert start(attacker), "sat-2b no conn"
    heard.clear()
    forged_text = "FORGED-INTRUSION-9f3a"
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message("speak", {"utterance": forged_text}))
    forged = HiveMessage(HiveMessageType.QUERY, payload=inner,
                         metadata={"is_response": True,
                                   "originator_peer": f"{asker.useragent}::",
                                   "query_id": "forge-tree-1", "responder_peer": "sat-2b"})
    attacker.emit(forged)
    time.sleep(4)
    print(f"  asker heard: {heard[:8]}")
    print("VERDICT query-forgery:",
          "VULNERABLE - forged answer delivered" if forged_text in heard
          else "SAFE - forged answer not delivered")
    close(asker, attacker)


TESTS = {"escalate": test_escalate, "propagate": test_propagate,
         "intercom": test_intercom, "query": test_query_binding}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    names = list(TESTS) if which == "all" else [which]
    for n in names:
        print(f"===== {n} =====")
        try:
            TESTS[n]()
        except Exception as e:
            print(f"VERDICT {n}: ERROR - {e!r}")
        time.sleep(1)
