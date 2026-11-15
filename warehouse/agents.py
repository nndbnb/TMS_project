"""Agents own their state and exchange messages instead of editing each other."""

from dataclasses import dataclass
from .environment import ChargingStation
from .model import RobotSpec, Scenario, State, Task, TaskStatus
from .protocol import Kind, MessageBus, Trace
from .strategies import AllocationStrategy, can_execute


@dataclass
class TaskRecord:
    task: Task
    status: TaskStatus = TaskStatus.PENDING
    assignee: str | None = None
    assigned_at: int | None = None
    completed_at: int | None = None
    reason: str | None = None


class Dispatcher:
    def __init__(self, scenario: Scenario, bus: MessageBus, trace: Trace,
                 strategy: AllocationStrategy):
        self.scenario, self.bus, self.trace, self.strategy = scenario, bus, trace, strategy
        self.records = {task.id: TaskRecord(task) for task in scenario.tasks}
        self.specs = {robot.id: robot for robot in scenario.robots}
        self.statuses: dict[str, dict] = {}
        self.released: set[str] = set()

    def step(self, tick: int):
        for message in self.bus.receive("dispatcher"):
            if message.sender not in self.specs:
                self.trace.add(tick, "ignored_message", "dispatcher", reason="unknown_sender")
                continue
            if message.kind == Kind.STATUS:
                self.statuses[message.sender] = message.body
                continue
            record = self.records.get(message.task_id)
            if record is None or record.assignee != message.sender:
                self.trace.add(tick, "ignored_message", "dispatcher", reason="wrong_assignment")
                continue
            if message.kind == Kind.ACCEPT and record.status == TaskStatus.OFFERED:
                record.status = TaskStatus.ACTIVE
                self.trace.add(tick, "task_accepted", "dispatcher", task_id=record.task.id,
                               robot_id=message.sender)
            elif message.kind == Kind.REJECT and record.status == TaskStatus.OFFERED:
                record.status, record.assignee, record.assigned_at = TaskStatus.PENDING, None, None
                self.statuses.pop(message.sender, None)  # wait for a fresh STATUS
                self.trace.add(tick, "task_rejected", "dispatcher", task_id=record.task.id,
                               reason=message.body.get("reason"))
            elif message.kind == Kind.COMPLETE and record.status == TaskStatus.ACTIVE:
                record.status, record.completed_at = TaskStatus.COMPLETED, tick
                self.trace.add(tick, "task_completed", "dispatcher", task_id=record.task.id,
                               robot_id=message.sender)

        for record in self.records.values():
            if record.task.release <= tick and record.task.id not in self.released:
                self.released.add(record.task.id)
                self.trace.add(tick, "task_released", "dispatcher", task_id=record.task.id)
                if not any(can_execute(r, record.task, self.scenario.home, self.scenario.reserve)
                           for r in self.specs.values()):
                    record.status = TaskStatus.UNASSIGNABLE
                    record.reason = "Ни один робот не проходит ограничения массы и энергии полного рейса"
                    self.trace.add(tick, "task_unassignable", "dispatcher", task_id=record.task.id,
                                   reason=record.reason)

        reserved = {r.assignee for r in self.records.values()
                    if r.status in (TaskStatus.OFFERED, TaskStatus.ACTIVE)}
        pending = sorted((r for r in self.records.values()
                          if r.status == TaskStatus.PENDING and r.task.release <= tick),
                         key=lambda r: (-r.task.priority, r.task.release, r.task.id))
        for record in pending:
            candidates = [spec for name, spec in self.specs.items()
                          if name not in reserved
                          and self.statuses.get(name, {}).get("state") == State.IDLE
                          and can_execute(spec, record.task, self.scenario.home, self.scenario.reserve)]
            chosen = self.strategy.choose(record.task, candidates, self.scenario.home)
            if chosen is not None:
                record.status, record.assignee, record.assigned_at = TaskStatus.OFFERED, chosen.id, tick
                reserved.add(chosen.id)
                self.bus.send(tick, "dispatcher", chosen.id, Kind.ASSIGN,
                              record.task.id, task=record.task.to_dict())

    @property
    def terminal(self):
        return all(r.status in (TaskStatus.COMPLETED, TaskStatus.UNASSIGNABLE)
                   for r in self.records.values())


