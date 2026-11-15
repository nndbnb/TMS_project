"""Shared non-agent resource. FIFO is a baseline rule, not a negotiation protocol."""

from collections import deque


class ChargingStation:
    def __init__(self, slots: int):
        self.slots = slots
        self.owners: set[str] = set()
        self.queue: deque[str] = deque()

    def request(self, robot_id: str) -> bool:
        if robot_id in self.owners:
            return True
        if robot_id not in self.queue:
            self.queue.append(robot_id)
        if len(self.owners) < self.slots and self.queue[0] == robot_id:
            self.queue.popleft()
            self.owners.add(robot_id)
            return True
        return False

    def release(self, robot_id: str):
        self.owners.remove(robot_id)
