import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from warehouse.agents import Robot
from warehouse.environment import ChargingStation
from warehouse.model import Scenario, State, TaskStatus
from warehouse.protocol import Kind, MessageBus, Trace
from warehouse.simulation import Simulation

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    return {"name": "test", "width": 5, "height": 5, "home": [0, 0],
            "charger_slots": 1, "charge_rate": 3, "reserve": 2,
            "robots": [{"id": "r1", "capacity": 3, "battery": 20}],
            "tasks": [{"id": "t1", "pickup": [1, 0], "dropoff": [2, 0], "weight": 2}]}


class SimulationTests(unittest.TestCase):
    def run_case(self, data=None, limit=1000):
        sim = Simulation(Scenario.from_dict(data or configuration()))
        result = sim.run(limit)
        return sim, result

    def test_one_delivery_has_real_route_and_return(self):
        sim, result = self.run_case()
        self.assertTrue(result["finished"])
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(result["distance"], 4)
        positions = [tuple(e["position"]) for e in sim.trace.events if e["event"] == "moved"]
        self.assertEqual(positions, [(1, 0), (2, 0), (1, 0), (0, 0)])
        self.assertEqual(result["tasks"][0]["delivered_at"], 5)
        self.assertEqual(result["tasks"][0]["completed_at"], 8)
        self.assertEqual(result["robots"][0]["battery"], 20)

    def test_release_time_and_deadline_are_observed(self):
        data = configuration()
        data["tasks"][0].update(release=15, deadline=16)
        sim, result = self.run_case(data)
        record = result["tasks"][0]
        self.assertGreaterEqual(record["assigned_at"], 15)
        self.assertGreater(record["delivery_tardiness"], 0)
        self.assertTrue(all(e["tick"] >= 15 for e in sim.trace.events if e["event"] == "loaded"))

    def test_weight_and_full_round_trip_budget(self):
        data = configuration()
        data["robots"][0]["battery"] = 5  # route=4, reserve=2 -> infeasible
        data["tasks"].append({"id": "heavy", "pickup": [0, 0], "dropoff": [0, 0], "weight": 4})
        _, result = self.run_case(data)
        self.assertEqual(result["tasks_unassignable"], 2)
        self.assertEqual(result["distance"], 0)
        self.assertTrue(result["finished"])

    def test_exact_energy_boundary_is_feasible(self):
        data = configuration()
        data["robots"][0]["battery"] = 6
        sim, result = self.run_case(data)
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(min(e["battery"] for e in sim.trace.events if e["event"] == "moved"), 2)

    def test_no_double_allocation_and_every_delivery_once(self):
        data = configuration()
        data["robots"].append({"id": "r2", "capacity": 5, "battery": 20})
        data["tasks"] = [{**data["tasks"][0], "id": f"t{i}"} for i in range(12)]
        sim, result = self.run_case(data)
        self.assertEqual(result["tasks_completed"], 12)
        deliveries = [e["task_id"] for e in sim.trace.events if e["event"] == "delivered"]
        self.assertEqual(len(deliveries), len(set(deliveries)))
        self.assertEqual(set(deliveries), {t["id"] for t in data["tasks"]})

    def test_priority_then_release_then_id(self):
        data = configuration()
        task = data["tasks"][0]
        data["tasks"] = [{**task, "id": "b", "priority": 0},
                         {**task, "id": "c", "priority": 2},
                         {**task, "id": "a", "priority": 2}]
        sim, _ = self.run_case(data)
        order = [e["message"]["task_id"] for e in sim.trace.events
                 if e["event"] == "message_sent" and e["message"]["kind"] == Kind.ASSIGN]
        self.assertEqual(order, ["a", "c", "b"])

    def test_smallest_suitable_robot_is_chosen(self):
        data = configuration()
        data["robots"] = [{"id": "big", "capacity": 10, "battery": 20},
                          {"id": "small", "capacity": 2, "battery": 20}]
        _, result = self.run_case(data)
        self.assertEqual(result["tasks"][0]["assignee"], "small")

    def test_charger_capacity_and_real_waiting(self):
        sim = Simulation(Scenario.load(ROOT / "scenarios" / "charger_queue.json"))
        result = sim.run()
        self.assertTrue(result["finished"])
        self.assertGreater(result["charge_wait_ticks"], 0)
        self.assertTrue(all(len(e["charger_owners"]) <= 1 for e in sim.trace.events if e["event"] == "snapshot"))

    def test_zero_distance_task_still_loads_and_unloads(self):
        data = configuration()
        data["tasks"][0].update(pickup=[0, 0], dropoff=[0, 0])
        sim, result = self.run_case(data)
        self.assertEqual(result["distance"], 0)
        self.assertEqual(result["tasks_completed"], 1)
        self.assertEqual(len([e for e in sim.trace.events if e["event"] == "loaded"]), 1)

    def test_simulation_is_deterministic(self):
        first, _ = self.run_case()
        second, _ = self.run_case()
        self.assertEqual(first.trace.events, second.trace.events)

    def test_tick_limit_preserves_unfinished_state(self):
        _, result = self.run_case(limit=3)
        self.assertFalse(result["finished"])
        self.assertEqual(result["tasks_unfinished"], 1)
        self.assertEqual(result["tasks_completed"], 0)

    def test_empty_task_set_terminates(self):
        data = configuration()
        data["tasks"] = []
        _, result = self.run_case(data)
        self.assertTrue(result["finished"])
        self.assertEqual(result["tasks_total"], 0)

    def test_exports_are_parseable_and_scenario_can_be_replayed(self):
        sim, result = self.run_case()
        with tempfile.TemporaryDirectory() as directory:
            sim.save(directory)
            path = Path(directory)
            self.assertEqual(json.loads((path / "summary.json").read_text()), result)
            self.assertEqual(len((path / "trace.jsonl").read_text().splitlines()), len(sim.trace.events))
            replay = Simulation(Scenario.load(path / "scenario.json"))
            self.assertEqual(replay.run(), result)

    def test_all_demonstration_scenarios_terminate(self):
        for path in sorted((ROOT / "scenarios").glob("*.json")):
            with self.subTest(scenario=path.name):
                result = Simulation(Scenario.load(path)).run()
                self.assertTrue(result["finished"])
                self.assertEqual(result["tasks_unfinished"], 0)


