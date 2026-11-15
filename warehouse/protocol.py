"""Reliable in-memory transport; messages arrive on the following tick."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from enum import StrEnum


class Kind(StrEnum):
    STATUS = "STATUS"
    ASSIGN = "ASSIGN"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class Message:
    sequence: int
    sent_at: int
    deliver_at: int
    sender: str
    receiver: str
    kind: Kind
    task_id: str | None
    body: dict


class Trace:
    def __init__(self):
        self.events: list[dict] = []

    def add(self, tick: int, event: str, actor: str, **details):
        self.events.append({"tick": tick, "event": event, "actor": actor, **details})


class MessageBus:
    def __init__(self, participants: set[str], trace: Trace):
        self.participants = participants
        self.trace = trace
        self.pending: list[Message] = []
        self.inboxes: dict[str, list[Message]] = defaultdict(list)
        self.sent = 0

    def send(self, tick, sender, receiver, kind, task_id=None, **body):
        if sender not in self.participants or receiver not in self.participants:
            raise ValueError("Неизвестный участник канала связи")
        self.sent += 1
        message = Message(self.sent, tick, tick + 1, sender, receiver, kind, task_id, deepcopy(body))
        self.pending.append(message)
        self.trace.add(tick, "message_sent", sender, message=asdict(message))

    def deliver(self, tick):
        ready = [m for m in self.pending if m.deliver_at <= tick]
        self.pending = [m for m in self.pending if m.deliver_at > tick]
        for message in ready:
            self.inboxes[message.receiver].append(message)
            self.trace.add(tick, "message_delivered", message.receiver, sequence=message.sequence)

    def receive(self, participant):
        return self.inboxes.pop(participant, [])
