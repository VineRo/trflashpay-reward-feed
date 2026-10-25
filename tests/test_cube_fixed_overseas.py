"""Reviewed fixed overseas extension and its complete-source withdrawal contract."""
import copy
import json
import unittest
from pathlib import Path
from datetime import datetime, timezone
from pipeline import build_feed, collect, normalize_aem_model, supporting_proof
from assessment_profiles import validate_profile
from project_evidence import requires_project_evidence


class FixedOverseasTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[2]
        self.registry=[s for s in json.loads((Path(__file__).resolve().parents[1]/'sources.json').read_text()) if s['cardId']=='cathay_cube']
        primary=(root/'audit/c02_cube_overseas_20261024/cube.model.json').read_bytes()
        scheme=(root/'audit/c02_cube_overseas_20261024/cube-list.model.json').read_bytes()
        points=(root/'audit/c02_cube_point_terms_20261024/conditions.model.json').read_bytes()
        supports=self.registry[0]['supportingUrls']
        data=dict(zip(supports,[scheme,points]))
        self.evidence=collect(self.registry,downloader=lambda _:normalize_aem_model(primary),
                              proof_downloader=lambda url:supporting_proof(url,data[url]))

    def profile(self,evidence):
        return build_feed([],evidence,['cathay_cube'],datetime(2026,10,24,tzinfo=timezone.utc))['assessmentProfiles']['cathay_cube']

    @requires_project_evidence
    def test_reviewed_extension_keeps_domestic_rate_and_bounds(self):
        p=self.profile(self.evidence);validate_profile(p,'cathay_cube')
        self.assertEqual(p['calculation']['overseasPhysicalRate'],2.5)
        self.assertEqual(p['calculation']['rate'],1.2)
        self.assertEqual(p['calculation']['rounding'],'halfUp')
        self.assertEqual(p['calculation']['validFrom'],'2026-08-03T00:00:00+08:00')
        self.assertIn('withinCubeMonthlyLimit',p['calculation']['requiredConditionIds'])
        self.assertFalse(p['fullCardCoverage'])

    @requires_project_evidence
    def test_each_missing_changed_or_failed_document_withdraws_both_branches(self):
        for url in self.registry[0]['supportingUrls']:
            for value in [None,{'sha256':'0'*64},{'error':'test timeout'},
                          {**self.evidence['cathay_cube']['supportingSources'][url],'error':'stale digest'}]:
                with self.subTest(url=url,value=value):
                    evidence=copy.deepcopy(self.evidence)
                    if value is None:del evidence['cathay_cube']['supportingSources'][url]
                    else:evidence['cathay_cube']['supportingSources'][url]=value
                    self.assertIsNone(self.profile(evidence)['calculation'])
        evidence=copy.deepcopy(self.evidence);evidence['cathay_cube']['error']='primary unavailable'
        self.assertIsNone(self.profile(evidence)['calculation'])

    @requires_project_evidence
    def test_reject_invented_overseas_or_domestic_policy_and_foreign_keys(self):
        original=self.profile(self.evidence)
        for key,value in [('overseasPhysicalRate',v) for v in [None,2.8,'2.5',True,float('nan')]] + [('rate',2.5),('extraBranch',2.5)]:
            with self.subTest(key=key,value=value):
                p=copy.deepcopy(original);p['calculation'][key]=value
                with self.assertRaises(ValueError):validate_profile(p,'cathay_cube')

    @requires_project_evidence
    def test_legacy_domestic_only_schema_remains_readable_without_overseas(self):
        p=self.profile(self.evidence);del p['calculation']['overseasPhysicalRate']
        validate_profile(p,'cathay_cube')
        self.assertEqual(p['calculation']['rate'],1.2)
