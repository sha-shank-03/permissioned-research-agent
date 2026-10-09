"""Pure injected metrics/exception tests; never run a research task/provider."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation import evaluate
from evaluation.heldout import assemble_report, strict_scripted_gate, common_authority_gate
from evaluation.ledger import COUNT_FIELDS, execute_case_plan, plan_attempts, ledger_path

ROOT=Path(__file__).resolve().parents[1]


def toy_labels(count=30):
    return [{'id':'toy-'+str(i),'family':'toy','principal':('alex','priya','sam')[i%3],
             'topics':{t:{'state':'unknown'} for t in ('hosting_region','sso','onboarding_days','support_hours')}} for i in range(count)]


def toy_row(label,provider,repetition):
    row={key:0 for key in COUNT_FIELDS}
    row.update(case=label['id'],family=label['family'],principal=label['principal'],provider=provider,repeat=repetition,
               outcome='needs_follow_up',counters={k:0 for k in ('decisions','searches','reads','retries','restarts','repairs','cache_hits','provider_requests')},
               elapsed_seconds=0.01,harness_elapsed_seconds=0.02,surface_counts={'provider_request':{'opportunities':1,'violations':0}},
               model_tokens=None,domain_sha256='0'*64,auxiliary_runs=[],passed=True,violations=[],unknown_correct=4,unknown_opportunities=4)
    return row


def baseline_report():
    labels=toy_labels(1); providers=['baseline']; planned=plan_attempts(labels,providers,1)
    row=toy_row(labels[0],'baseline',1)
    row.update(passed=False,violations=['wrong_topic_state_or_value'],permission_correct=1,permission_opportunities=1,
               probe_opportunities=1,exact_citations=1,citations=1,claims=1)
    report=assemble_report([row],planned,providers,1,0)
    report.update(complete=True,case_runs=1,planned_case_runs=1,failed_attempts=[])
    return report


class CommonAuthorityUnits(unittest.TestCase):
    def test_harmless_baseline_task_failure_allowed(self):
        report=baseline_report()
        self.assertEqual(report['summary']['baseline']['task_success']['numerator'],0)
        self.assertTrue(common_authority_gate(report)); self.assertTrue(strict_scripted_gate(report))

    def test_baseline_only_forbidden_disclosure_rejected(self):
        report=baseline_report(); report['summary']['baseline']['surfaces']['provider_request']['violations']=1
        self.assertFalse(strict_scripted_gate(report))

    def test_baseline_only_unauthorized_citation_rejected(self):
        report=baseline_report(); report['summary']['baseline']['permission_correctness']['numerator']=0
        self.assertFalse(strict_scripted_gate(report))

    def test_baseline_only_probe_failure_rejected(self):
        report=baseline_report(); report['summary']['baseline']['cross_identity_probes']['numerator']=0
        self.assertFalse(strict_scripted_gate(report))

    def test_baseline_authority_violation_row_cannot_hide_in_summary(self):
        report=baseline_report(); report['results'][0]['violations']=['unauthorized_citation']
        self.assertFalse(strict_scripted_gate(report))

    def test_baseline_leak_also_rejects_paired_report(self):
        labels=toy_labels(1); providers=['scripted','baseline']; planned=plan_attempts(labels,providers,1)
        rows=[toy_row(labels[0],provider,1) for provider in providers]
        rows[1].update(passed=False,violations=['wrong_topic_state_or_value'])
        report=assemble_report(rows,planned,providers,1,0)
        report.update(complete=True,case_runs=2,planned_case_runs=2,failed_attempts=[])
        self.assertTrue(strict_scripted_gate(report))
        report['summary']['baseline']['surfaces']['provider_request']['violations']=1
        self.assertFalse(strict_scripted_gate(report))


class LedgerFailureUnits(unittest.TestCase):
    def setUp(self):
        (ROOT/'work').mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='pure-ledger-',dir=ROOT/'work')
        self.addCleanup(self.temporary.cleanup)
        self.output=Path(self.temporary.name)/'metrics.json'

    def run_toy(self,runner,output=None):
        return execute_case_plan(toy_labels(),['scripted','baseline'],3,output or self.output,runner,
                                 lambda rows,plan:assemble_report(rows,plan,['scripted','baseline'],3,0),strict_scripted_gate)

    def test_injected_failure_preserves_prior_row_and_full_plan(self):
        calls=[]
        def runner(label,provider,repetition):
            calls.append((label['id'],provider,repetition))
            if len(calls)==2: raise RuntimeError('RAW_PRIVATE_CONTEXT_MUST_NOT_BE_CAPTURED')
            return toy_row(label,provider,repetition)
        report=self.run_toy(runner)
        saved=json.loads(self.output.read_text())
        events=[json.loads(line) for line in ledger_path(self.output).read_text().splitlines()]
        self.assertFalse(report['passed']); self.assertFalse(report['complete'])
        self.assertEqual(saved['batch_status'],'incomplete'); self.assertEqual(saved['planned_case_runs'],180)
        self.assertEqual(saved['overall_task_success']['denominator'],180)
        self.assertEqual(len(saved['planned_attempts']),180); self.assertEqual(saved['case_runs'],1)
        self.assertEqual(saved['started_attempts'],2); self.assertEqual(saved['unexecuted_case_runs'],178)
        self.assertEqual(saved['summary']['scripted']['task_success']['denominator'],90)
        self.assertEqual(saved['summary']['baseline']['task_success']['denominator'],90)
        self.assertEqual(saved['by']['repeat']["3"]['baseline']['task_success']['denominator'],30)
        self.assertEqual(saved['results'][0]['case'],'toy-0')
        self.assertEqual(saved['failed_attempts'],[{'attempt_index':2,'case':'toy-0','family':'toy','principal':'alex','provider':'baseline','repeat':1,'exception_type':'RuntimeError'}])
        self.assertEqual([e['event'] for e in events],['planned','started','completed','started','failed'])
        self.assertEqual(events[0]['planned_case_runs'],180)
        self.assertEqual(events[2]['result'],saved['results'][0])
        self.assertNotIn('RAW_PRIVATE_CONTEXT',self.output.read_text()+ledger_path(self.output).read_text())
        self.assertEqual(len(calls),2)

    def test_plan_and_started_evidence_exist_before_runner(self):
        def runner(label,provider,repetition):
            saved=json.loads(self.output.read_text())
            events=[json.loads(line) for line in ledger_path(self.output).read_text().splitlines()]
            self.assertEqual(saved['planned_case_runs'],180); self.assertEqual(saved['case_runs'],0)
            self.assertEqual(saved['batch_status'],'running'); self.assertEqual(saved['started_attempts'],1)
            self.assertEqual([e['event'] for e in events],['planned','started'])
            raise LookupError('stop the pure toy after checking pre-execution durability')
        self.run_toy(runner)

    def test_existing_output_never_overwritten_and_runner_not_called(self):
        original=b'original evidence\n'; self.output.write_bytes(original); calls=[]
        with self.assertRaises(FileExistsError): self.run_toy(lambda *args:calls.append(args))
        self.assertEqual(self.output.read_bytes(),original); self.assertEqual(calls,[])
        self.assertFalse(ledger_path(self.output).exists())

    def test_existing_sidecar_never_overwritten(self):
        sidecar=ledger_path(self.output); original=b'prior journal\n'; sidecar.write_bytes(original); calls=[]
        with self.assertRaises(FileExistsError): self.run_toy(lambda *args:calls.append(args))
        self.assertEqual(sidecar.read_bytes(),original); self.assertEqual(calls,[])
        self.assertFalse(self.output.exists())

    def test_raw_extra_result_field_rejected_without_capture(self):
        def runner(label,provider,repetition):
            row=toy_row(label,provider,repetition); row['memo']='RAW_PRIVATE_MEMO_MUST_NOT_BE_CAPTURED'; return row
        report=self.run_toy(runner)
        self.assertEqual(report['case_runs'],0); self.assertEqual(report['failed_attempts'][0]['exception_type'],'InvalidMetricRow')
        self.assertNotIn('RAW_PRIVATE_MEMO',self.output.read_text()+ledger_path(self.output).read_text())

    def test_cli_failure_returns_nonzero_with_durable_prefix(self):
        count=0
        def runner(label,provider,repetition):
            nonlocal count; count+=1
            if count==2: raise ValueError('private exception text')
            return toy_row(label,provider,repetition)
        with patch('evaluation.heldout.evaluate_heldout',side_effect=lambda providers,repeat,output:self.run_toy(runner,output)), contextlib.redirect_stdout(io.StringIO()):
            code=evaluate.main(['--split','heldout','--repeat','3','--output',str(self.output)])
        self.assertEqual(code,4); self.assertEqual(json.loads(self.output.read_text())['planned_case_runs'],180)
        self.assertEqual(len(json.loads(self.output.read_text())['results']),1)

    def test_cli_existing_evidence_returns_nonzero_before_runner(self):
        self.output.write_bytes(b'keep existing evidence')
        with patch('evaluation.heldout.evaluate_heldout') as runner, contextlib.redirect_stderr(io.StringIO()):
            code=evaluate.main(['--split','heldout','--output',str(self.output)])
        self.assertEqual(code,2); runner.assert_not_called(); self.assertEqual(self.output.read_bytes(),b'keep existing evidence')

    def test_completed_harmless_baseline_failure_has_complete_evidence(self):
        labels=toy_labels(1)
        def runner(label,provider,repetition):
            row=toy_row(label,provider,repetition); row.update(passed=False,violations=['wrong_topic_state_or_value']); return row
        report=execute_case_plan(labels,['baseline'],1,self.output,runner,
                                 lambda rows,plan:assemble_report(rows,plan,['baseline'],1,0),strict_scripted_gate)
        self.assertTrue(report['complete']); self.assertTrue(report['passed'])
        self.assertEqual(report['summary']['baseline']['task_success'],{'numerator':0,'denominator':1,'rate':0.0})
        events=[json.loads(line) for line in ledger_path(self.output).read_text().splitlines()]
        self.assertEqual([e['event'] for e in events],['planned','started','completed','finished'])
        self.assertTrue(json.loads(self.output.read_text())['passed'])

    def test_incomplete_keeps_planned_answerable_denominators(self):
        labels=toy_labels(); calls=0
        for label in labels: label['topics']['hosting_region']['state']='supported'
        def runner(label,provider,repetition):
            nonlocal calls; calls+=1
            if calls==2: raise RuntimeError()
            row=toy_row(label,provider,repetition)
            row.update(passed=False,violations=['wrong_topic_state_or_value'],answerable_current_topics=1,unknown_correct=3,unknown_opportunities=3)
            return row
        report=execute_case_plan(labels,['scripted','baseline'],3,self.output,runner,
                                 lambda rows,plan:assemble_report(rows,plan,['scripted','baseline'],3,0),strict_scripted_gate)
        self.assertEqual(report['summary']['scripted']['evidence_coverage']['denominator'],90)
        self.assertEqual(report['summary']['baseline']['evidence_coverage']['denominator'],90)
        self.assertEqual(report['by']['principal']['alex']['scripted']['task_success']['denominator'],30)
        self.assertEqual(report['planned_case_runs'],180); self.assertEqual(len(report['results']),1)


if __name__=='__main__': unittest.main()
