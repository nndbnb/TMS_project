"""Explicit baseline allocation; alternative algorithms belong to lab 2."""

from typing import Protocol
from .model import Point, RobotSpec, Task


class AllocationStrategy(Protocol):
    def choose(self, task: Task, candidates: list[RobotSpec], home: Point) -> RobotSpec | None: ...


class LeastCapacitySlack:
    """Preserve high-capacity robots for heavy tasks; ties use the robot id."""

    def choose(self, task, candidates, home):
        return min(candidates, key=lambda r: (r.capacity - task.weight, r.id), default=None)


def can_execute(spec: RobotSpec, task: Task, home: Point, reserve: int) -> bool:
    return spec.capacity >= task.weight and task.mission_distance(home) + reserve <= spec.battery
