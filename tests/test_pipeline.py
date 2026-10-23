import copy
import hashlib
import unittest
from datetime import datetime, timezone
from pipeline import collect, compile_rules, normalize, build_feed
from validator import validate


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.rule = {
            'cardId': 'test', 'cardName': 'Test card', 'bankName': 'Test bank',
            'cardType': 'creditCard', 'supportedPlatforms': ['linePay'],
            'defaultRewardRate': 1, 'promoRewardRate': 2,
            'promoDescription': 'Fixture only', 'badgeText': 'Fixture',
            'schemes': None, 'merchantRates': None, 'requiresAccount': False,
            'accountTierRate': None, 'bonusAllowedPlatforms': None,
            'activeDaysOfWeek': None, 'verificationRequiredReason': None,
            'conditionTags': [], 'officialUrl': 'https://bank.example/terms',
            'validUntil': '2026-12-31T23:59:59+08:00',
        }
        digest = hashlib.sha256(b'fixture').hexdigest()
        self.approved = [{'rule': self.rule, 'sourceSha256': digest}]
        self.evidence = {'test': {'url': self.rule['officialUrl'], 'sha256': digest}}
        self.now = datetime(2026, 10, 23, tzinfo=timezone.utc)

    def test_unreviewed_catalog_is_explicitly_pending(self):
        feed = build_feed([], {}, ['a', 'b'], self.now)
        self.assertEqual(feed['rules'], [])
        self.assertEqual(set(feed['pending']), {'a', 'b'})

    def test_changed_source_revokes_previously_approved_rate(self):
        feed = build_feed(self.approved, {'test': {'error': 'changed'}}, ['test'], self.now)
        self.assertEqual(feed['rules'], [])
        self.assertIn('test', feed['pending'])

    def test_unrelated_failure_does_not_erase_valid_reviewed_rule(self):
        feed = build_feed(self.approved, self.evidence, ['test', 'other'], self.now)
        self.assertEqual(len(feed['rules']), 1)
        self.assertIn('other', feed['pending'])

    def test_publish_exact_reviewed_source(self):
        self.assertEqual(compile_rules(self.approved, self.evidence, self.now), [self.rule])

    def test_changed_or_failed_source_cannot_publish(self):
        for source in ({}, {'error': 'timeout'}, {'url': self.rule['officialUrl'], 'sha256': 'changed'}):
            with self.assertRaises(ValueError):
                compile_rules(self.approved, {'test': source}, self.now)

    def test_no_silent_initial_publication(self):
        with self.assertRaises(ValueError):
            compile_rules([], {}, self.now)

    def test_expiry_and_ambiguous_date(self):
        for value in ('2026-01-01T00:00:00Z', '2026-12-31', '2026-02-30T00:00:00Z'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.rule['validUntil'] = value
                compile_rules(self.approved, self.evidence, self.now)

    def test_invalid_rates(self):
        for value in (True, '3', float('nan'), float('inf'), -1, 31):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.rule['promoRewardRate'] = value
                validate(self.rule)

    def test_conditions_and_routes_cannot_be_missing(self):
        for field, value in [('supportedPlatforms', ['typo']), ('conditionTags', 'registered'), ('requiresAccount', 'true')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                rule = copy.deepcopy(self.rule)
                rule[field] = value
                validate(rule)

    def test_duplicate_card_id(self):
        with self.assertRaises(ValueError):
            compile_rules(self.approved * 2, self.evidence, self.now)

    def test_shared_official_url_is_downloaded_once_per_snapshot(self):
        calls = []
        def download(url):
            calls.append(url)
            return 'one consistent announcement'
        result = collect([{'cardId': 'credit', 'url': 'https://bank.example/terms'},
                          {'cardId': 'debit', 'url': 'https://bank.example/terms'}], download)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['credit']['sha256'], result['debit']['sha256'])

    def test_collect_failure_is_explicit(self):
        def fail(url):
            raise TimeoutError('timeout')
        self.assertIn('error', collect([{'cardId': 'test', 'url': self.rule['officialUrl']}], fail)['test'])

    def test_normalize_excludes_scripts(self):
        self.assertEqual(normalize('<p>回饋 2%</p><script>3%</script><style>4%</style>'), '回饋 2%')

    def test_cap_requires_cycle_basis_and_pool(self):
        self.rule['merchantRates'] = [{'ruleId': 'a', 'ratePercentage': 2, 'merchantKeywords': ['Test'], 'capAmount': 100}]
        with self.assertRaises(ValueError):
            validate(self.rule)