class Robot:
    def __init__(self, spec: RobotSpec, scenario: Scenario, bus: MessageBus,
                 trace: Trace, station: ChargingStation):
        self.spec, self.scenario, self.bus, self.trace, self.station = spec, scenario, bus, trace, station
        self.position, self.battery, self.state = scenario.home, spec.battery, State.IDLE
        self.task: Task | None = None
        self.travelled = 0
        self.charge_wait_ticks = 0
        self._published = None

    def _transition(self, tick, state):
        if self.state != state:
            self.trace.add(tick, "state_changed", self.spec.id, previous=self.state, state=state,
                           task_id=self.task.id if self.task else None)
            self.state = state

    def _publish(self, tick):
        # State changes are enough for allocation; battery and location are snapshots.
        if self._published != self.state:
            self.bus.send(tick, self.spec.id, "dispatcher", Kind.STATUS, state=self.state,
                          battery=self.battery, position=self.position)
            self._published = self.state

    def _move(self, tick, target):
        if self.position == target:
            return True
        x, y = self.position
        if x != target[0]:
            x += 1 if x < target[0] else -1
        else:
            y += 1 if y < target[1] else -1
        if self.battery <= 0:
            raise RuntimeError("Нарушен инвариант энергии")
        self.position, self.battery = (x, y), self.battery - 1
        self.travelled += 1
        self.trace.add(tick, "moved", self.spec.id, position=self.position, battery=self.battery,
                       task_id=self.task.id)
        return self.position == target

    def _assignment(self, tick, message):
        if message.sender != "dispatcher" or message.kind != Kind.ASSIGN:
            self.trace.add(tick, "ignored_message", self.spec.id, reason="unexpected_sender_or_kind")
            return
        data = message.body["task"]
        task = Task(**{**data, "pickup": tuple(data["pickup"]), "dropoff": tuple(data["dropoff"])})
        reason = None
        if self.state != State.IDLE or self.task is not None:
            reason = "busy"
        elif task.id != message.task_id or task.release > tick:
            reason = "invalid_task"
        elif not can_execute(self.spec, task, self.scenario.home, self.scenario.reserve):
            reason = "capacity_or_energy"
        elif self.position != self.scenario.home or self.battery != self.spec.battery:
            reason = "not_ready"
        if reason:
            self.bus.send(tick, self.spec.id, "dispatcher", Kind.REJECT, message.task_id, reason=reason)
            self._published = None  # REJECT must be followed by a fresh readiness report.
            return
        self.task = task
        self._transition(tick, State.TO_PICKUP)
        self.bus.send(tick, self.spec.id, "dispatcher", Kind.ACCEPT, task.id)

    def step(self, tick: int):
        for message in self.bus.receive(self.spec.id):
            self._assignment(tick, message)
        if self.state == State.TO_PICKUP:
            if self._move(tick, self.task.pickup):
                self._transition(tick, State.LOADING)
        elif self.state == State.LOADING:
            self.trace.add(tick, "loaded", self.spec.id, task_id=self.task.id)
            self._transition(tick, State.TO_DROPOFF)
        elif self.state == State.TO_DROPOFF:
            if self._move(tick, self.task.dropoff):
                self._transition(tick, State.UNLOADING)
        elif self.state == State.UNLOADING:
            self.trace.add(tick, "delivered", self.spec.id, task_id=self.task.id)
            self._transition(tick, State.RETURNING)
        elif self.state == State.RETURNING:
            if self._move(tick, self.scenario.home):
                self.bus.send(tick, self.spec.id, "dispatcher", Kind.COMPLETE, self.task.id)
                self.task = None
                self._transition(tick, State.WAITING_CHARGE if self.battery < self.spec.battery else State.IDLE)
        elif self.state == State.WAITING_CHARGE:
            if self.station.request(self.spec.id):
                self._transition(tick, State.CHARGING)
            else:
                self.charge_wait_ticks += 1
        elif self.state == State.CHARGING:
            self.battery = min(self.spec.battery, self.battery + self.scenario.charge_rate)
            self.trace.add(tick, "charged", self.spec.id, battery=self.battery)
            if self.battery == self.spec.battery:
                self.station.release(self.spec.id)
                self._transition(tick, State.IDLE)
        self._publish(tick)

    def snapshot(self):
        return {"id": self.spec.id, "position": list(self.position), "battery": self.battery,
                "state": self.state, "task_id": self.task.id if self.task else None}
