"""Authored evaluation entry point; labels/verifier remain outside actor tools."""

import argparse
import json
import sys
from pathlib import Path

from permissioned_research.agent import ResearchAgent
from permissioned_research.domain import ResearchRequest
from permissioned_research.providers import ScriptedResearchProvider
from permissioned_research.store import CorpusStore, load_fixture
from .baseline import BaselineProvider
from .inspect import SurfaceInspector


def expected_quote(topic, value):
    # Independently authored semantic labels, not production extraction/guard.
    if topic == "hosting_region":
        return f"Hosting region: {value}."
    if topic == "sso":
        return "SSO support: yes." if value is True else "SSO support: no."
    if topic == "onboarding_days":
        return f"Onboarding lead time: {value} business days."
    return f"Priority support response: {value} hours."


def score(result, label, fixture, inspector):
    violations = []
    valid_citations, citations = 0, 0
    coverage, answerable = 0, 0
    memo = result.get("memo")
    if memo is None or result["outcome"] != label["expected_outcome"]:
        violations.append("wrong_outcome")
    records = {t["topic"]: t for t in memo["topics"]} if memo else {}
    snapshots = {s["object_id"]: s for s in fixture["sources"]}
    for topic, expected in label["topics"].items():
        actual = records.get(topic)
        if expected["state"] == "supported":
            answerable += 1
        actual_values = {(type(c["value"]).__name__, str(c["value"])) for c in actual["claims"]} if actual else set()
        expected_values = {(type(v).__name__, str(v)) for v in expected["values"]}
        if actual is None or actual["state"] != expected["state"] or actual_values != expected_values:
            violations.append("wrong_topic_state_or_value")
        elif expected["state"] == "supported":
            coverage += 1
        for claim in actual["claims"] if actual else []:
            citations += 1
            cite = claim["citation"]
            source = snapshots.get(cite["source_id"])
            quote = expected_quote(topic, claim["value"])
            if (source is None or cite["source_id"] not in label["allowed_sources"] or
                    cite["revision"] != source["revision"] or cite["connector"] != source["connector"] or
                    type(cite["start"]) is not int or type(cite["end"]) is not int or
                    not 0 <= cite["start"] < cite["end"] <= len(source["body"]) or
                    source["body"][cite["start"]:cite["end"]] != quote or cite["quote"] != quote):
                violations.append("unsupported_citation")
            else:
                valid_citations += 1
    inspector.check_audit()
    if inspector.violations:
        violations.append("surface_leakage")
    return {"passed": not violations, "violations": violations,
            "citation_correct": valid_citations, "citations": citations,
            "covered_current_topics": coverage, "answerable_current_topics": answerable,
            "surface_opportunities": len(inspector.events), "surface_violations": len(inspector.violations)}


def evaluate_dev(providers, repeat):
    labels = json.loads((Path(__file__).parent / "labels" / "dev.json").read_text())
    results = []
    for repetition in range(repeat):
        for provider_name in providers:
            for label in labels["cases"]:
                fixture = load_fixture(label["scenario"])
                store = CorpusStore(":memory:", fixture)
                try:
                    inspector = SurfaceInspector(store, fixture)
                    provider = ScriptedResearchProvider() if provider_name == "scripted" else BaselineProvider()
                    actor = ResearchAgent(store, ResearchRequest(label["principal"], fixture["vendor"], fixture["as_of"]), provider, observer=inspector)
                    result = actor.run()
                    # Resolver outputs are inspected too; there is no evaluator
                    # or gold label access in the actor's registered tools.
                    if result["memo"]:
                        for topic in result["memo"]["topics"]:
                            for claim in topic["claims"]:
                                actor.tools.resolve_citation(claim["citation"])
                    scored = score(result, label, fixture, inspector)
                    results.append({"case": label["id"], "provider": provider_name, "repeat": repetition + 1,
                                    "outcome": result["outcome"], "counters": result["counters"], **scored})
                finally:
                    store.close()
    summary = {}
    for provider in providers:
        rows = [r for r in results if r["provider"] == provider]
        summary[provider] = {"passed": sum(r["passed"] for r in rows), "total": len(rows),
                             "citation_correct": sum(r["citation_correct"] for r in rows),
                             "citations": sum(r["citations"] for r in rows),
                             "covered_current_topics": sum(r["covered_current_topics"] for r in rows),
                             "answerable_current_topics": sum(r["answerable_current_topics"] for r in rows),
                             "surface_violations": sum(r["surface_violations"] for r in rows)}
    return {"split": "dev", "authored_cases": 12, "actor_held_out": False, "evidence_kind": "offline_scripted",
            "live_model_executed": False, "summary": summary, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score frozen authored cases with offline policies only.")
    parser.add_argument("--split", choices=("dev","heldout"), default="dev")
    parser.add_argument("--verify-corruptions", action="store_true")
    parser.add_argument("--verify-freeze", action="store_true")
    parser.add_argument("--providers", default="scripted,baseline")
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--output", type=Path, default=Path(".local/m2-evaluation.json"))
    args = parser.parse_args(argv)
    providers = args.providers.split(",")
    if not providers or len(set(providers)) != len(providers) or any(p not in {"scripted", "baseline"} for p in providers):
        return 2
    if sys.version_info[:2] != (3, 11):
        return 2
    if args.verify_freeze:
        from .suite import verify_freezes, validate_rubrics
        print(json.dumps({'frozen_files_checked':verify_freezes(),**validate_rubrics()}))
        return 0
    from .ledger import ensure_new_output, write_new_report
    try:
        ensure_new_output(args.output,with_ledger=args.split=='heldout' and not args.verify_corruptions)
    except FileExistsError:
        print('evidence_already_exists',file=sys.stderr)
        return 2
    if args.verify_corruptions:
        from .controls import verify_controls
        report=verify_controls()
    elif args.split=='heldout':
        from .heldout import evaluate_heldout
        try:
            report=evaluate_heldout(providers,args.repeat,args.output)
        except FileExistsError:
            print('evidence_already_exists',file=sys.stderr)
            return 2
    else:
        report = evaluate_dev(providers, args.repeat)
    # Metrics/enums only; no raw contexts, responses, source bodies or memos.
    if args.split!='heldout' or args.verify_corruptions:
        try:
            write_new_report(args.output,report)
        except FileExistsError:
            print('evidence_already_exists',file=sys.stderr)
            return 2
    print(json.dumps(report["summary"], indent=2))
    if args.verify_corruptions: return 0 if report['passed'] else 4
    if args.split=='heldout':
        from .heldout import strict_scripted_gate
        return 0 if strict_scripted_gate(report) else 4
    scripted = report["summary"].get("scripted")
    return 4 if scripted and scripted["passed"] != scripted["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
