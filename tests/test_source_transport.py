"""Exercise the actual transport boundary without contacting banks or publishing."""
import hashlib
import io
import json
import ssl
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from unittest.mock import patch

from pipeline import CUBE_SCHEME_URL, LIMIT, SameHostRedirect, build_feed, collect, fetch_proof, supporting_proof
import test_cube_model
from test_cube_scheme_inventory import model


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status
        self.read_limits = []

    def read(self, size=-1):
        self.read_limits.append(size)
        return super().read(size)


class SourceTransportTests(unittest.TestCase):
    def setUp(self):
        self.data = json.dumps(model(), ensure_ascii=False).encode()
        self.proof = supporting_proof(CUBE_SCHEME_URL, self.data)
        self.text = test_cube_model.CubeModelTests.terms
        self.sha = hashlib.sha256(self.text.encode()).hexdigest()
        self.registry = [{'cardId': 'cathay_cube', 'url': 'https://www.cathay-cube.com.tw/card',
                          'supportingUrls': [CUBE_SCHEME_URL]}]

    def feed(self, evidence):
        with patch.dict('assessment_profiles.APPROVALS', {'cathay_cube': self.sha}), patch.dict(
                'assessment_profiles.SUPPORT_APPROVALS', {'cathay_cube': {CUBE_SCHEME_URL: self.proof['sha256']}}):
            return build_feed([], evidence, ['cathay_cube'], datetime(2026, 10, 24, tzinfo=timezone.utc))

    def test_registered_model_uses_bounded_read_and_finite_transport_timeout(self):
        response = Response(self.data)
        with patch('pipeline.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = response
            result = fetch_proof(CUBE_SCHEME_URL)
            self.assertEqual(result, self.proof)
            self.assertEqual(response.read_limits, [LIMIT + 1])
            self.assertEqual(factory.return_value.open.call_args.kwargs['timeout'], 20)
            self.assertIsInstance(factory.call_args.args[0], SameHostRedirect)

    def test_http_failures_and_oversized_or_truncated_models_revoke_feed_formula(self):
        cases = [(self.data, 204), (self.data, 404), (self.data, 500),
                 (b'x' * (LIMIT + 1), 200), (self.data[:-12], 200),
                 (b'<html>upstream error</html>', 200)]
        for data, status in cases:
            with self.subTest(status=status, byteCount=len(data)), patch('pipeline.urllib.request.build_opener') as factory:
                factory.return_value.open.return_value = Response(data, status)
                evidence = collect(self.registry, downloader=lambda _: self.text)
                self.assertIn('error', evidence['cathay_cube']['supportingSources'][CUBE_SCHEME_URL])
                profile = self.feed(evidence)['assessmentProfiles']['cathay_cube']
                self.assertIsNone(profile['calculation'])
                self.assertTrue(profile['announcementObserved'])

    def test_network_timeout_certificate_or_read_failures_do_not_keep_old_formula(self):
        for error in [TimeoutError('test timeout'), urllib.error.URLError('test DNS failure'),
                      ssl.SSLCertVerificationError('test invalid certificate'), OSError('test interrupted read')]:
            for stage in ['open', 'read']:
                with self.subTest(error=type(error).__name__, stage=stage), patch('pipeline.urllib.request.build_opener') as factory:
                    if stage == 'open':
                        factory.return_value.open.side_effect = error
                    else:
                        response = Response(self.data)
                        response.read = lambda _: (_ for _ in ()).throw(error)
                        factory.return_value.open.return_value = response
                    evidence = collect(self.registry, downloader=lambda _: self.text)
                    proof = evidence['cathay_cube']['supportingSources'][CUBE_SCHEME_URL]
                    self.assertIn('error', proof)
                    self.assertNotIn('sha256', proof)
                    self.assertIsNone(self.feed(evidence)['assessmentProfiles']['cathay_cube']['calculation'])

    def test_invalid_source_urls_are_rejected_before_opening_transport(self):
        for url in [CUBE_SCHEME_URL.replace('https:', 'http:'),
                    CUBE_SCHEME_URL.replace('https://', 'https://fixture:fixture@'),
                    CUBE_SCHEME_URL + '#fragment']:
            with self.subTest(url=url), patch('pipeline.urllib.request.build_opener') as factory:
                with self.assertRaises(ValueError):
                    fetch_proof(url)
                factory.assert_not_called()

    def test_redirects_cannot_bypass_no_credentials_or_https_and_host_constraints(self):
        request = urllib.request.Request(CUBE_SCHEME_URL)
        for url in ['http://www.cathay-cube.com.tw/terms', 'https://invalid.example/terms',
                    'https://fixture:fixture@www.cathay-cube.com.tw/terms',
                    'https://fixture@www.cathay-cube.com.tw/terms']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                SameHostRedirect().redirect_request(request, None, 302, 'Found', {}, url)

    def test_same_host_https_redirect_preserves_registered_model_fingerprint_check(self):
        request = urllib.request.Request(CUBE_SCHEME_URL)
        redirected = SameHostRedirect().redirect_request(request, None, 302, 'Found', {},
                                                        'https://www.cathay-cube.com.tw/new-terms')
        self.assertEqual(redirected.full_url, 'https://www.cathay-cube.com.tw/new-terms')
        # A redirect does not approve replacement content returned under the original URL.
        changed = model()
        changed['newEligibilityField'] = 'new restriction'
        with patch('pipeline.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = Response(json.dumps(changed).encode())
            evidence = collect(self.registry, downloader=lambda _: self.text)
            self.assertIsNone(self.feed(evidence)['assessmentProfiles']['cathay_cube']['calculation'])

    def test_rejected_redirect_is_recorded_and_revokes_the_formula(self):
        with patch('pipeline.urllib.request.build_opener') as factory:
            def open_with_redirect(request, **kwargs):
                handler = factory.call_args.args[0]
                handler.redirect_request(request, None, 302, 'Found', {},
                    'https://fixture:fixture@www.cathay-cube.com.tw/terms')
                return Response(self.data)
            factory.return_value.open.side_effect = open_with_redirect
            evidence = collect(self.registry, downloader=lambda _: self.text)
            proof = evidence['cathay_cube']['supportingSources'][CUBE_SCHEME_URL]
            self.assertIn('error', proof)
            self.assertNotIn('sha256', proof)
            self.assertIsNone(self.feed(evidence)['assessmentProfiles']['cathay_cube']['calculation'])

    def test_valid_pdf_transport_contract_survives_model_support(self):
        data = b'%PDF-1.7\nfixture'
        with patch('pipeline.urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = Response(data)
            proof = fetch_proof('https://bank.example/terms.pdf')
            self.assertEqual(proof['format'], 'pdf')
            self.assertEqual(proof['sha256'], hashlib.sha256(data).hexdigest())
