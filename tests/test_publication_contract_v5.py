"""Fresh-checkout inputs retain real source/refusal checks, no private fallback."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation.upgrade import public_contract_fixtures_v5 as public
from evaluation.upgrade import narrative_continuation_v4 as v4
from evaluation.upgrade.narrative_suite.data import SuiteError


class PublicContractPackagingTests(unittest.TestCase):
    def setUp(self):
        self.scratch=public.ROOT/'work'; self.scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='packaging-v5-unit-',dir=self.scratch)
        self.base=Path(self.temp.name).resolve(); self.assertTrue(self.base.is_relative_to(self.scratch.resolve()))
    def tearDown(self):
        self.assertTrue(self.base.is_relative_to(self.scratch.resolve())); self.temp.cleanup()
    def test_projections_are_partial_and_preserve_source_hash_provenance(self):
        raw=public.read_json(public.ROOT/public.PROJECTIONS); values=public.projections()
        self.assertEqual(len(values),3); self.assertTrue(all(e['is_full_receipt'] is False for e in raw['entries']))
        self.assertIsNone(raw['semantic_quality']); self.assertEqual(raw['new_native_attempts'],0)
        self.assertFalse(values[1]['dispatch_authorized'])
    def test_changed_projection_provenance_refused(self):
        target=self.base/'source'
        public.write(target/'fixtures/upgrade/narrative-continuation-v4/partial-checkpoint.json',public.read_json(public.ROOT/v4.CHECKPOINT))
        value=public.read_json(public.ROOT/public.PROJECTIONS); value['entries'][0]['private_source_sha256']='0'*64
        public.write(target/public.PROJECTIONS,value)
        with self.assertRaises(SuiteError):public.projections(target)
    def test_original_test_archives_match_historical_seal_bytes(self):
        seal=public.read_json(public.ROOT/public.V4_SEAL)
        historic={e['path']:e['sha256'] for e in seal['files']+seal['preserved_files']}
        for name in ('test_upgrade_narrative_supplement_pure.py','test_upgrade_narrative_continuation_v4_pure.py'):
            path=public.ROOT/'evaluation/upgrade/freezes/publication-packaging-v5'/(name+'.original.txt')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),historic['tests/'+name])
    def test_fixture_reads_only_repo_sources_or_its_owned_temporary_inputs(self):
        real_open=Path.open
        forbidden=[public.ROOT/'work'/name for name in ('main-u2b2-1239','main-u2b2-supplement-v3','main-u2b2-continuation-v4')]
        def controlled(path,*args,**kwargs):
            resolved=path.resolve()
            self.assertTrue(resolved.is_relative_to(public.ROOT.resolve()))
            self.assertFalse(any(resolved.is_relative_to(p.resolve()) for p in forbidden))
            return real_open(path,*args,**kwargs)
        with patch.object(Path,'open',controlled),patch('sqlite3.connect',side_effect=AssertionError('SQL forbidden')) as sql,patch('subprocess.Popen',side_effect=AssertionError('child forbidden')) as child,patch('socket.socket',side_effect=AssertionError('socket forbidden')) as socket:
            root=public.build_contract_root(self.base/'checkout',include_v4=True)
            context=v4.Context(root,public.file_hash(root/v4.SEAL),root/public.POPULATION,'public-packaging-contract')
            self.assertTrue(context.verify()); self.assertFalse(context.out.exists())
            sql.assert_not_called(); child.assert_not_called(); socket.assert_not_called()
    def test_synthetic_graph_cannot_be_reported_as_native_or_SQL_database(self):
        root=public.build_contract_root(self.base/'checkout',include_v4=True)
        provenance=public.read_json(root/'SYNTHETIC_PROVENANCE.json')
        self.assertFalse(provenance['native_evidence']); self.assertEqual(provenance['actor_runs'],0)
        self.assertFalse((root/v4.PARENT/'s001/corpus.sqlite').read_bytes().startswith(b'SQLite format'))
        self.assertTrue(all((root.parent/e['path']).resolve().is_relative_to(root) for e in public.read_json(root/v4.CHECKPOINT)['main_references']))
    def test_synthetic_parent_drift_is_detected_by_unmodified_validator(self):
        root=public.build_contract_root(self.base/'checkout',include_v4=True)
        context=v4.Context(root,public.file_hash(root/v4.SEAL),root/public.POPULATION,'public-packaging-contract')
        self.assertTrue(context.verify())
        (root/v4.PARENT/'writer.lock').write_text('changed synthetic parent')
        with self.assertRaises(SuiteError):context.verify()
    def test_missing_local_projection_is_error_not_skip_or_private_fallback(self):
        with self.assertRaises(SuiteError):public.projections(self.base/'absent')


if __name__=='__main__':unittest.main()
