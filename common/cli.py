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
COMMANDS: dict[str, tuple[str, str, str]] = {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bustrust", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, _, help_text) in sorted(COMMANDS.items()):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    module, func, _ = COMMANDS[ns.command]
    result = getattr(importlib.import_module(module), func)(ns.args)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
