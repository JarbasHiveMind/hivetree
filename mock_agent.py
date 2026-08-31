"""Minimal deterministic agent for the root node's OVOS bus.

It answers a spoken utterance with an echo, so an ESCALATE or utterance that
travels up the tree to the root produces a `speak` that routes back to the
originating leaf. No skills, no model load — the tree forms in seconds.
"""
import time

from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message

bus = MessageBusClient(host="127.0.0.1", port=8181)
bus.run_in_thread()
bus.connected_event.wait(30)


def _answer(utt: str, m: Message) -> None:
    reply = f"echo {utt}"
    bus.emit(m.forward("ovos.utterance.speak", {"utterance": reply}))
    bus.emit(m.forward("speak", {"utterance": reply, "lang": "en-US"}))
    bus.emit(m.forward("ovos.utterance.handled"))


def on_utterance(m: Message) -> None:
    utts = m.data.get("utterances") or [m.data.get("utterance", "?")]
    _answer(utts[0], m)


bus.on("recognizer_loop:utterance", on_utterance)
print("[mock_agent] connected, echoing recognizer_loop:utterance -> speak")

while True:
    time.sleep(3600)