class ProtocolTests(unittest.TestCase):
    def test_transport_delays_and_copies_payload(self):
        bus = MessageBus({"a", "b"}, Trace())
        payload = {"items": [1]}
        bus.send(0, "a", "b", Kind.STATUS, payload=payload)
        payload["items"].append(2)
        bus.deliver(0)
        self.assertEqual(bus.receive("b"), [])
        bus.deliver(1)
        self.assertEqual(bus.receive("b")[0].body["payload"], {"items": [1]})

    def test_unknown_transport_participant_rejected(self):
        bus = MessageBus({"a"}, Trace())
        with self.assertRaises(ValueError):
            bus.send(0, "a", "unknown", Kind.STATUS)

    def test_robot_can_reject_infeasible_order_locally(self):
        scenario = Scenario.from_dict(configuration())
        trace = Trace()
        bus = MessageBus({"dispatcher", "r1"}, trace)
        robot = Robot(scenario.robots[0], scenario, bus, trace, ChargingStation(1))
        task = scenario.tasks[0].to_dict()
        task["weight"] = 100
        bus.send(0, "dispatcher", "r1", Kind.ASSIGN, "t1", task=task)
        bus.deliver(1)
        robot.step(1)
        self.assertEqual(robot.state, State.IDLE)
        self.assertTrue(any(m.kind == Kind.REJECT for m in bus.pending))
        self.assertIsNone(robot.task)

    def test_wrong_robot_cannot_complete_someone_elses_task(self):
        data = configuration()
        data["robots"].append({"id": "r2", "capacity": 10, "battery": 20})
        sim = Simulation(Scenario.from_dict(data))
        sim.step()
        sim.step()  # t1 -> r1
        sim.bus.send(1, "r2", "dispatcher", Kind.COMPLETE, "t1")
        sim.step()
        self.assertEqual(sim.dispatcher.records["t1"].status, TaskStatus.OFFERED)
        self.assertTrue(any(e["event"] == "ignored_message" for e in sim.trace.events))

    def test_rejection_republishes_unchanged_readiness(self):
        scenario = Scenario.from_dict(configuration())
        trace = Trace()
        bus = MessageBus({"dispatcher", "r1"}, trace)
        robot = Robot(scenario.robots[0], scenario, bus, trace, ChargingStation(1))
        robot.step(0)  # initial idle already announced
        task = {**scenario.tasks[0].to_dict(), "weight": 100}
        bus.send(0, "dispatcher", "r1", Kind.ASSIGN, "t1", task=task)
        bus.deliver(1)
        robot.step(1)
        kinds = [m.kind for m in bus.pending if m.sender == "r1"]
        self.assertEqual(kinds, [Kind.REJECT, Kind.STATUS])

    def test_charger_is_fifo_and_requests_are_idempotent(self):
        station = ChargingStation(1)
        self.assertTrue(station.request("a"))
        self.assertFalse(station.request("b"))
        self.assertFalse(station.request("b"))
        self.assertFalse(station.request("c"))
        self.assertEqual(list(station.queue), ["b", "c"])
        station.release("a")
        self.assertFalse(station.request("c"))
        self.assertTrue(station.request("b"))


class InputAndCliTests(unittest.TestCase):
    def test_invalid_scenarios(self):
        original = configuration()
        bad_cases = []
        for field, value in [("width", 0), ("reserve", -1), ("charge_rate", 0),
                             ("charger_slots", True), ("robots", [])]:
            data = copy.deepcopy(original)
            data[field] = value
            bad_cases.append(data)
        data = copy.deepcopy(original)
        data["tasks"][0]["pickup"] = [5, 0]
        bad_cases.append(data)
        data = copy.deepcopy(original)
        data["tasks"].append(data["tasks"][0])
        bad_cases.append(data)
        data = copy.deepcopy(original)
        data["robots"].append(data["robots"][0])
        bad_cases.append(data)
        data = copy.deepcopy(original)
        data["robots"][0]["id"] = "dispatcher"
        bad_cases.append(data)
        for data in bad_cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                Scenario.from_dict(data)

    def test_cli_runs_without_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-m", "warehouse", "--output", directory]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(directory) / "trace.jsonl").is_file())

    def test_cli_tick_limit_and_bad_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-m", "warehouse", "--max-ticks", "1",
                                     "--output", directory], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
        result = subprocess.run([sys.executable, "-m", "warehouse", "--scenario", "absent.json"],
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
