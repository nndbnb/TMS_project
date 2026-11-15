"""Domain objects and strict scenario validation. One tick = one time unit."""

from dataclasses import asdict, dataclass
from enum import StrEnum
import json
from pathlib import Path

Point = tuple[int, int]


def distance(a: Point, b: Point) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class State(StrEnum):
    IDLE = "idle"
    TO_PICKUP = "to_pickup"
    LOADING = "loading"
    TO_DROPOFF = "to_dropoff"
    UNLOADING = "unloading"
    RETURNING = "returning"
    WAITING_CHARGE = "waiting_charge"
    CHARGING = "charging"


class TaskStatus(StrEnum):
    PENDING = "pending"
    OFFERED = "offered"
    ACTIVE = "active"
    COMPLETED = "completed"
    UNASSIGNABLE = "unassignable"


@dataclass(frozen=True)
class Task:
    id: str
    pickup: Point
    dropoff: Point
    weight: int
    release: int = 0
    priority: int = 0
    deadline: int | None = None

    def mission_distance(self, home: Point) -> int:
        return distance(home, self.pickup) + distance(self.pickup, self.dropoff) + distance(self.dropoff, home)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RobotSpec:
    id: str
    capacity: int
    battery: int


@dataclass(frozen=True)
class Scenario:
    name: str
    width: int
    height: int
    home: Point
    charger_slots: int
    charge_rate: int
    reserve: int
    robots: tuple[RobotSpec, ...]
    tasks: tuple[Task, ...]

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        def integer(value, label, minimum=0):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{label}: требуется целое число >= {minimum}")
            return value

        def identifier(value, label):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}: требуется непустая строка")
            return value

        width = integer(data["width"], "width", 1)
        height = integer(data["height"], "height", 1)

        def point(value, label):
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"{label}: ожидается [x, y]")
            x, y = [integer(v, label) for v in value]
            if x >= width or y >= height:
                raise ValueError(f"{label}: точка вне склада")
            return (x, y)

        robots = tuple(RobotSpec(identifier(r["id"], "robot.id"),
                                 integer(r["capacity"], "capacity", 1),
                                 integer(r["battery"], "battery", 1)) for r in data["robots"])
        if not robots or len({r.id for r in robots}) != len(robots):
            raise ValueError("Нужен хотя бы один робот; идентификаторы роботов уникальны")
        if any(r.id == "dispatcher" for r in robots):
            raise ValueError("Идентификатор dispatcher зарезервирован")
        tasks = tuple(Task(identifier(t["id"], "task.id"), point(t["pickup"], "pickup"),
                           point(t["dropoff"], "dropoff"), integer(t["weight"], "weight", 1),
                           integer(t.get("release", 0), "release"),
                           integer(t.get("priority", 0), "priority"),
                           None if t.get("deadline") is None else integer(t["deadline"], "deadline"))
                      for t in data["tasks"])
        if len({t.id for t in tasks}) != len(tasks):
            raise ValueError("Идентификаторы заданий должны быть уникальны")
        return cls(identifier(data["name"], "name"), width, height,
                   point(data["home"], "home"),
                   integer(data.get("charger_slots", 1), "charger_slots", 1),
                   integer(data.get("charge_rate", 3), "charge_rate", 1),
                   integer(data.get("reserve", 2), "reserve"), robots, tasks)
