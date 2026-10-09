"""Run from a checkout with py -3.11 -m permissioned_research."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .agent import ResearchAgent
from .domain import PROTOCOL, ResearchError, ResearchRequest
from .providers import ScriptedResearchProvider
from .render import render
from .store import SCENARIOS, SCHEMA_VERSION, CorpusStore, load_fixture

BANNER = "OFFLINE SCRIPTED DEMONSTRATION - NO MODEL CALLED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research fictional enterprise sources under simulated user permissions.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Inspect runtime capabilities without network calls.")
    demo = commands.add_parser("demo", help="Run a bounded authored scenario with scripted decisions.")
    demo.add_argument("--scenario", choices=(*SCENARIOS, "revoked-resolution"), default="vendor-review")
    demo.add_argument("--principal", choices=("alex", "priya", "sam"), default="priya")
    demo.add_argument("--provider", choices=("scripted",), default="scripted")
    demo.add_argument("--state-dir", type=Path, default=Path(".local/demo"))
    demo.add_argument("--format", choices=("json", "markdown"), default="json")
    demo.add_argument("--request-key", help="Bind a stable local request key (1-64 letters/digits/underscore/hyphen); omitted keys are new.")
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        print(json.dumps({"error": "python_3_11_required"}), file=sys.stderr)
        return 2
    if args.command == "doctor":
        print(json.dumps({"version": __version__, "python": sys.version.split()[0],
                          "sqlite": sqlite3.sqlite_version, "schema": SCHEMA_VERSION, "protocol": PROTOCOL,
                          "dependencies": "standard_library_only", "network_used": False,
                          "live_model_executed": False}, indent=2))
        return 0
    store = None
    try:
        fixture = load_fixture("vendor-review" if args.scenario == "revoked-resolution" else args.scenario)
        args.state_dir.mkdir(parents=True, exist_ok=True)
        store = CorpusStore(args.state_dir / "corpus.sqlite3", fixture)
        request = ResearchRequest(args.principal, fixture["vendor"], fixture["as_of"])
        hooks = {}
        if args.scenario == "revoked-resolution":
            fired = False
            def revoke_after_read(actor):
                nonlocal fired
                observed = actor.observations[-1]
                if not fired and observed["action"] == "read" and "PROCUREMENT_ONLY_CEDAR" in observed["result"].get("body", ""):
                    fired = True
                    store.revoke_membership(request.principal, "procurement", "demo-revoke-procurement")
            hooks["after_tool"] = revoke_after_read
        agent = ResearchAgent(store, request, ScriptedResearchProvider(), hooks=hooks, request_key=args.request_key)
        print(BANNER, file=sys.stderr)
        result = agent.run()
        print(render(result, args.format))
        return 3 if result["outcome"] in {"failed", "cancelled"} else 0
    except ResearchError as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"error": "storage_error"}), file=sys.stderr)
        return 3
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
