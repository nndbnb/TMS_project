"""Fixed-step scheduler; it transports messages but never chooses for an agent."""

from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
from .agents import Dispatcher, Robot
from .environment import ChargingStation
from .model import Scenario, State, TaskStatus
from .protocol import MessageBus, Trace
from .strategies import AllocationStrategy, LeastCapacitySlack


class Simulation:
    def __init__(self, scenario: Scenario, strategy: AllocationStrategy | None = None):
        self.scenario, self.trace = scenario, Trace()
        self.bus = MessageBus({"dispatcher", *(r.id for r in scenario.robots)}, self.trace)
        self.station = ChargingStation(scenario.charger_slots)
        self.dispatcher = Dispatcher(scenario, self.bus, self.trace, strategy or LeastCapacitySlack())
        self.robots = [Robot(r, scenario, self.bus, self.trace, self.station)
                       for r in sorted(scenario.robots, key=lambda r: r.id)]
        self.tick = 0
        self.finished = False

    def step(self):
        if self.finished:
            raise RuntimeError("Симуляция уже завершена")
        self.bus.deliver(self.tick)
        self.dispatcher.step(self.tick)
        for robot in self.robots:
            robot.step(self.tick)
        self._check_invariants()
        self.trace.add(self.tick, "snapshot", "scheduler", robots=[r.snapshot() for r in self.robots],
                       tasks={name: record.status for name, record in self.dispatcher.records.items()},
                       charger_owners=sorted(self.station.owners), charger_queue=list(self.station.queue))
        self.finished = (self.dispatcher.terminal and all(r.state == State.IDLE for r in self.robots)
                         and not self.bus.pending)
        self.tick += 1

    def _check_invariants(self):
        if len(self.station.owners) > self.station.slots:
            raise RuntimeError("Превышено число зарядных мест")
        assigned = [r.assignee for r in self.dispatcher.records.values()
                    if r.status in (TaskStatus.OFFERED, TaskStatus.ACTIVE)]
        if len(assigned) != len(set(assigned)):
            raise RuntimeError("У робота более одного активного назначения")
        for robot in self.robots:
            x, y = robot.position
            if not (0 <= robot.battery <= robot.spec.battery and
                    0 <= x < self.scenario.width and 0 <= y < self.scenario.height):
                raise RuntimeError("Нарушены границы состояния робота")
            if robot.state == State.IDLE and (robot.position != self.scenario.home or
                                              robot.battery != robot.spec.battery):
                raise RuntimeError("Неготовый робот объявлен свободным")

    def run(self, max_ticks=1000, on_step=None):
        if type(max_ticks) is not int or max_ticks < 1:
            raise ValueError("max_ticks должен быть положительным целым числом")
        while not self.finished and self.tick < max_ticks:
            self.step()
            if on_step:
                on_step(self)
        return self.summary()

    def summary(self):
        records = list(self.dispatcher.records.values())
        completed = [r for r in records if r.status == TaskStatus.COMPLETED]
        counts = Counter(r.status for r in records)
        deliveries = {e["task_id"]: e["tick"] for e in self.trace.events if e["event"] == "delivered"}
        return {
            "scenario": self.scenario.name, "finished": self.finished, "ticks": self.tick,
            "tasks_total": len(records), "tasks_completed": len(completed),
            "tasks_unassignable": counts[TaskStatus.UNASSIGNABLE],
            "tasks_unfinished": sum(r.status not in (TaskStatus.COMPLETED, TaskStatus.UNASSIGNABLE) for r in records),
            "messages_sent": self.bus.sent,
            "messages_by_kind": dict(Counter(e["message"]["kind"] for e in self.trace.events if e["event"] == "message_sent")),
            "distance": sum(r.travelled for r in self.robots),
            "charge_wait_ticks": sum(r.charge_wait_ticks for r in self.robots),
            "mission_makespan": max((r.completed_at for r in completed), default=None),
            "tasks": [{"id": r.task.id, "status": r.status, "assignee": r.assignee,
                       "assigned_at": r.assigned_at, "delivered_at": deliveries.get(r.task.id),
                       "completed_at": r.completed_at, "reason": r.reason,
                       "delivery_tardiness": (max(0, deliveries[r.task.id] - r.task.deadline)
                                              if r.task.id in deliveries and r.task.deadline is not None else None)}
                      for r in records],
            "robots": [{**r.snapshot(), "distance": r.travelled,
                        "charge_wait_ticks": r.charge_wait_ticks} for r in self.robots],
        }

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "summary.json").write_text(json.dumps(self.summary(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (directory / "trace.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.trace.events), encoding="utf-8")
        (directory / "scenario.json").write_text(json.dumps(asdict(self.scenario), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
