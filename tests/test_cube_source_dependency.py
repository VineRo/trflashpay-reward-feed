"""S2 failure/unknown-field changes must reach the published formula boundary."""
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pipeline import CUBE_SCHEME_URL, LIMIT, build_feed, collect, supporting_proof
import test_cube_model
from test_cube_scheme_inventory import model


class CubeSourceDependencyTests(unittest.TestCase):
    def setUp(self):
        self.data = json.dumps(model(), ensure_ascii=False).encode()
        self.proof = supporting_proof(CUBE_SCHEME_URL, self.data)
        self.text = test_cube_model.CubeModelTests.terms
        self.primary = hashlib.sha256(self.text.encode()).hexdigest()
        self.registry = [{'cardId': 'cathay_cube', 'url': 'https://www.cathay-cube.com.tw/card',
                          'supportingUrls': [CUBE_SCHEME_URL]}]
        self.now = datetime(2026, 10, 24, tzinfo=timezone.utc)

    def feed(self, evidence):
        with patch.dict('assessment_profiles.APPROVALS', {'cathay_cube': self.primary}), patch.dict(
                'assessment_profiles.SUPPORT_APPROVALS', {'cathay_cube': {CUBE_SCHEME_URL: self.proof['sha256']}}):
            return build_feed([], evidence, ['cathay_cube'], self.now)

    def evidence(self, proof=None):
        return collect(self.registry, downloader=lambda _: self.text,
                       proof_downloader=lambda _: self.proof if proof is None else proof)

    def test_same_complete_model_reordered_keys_keeps_formula_and_combined_digest(self):
        evidence = self.evidence()
        p = self.feed(evidence)['assessmentProfiles']['cathay_cube']
        self.assertEqual(p['calculation']['rate'], 1.2)
        self.assertNotEqual(p['sourceSha256'], self.primary)
        reordered = json.dumps(dict(reversed(list(model().items())))).encode()
        self.assertEqual(supporting_proof(CUBE_SCHEME_URL, reordered)['sha256'], self.proof['sha256'])

    def test_changed_authored_or_unknown_fields_revoke_formula_but_keep_limited_observation(self):
        for field in ['mainTitle', 'paragraphNoticeContent', 'newEligibilityField']:
            changed = copy.deepcopy(model())
            changed[':items']['scheme'][field] = '新的限制'
            proof = supporting_proof(CUBE_SCHEME_URL, json.dumps(changed).encode())
            feed = self.feed(self.evidence(proof))
            p = feed['assessmentProfiles']['cathay_cube']
            self.assertIsNone(p['calculation'])
            self.assertTrue(p['announcementObserved'])
            self.assertFalse(p['fullCardCoverage'])
            self.assertTrue(feed['rules'][0]['verificationRequiredReason'])

    def test_missing_unavailable_extra_or_error_with_old_digest_cannot_keep_formula(self):
        base = self.evidence()
        variants = []
        for supports in [{}, {CUBE_SCHEME_URL: {'error': 'timeout'}},
                         {CUBE_SCHEME_URL: {**self.proof, 'error': 'stale cached digest'}},
                         {CUBE_SCHEME_URL: self.proof, 'https://bank.example/new': self.proof}]:
            value = copy.deepcopy(base)
            value['cathay_cube']['supportingSources'] = supports
            variants.append(value)
        value = copy.deepcopy(base)
        value['cathay_cube']['error'] = 'primary unavailable'
        variants.append(value)
        for evidence in variants:
            self.assertIsNone(self.feed(evidence)['assessmentProfiles']['cathay_cube']['calculation'])

    def test_collect_records_schema_and_download_failures_then_feed_withdraws(self):
        for data in [b'{}', b'{"x":NaN}', b'<html>error</html>']:
            def download(_):
                return supporting_proof(CUBE_SCHEME_URL, data)
            evidence = collect(self.registry, downloader=lambda _: self.text, proof_downloader=download)
            self.assertIn('error', evidence['cathay_cube']['supportingSources'][CUBE_SCHEME_URL])
            self.assertIsNone(self.feed(evidence)['assessmentProfiles']['cathay_cube']['calculation'])

    def test_support_model_requires_exact_registered_url_and_bounded_valid_schema(self):
        for url, data in [(CUBE_SCHEME_URL + '?new=1', self.data),
                          (CUBE_SCHEME_URL.replace('www.cathay-cube.com.tw', 'invalid.example'), self.data),
                          (CUBE_SCHEME_URL, b'x' * (LIMIT + 1)),
                          (CUBE_SCHEME_URL, b'{"title":"x","title":"y"}')]:
            with self.assertRaises(ValueError):
                supporting_proof(url, data)

    def test_existing_pdf_proof_contract_is_preserved(self):
        data = b'%PDF-1.7\nfixture'
        proof = supporting_proof('https://bank.example/terms.pdf', data)
        self.assertEqual(proof, {'sha256': hashlib.sha256(data).hexdigest(),
                                 'format': 'pdf', 'byteCount': len(data)})
