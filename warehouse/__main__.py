import argparse
import json
from pathlib import Path
import sys
from .model import Scenario
from .simulation import Simulation


def main(argv=None):
    parser = argparse.ArgumentParser(description="ЛР1: многоагентный склад, Python 3.11+")
    parser.add_argument("--scenario", type=Path, default=Path(__file__).resolve().parents[1] / "scenarios" / "baseline.json")
    parser.add_argument("--max-ticks", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--step", action="store_true", help="Пауза после каждого такта; q — выход")
    parser.add_argument("--verbose", action="store_true", help="Печатать состояния после каждого такта")
    args = parser.parse_args(argv)
    try:
        simulation = Simulation(Scenario.load(args.scenario))

        def display(sim):
            if args.step or args.verbose:
                print(f"\nt={sim.tick - 1}: " + " | ".join(
                    f"{r.spec.id}: {r.state}, pos={r.position}, E={r.battery}, task={r.task.id if r.task else '-'}"
                    for r in sim.robots))
            if args.step and not sim.finished and input("Enter — следующий такт, q — сохранить и выйти: ").lower() == "q":
                raise KeyboardInterrupt

        try:
            simulation.run(args.max_ticks, display)
        except (KeyboardInterrupt, EOFError):
            print("\nДемонстрация остановлена; текущее состояние сохранено.")
        simulation.save(args.output)
        print(json.dumps(simulation.summary(), ensure_ascii=False, indent=2))
        print(f"\nРезультаты: {args.output.resolve()}")
        return 0 if simulation.finished else 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
