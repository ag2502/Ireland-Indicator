"""Single entry point for every BusTrust job: `bustrust <command> [options]`.

Each package registers its commands in COMMANDS; imports are lazy so the API container
does not load LightGBM and the poller does not load FastAPI.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

# command name -> (module path, function name, help text)
COMMANDS: dict[str, tuple[str, str, str]] = {
    "init-db": ("common.db", "run_init", "create tables (and TimescaleDB hypertables)"),
    "load-schedule": ("ingest.static_loader", "run", "download, version and load static GTFS"),
    "poll": ("ingest.poller", "run", "poll the realtime feeds (NTA or simulated) every 60 s"),
    "weather": ("ingest.weather", "run", "fetch hourly Dublin weather from Open-Meteo"),
    "build-labels": ("pipeline.arrivals", "run", "reconstruct arrivals and no-show outcomes per day"),
    "features": ("pipeline.features", "run", "build training examples (Parquet) for both models"),
    "scores": ("pipeline.scores", "run", "route scores, heatmap and slow segments"),
    "checks": ("pipeline.checks", "run", "daily data-quality checks (exit 1 on failure)"),
    "stopwatch": ("pipeline.stopwatch", "run", "compare hand-timed arrivals with reconstructed labels"),
    "train": ("ml.train", "run", "train, evaluate and promote the arrival-range and no-show models"),
    "score": ("ml.score", "run", "score the live board every minute (after the poller)"),
    "accuracy": ("ml.accuracy", "run", "grade yesterday's live predictions against arrivals"),
    "simulate-backfill": ("ingest.simulator", "run_backfill", "replay simulated history into the DB"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bustrust",
        description="BusTrust jobs. Run `bustrust <command> --help` for a command's options.",
        epilog="commands:\n" + "\n".join(f"  {n:<16} {h}" for n, (_, _, h) in sorted(COMMANDS.items())),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("command", choices=sorted(COMMANDS), metavar="command")
    ns, rest = parser.parse_known_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    module, func, _ = COMMANDS[ns.command]
    result = getattr(importlib.import_module(module), func)(rest)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
