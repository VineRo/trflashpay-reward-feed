"""Point terms parsing and mandatory production dependency withdrawal."""
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cube_point_terms import parse_point_terms
from pipeline import CUBE_POINT_TERMS_URL, CUBE_SCHEME_URL, LIMIT, build_feed, collect, fetch_proof, supporting_proof
from assessment_profiles import APPROVALS, SUPPORT_APPROVALS
from project_evidence import requires_project_evidence


def model():
    return {'title': '小樹點(信用卡) 累積辦法', ':items': {'credit': {
        'title': '小樹點(信用卡)累積辦法',
        'text': '<p>活動期間 2026/1/1~2026/12/31 參與資格 一般消費項目排除 其他經本行公告排除之項目 未盡事宜</p>'}}}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


class PointTermsTests(unittest.TestCase):
    def test_credit_terms_are_separate_from_debit_and_keep_evidence_pointer(self):
        value = model()
        value[':items']['debit'] = {'title': '簽帳金融卡點數累積辦法', 'text': '不同規則'}
        result = parse_point_terms(encoded(value))
        self.assertEqual(result['creditTerms']['path'], '/:items/credit/text')
        self.assertNotIn('不同規則', result['creditTerms']['text'])
        self.assertFalse(result['fullCardCoverage'])

    def test_complete_model_digest_detects_even_unparsed_fields(self):
        value = model()
        original = parse_point_terms(encoded(value))['modelSha256']
        self.assertEqual(parse_point_terms(encoded(dict(reversed(list(value.items())))))['modelSha256'], original)
        value['futureEligibility'] = 'unreviewed restriction'
        self.assertNotEqual(parse_point_terms(encoded(value))['modelSha256'], original)

    def test_reject_missing_duplicate_truncated_nonfinite_or_wrong_document(self):
        missing = model(); missing[':items']['credit']['text'] = '一般消費項目排除'
        duplicate = model(); duplicate[':items']['copy'] = duplicate[':items']['credit']
        wrong = model(); wrong['title'] = '其他辦法'
        wrong_type = model(); wrong_type[':items']['credit']['text'] = []
        nested = model(); node = nested
        for _ in range(45):
            node['nested'] = {}; node = node['nested']
        for data in [encoded(missing), encoded(duplicate), encoded(wrong), encoded(wrong_type),
                     encoded(nested), b'{"x":NaN}', b'{"title":"x","title":"y"}',
                     b'<html>login</html>', encoded(model())[:-4], b'x'*(LIMIT+1)]:
            with self.subTest(data=data[:50]), self.assertRaises(ValueError):
                parse_point_terms(data)

    def test_only_exact_registered_url_accepts_point_model(self):
        data = encoded(model())
        self.assertEqual(supporting_proof(CUBE_POINT_TERMS_URL, data)['format'], 'cubePointTermsModel')
        for url in [CUBE_POINT_TERMS_URL+'?new=1', CUBE_POINT_TERMS_URL+'#fragment',
                    CUBE_POINT_TERMS_URL.replace('www.cathay-cube.com.tw','invalid.example')]:
            with self.assertRaises(ValueError):
                supporting_proof(url, data)

    def test_production_registry_has_both_mandatory_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        source = next(s for s in json.loads((root/'sources.json').read_text()) if s['cardId']=='cathay_cube')
        self.assertEqual(set(source['supportingUrls']), {CUBE_SCHEME_URL, CUBE_POINT_TERMS_URL})
        self.assertEqual(set(SUPPORT_APPROVALS['cathay_cube']), set(source['supportingUrls']))

    @requires_project_evidence
    def test_reviewed_snapshot_and_individual_failure_cases_reach_feed_boundary(self):
        root = Path(__file__).resolve().parents[2]
        from pipeline import normalize_aem_model
        primary = (root/'audit/c02_cube_overseas_20261024/cube.model.json').read_bytes()
        scheme = (root/'audit/c02_cube_overseas_20261024/cube-list.model.json').read_bytes()
        terms = (root/'audit/c02_cube_point_terms_20261024/conditions.model.json').read_bytes()
        text = normalize_aem_model(primary)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), APPROVALS['cathay_cube'])
        proofs = {CUBE_SCHEME_URL: supporting_proof(CUBE_SCHEME_URL, scheme),
                  CUBE_POINT_TERMS_URL: supporting_proof(CUBE_POINT_TERMS_URL, terms)}
        registry = [s for s in json.loads((Path(__file__).resolve().parents[1]/'sources.json').read_text()) if s['cardId']=='cathay_cube']
        evidence = collect(registry, downloader=lambda _: text, proof_downloader=proofs.__getitem__)
        def profile(value):
            return build_feed([], value, ['cathay_cube'], datetime(2026,10,24,tzinfo=timezone.utc))['assessmentProfiles']['cathay_cube']
        original = profile(evidence)
        self.assertEqual(original['calculation']['rate'], 1.2)
        changed = json.loads(terms); changed['unparsedNewRestriction'] = 'new restriction'
        changed_proof = supporting_proof(CUBE_POINT_TERMS_URL, encoded(changed))
        for value in [None, {'error': 'timeout'}, changed_proof,
                      {**proofs[CUBE_POINT_TERMS_URL], 'error': 'stale digest'}]:
            current = copy.deepcopy(evidence)
            support = current['cathay_cube']['supportingSources']
            if value is None: del support[CUBE_POINT_TERMS_URL]
            else: support[CUBE_POINT_TERMS_URL] = value
            p = profile(current)
            self.assertIsNone(p['calculation'])
            self.assertTrue(p['announcementObserved'])
            if value is None or not value.get('error'):
                self.assertNotEqual(p['sourceSha256'], original['sourceSha256'])
        with patch('pipeline.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect = TimeoutError('test point terms unavailable')
            current = collect(registry, downloader=lambda _: text,
                              proof_downloader=lambda url: proofs[url] if url == CUBE_SCHEME_URL else fetch_proof(url))
        self.assertIn('error', current['cathay_cube']['supportingSources'][CUBE_POINT_TERMS_URL])
        self.assertIsNone(profile(current)['calculation'])


class PointTermsTransportTests(unittest.TestCase):
    """Exercise point-terms fetch failures with both required dependencies present."""
    def setUp(self):
        import test_cube_model
        from test_cube_scheme_inventory import model as scheme_model
        self.data = encoded(model())
        self.text = test_cube_model.CubeModelTests.terms
        self.scheme = supporting_proof(CUBE_SCHEME_URL, encoded(scheme_model()))
        self.point = supporting_proof(CUBE_POINT_TERMS_URL, self.data)
        self.registry = [{'cardId': 'cathay_cube', 'url': 'https://www.cathay-cube.com.tw/card',
                          'supportingUrls': [CUBE_SCHEME_URL, CUBE_POINT_TERMS_URL]}]

    def collect(self):
        return collect(self.registry, downloader=lambda _: self.text,
                       proof_downloader=lambda url: self.scheme if url == CUBE_SCHEME_URL else fetch_proof(url))

    def profile(self, evidence):
        with patch.dict('assessment_profiles.APPROVALS', {'cathay_cube': hashlib.sha256(self.text.encode()).hexdigest()}), patch.dict(
                'assessment_profiles.SUPPORT_APPROVALS', {'cathay_cube': {
                    CUBE_SCHEME_URL: self.scheme['sha256'], CUBE_POINT_TERMS_URL: self.point['sha256']}}):
            return build_feed([], evidence, ['cathay_cube'], datetime(2026,10,24,tzinfo=timezone.utc))['assessmentProfiles']['cathay_cube']

    def test_point_terms_transport_success_preserves_formula_and_bounds_reads(self):
        from test_source_transport import Response
        from pipeline import SameHostRedirect
        response = Response(self.data)
        with patch('pipeline.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = response
            evidence = self.collect()
            self.assertEqual(factory.return_value.open.call_args.kwargs['timeout'], 20)
            self.assertIsInstance(factory.call_args.args[0], SameHostRedirect)
        self.assertEqual(response.read_limits, [LIMIT+1])
        self.assertEqual(evidence['cathay_cube']['supportingSources'][CUBE_POINT_TERMS_URL]['format'], 'cubePointTermsModel')
        self.assertEqual(self.profile(evidence)['calculation']['rate'], 1.2)

    def test_point_terms_http_truncation_login_and_debit_replacement_withdraw_formula(self):
        from test_source_transport import Response
        debit_only = model(); debit_only[':items']['credit']['title'] = '簽帳金融卡點數累積辦法'
        missing_end = model(); missing_end[':items']['credit']['text'] = '一般消費項目排除'
        wrong_type = model(); wrong_type[':items']['credit']['text'] = {'text': '一般消費項目排除'}
        cases = [(self.data, status) for status in (204, 301, 404, 429, 500)] + [
            (b'x'*(LIMIT+1), 200), (self.data[:-4], 200), (b'<html>login required</html>', 200),
            (encoded(debit_only), 200), (encoded(missing_end), 200), (encoded(wrong_type), 200)]
        for data, status in cases:
            with self.subTest(status=status, bytes=len(data)), patch('pipeline.urllib.request.build_opener') as factory:
                factory.return_value.open.return_value = Response(data, status)
                evidence = self.collect()
                proof = evidence['cathay_cube']['supportingSources'][CUBE_POINT_TERMS_URL]
                self.assertIn('error', proof)
                self.assertNotIn('sha256', proof)
                p = self.profile(evidence)
                self.assertIsNone(p['calculation'])
                self.assertTrue(p['announcementObserved'])

    def test_point_terms_dns_tls_timeout_and_interrupted_read_withdraw_formula(self):
        import ssl
        from urllib.error import URLError
        from test_source_transport import Response
        for error in [TimeoutError('test timeout'), URLError('test DNS failure'),
                      ssl.SSLCertVerificationError('test invalid certificate'), OSError('test interrupted read')]:
            for stage in ('open', 'read'):
                with self.subTest(error=type(error).__name__, stage=stage), patch('pipeline.urllib.request.build_opener') as factory:
                    if stage == 'open':
                        factory.return_value.open.side_effect = error
                    else:
                        response = Response(self.data)
                        response.read = lambda _: (_ for _ in ()).throw(error)
                        factory.return_value.open.return_value = response
                    evidence = self.collect()
                    proof = evidence['cathay_cube']['supportingSources'][CUBE_POINT_TERMS_URL]
                    self.assertIn('error', proof)
                    self.assertNotIn('sha256', proof)
                    self.assertIsNone(self.profile(evidence)['calculation'])
