"""V2-S6: platform (L2) programs, Chrome-rendered sources and section fingerprints.

Every catalog here is TEST DATA ONLY. No test starts a browser: the Chrome
renderer is exercised through an injected command runner.
"""
import copy
import hashlib
import subprocess
import unittest
from datetime import datetime, timezone

import pipeline
from pipeline import build_feed, collect, render_chrome, section_text
from rules_v2 import build_programs, check_sources, validate_catalog

NOW = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
PAGE = 'https://wallet.example/campaign'
START, END = '活動內容', '注意事項結束'
BODY = ('頁首導覽 活動內容 活動期間 2026/06/01 - 12/31 掃描任一 TWQR 立牌付款 享綠點 10% 回饋 '
        '每人每月回饋上限 200 點 注意事項結束 頁尾 已結束 7/2 輪播')
SECTION = BODY[BODY.index(START):BODY.index(END) + len(END)]
SECTION_SHA = hashlib.sha256(SECTION.encode()).hexdigest()


def platform_catalog():
    """TEST DATA ONLY: one priced platform program and one uncovered-only program."""
    return {
        'rewardUnits': [{'id': 'green', 'label': '綠點', 'twdValue': '1', 'cashEquivalent': 'restricted'}],
        'merchantSets': [],
        'programs': [
            {'id': 'wallet_platform', 'issuer': 'wallet', 'name': '測試錢包', 'rewardUnit': 'green',
             'selectors': [],
             'rules': [{'id': 'w.scan', 'layer': 'L2', 'kind': 'bonus', 'group': 'w.main',
                        'period': {'from': '2026-06-01T00:00:00+08:00', 'to': '2026-12-31T23:59:59.999999+08:00'},
                        'when': {'all': [{'route': {'platform': ['ipassMoney'], 'funding': ['storedBalance']}},
                                         {'qrStandard': 'twqr'}]},
                        'rate': {'type': 'fixed', 'percent': '10'}, 'includesBase': False, 'unit': 'green',
                        'rounding': {'mode': 'halfUp', 'basis': 'monthlyAggregate', 'scope': 'account'},
                        'caps': [{'measure': 'reward', 'amount': '200', 'per': 'calendarMonth',
                                  'scope': 'account', 'overflowRule': None}],
                        'combinable': {'default': 'unknown'}, 'evidenceRefs': ['w.page#rate', 'w.page#cap']}],
             'exclusions': [],
             'evidence': [{'id': 'w.page', 'sourceCard': 'wallet_x', 'url': PAGE, 'role': 'primary', 'format': 'html',
                           'approvedSha256': SECTION_SHA, 'section': {'start': START, 'end': END},
                           'anchors': {'rate': {'pointer': '/text', 'contains': '享綠點 10% 回饋'},
                                       'cap': {'pointer': '/text', 'contains': '每人每月回饋上限 200 點'}},
                           'approval': {'by': 'test', 'at': '2026-10-25T12:00:00+08:00'}}]},
            {'id': 'other_platform', 'issuer': 'other', 'name': '另一錢包', 'rewardUnit': 'green',
             'selectors': [], 'exclusions': [], 'evidence': [],
             'rules': [{'id': 'o.base', 'layer': 'L2', 'kind': 'base', 'group': 'o.main', 'status': 'uncovered',
                        'notes': '官方未見平台常態回饋比例'}]},
        ],
    }


REGISTRY = [{'cardId': 'wallet_x', 'url': PAGE, 'render': 'chrome'}]


def evidence(text=BODY, error=None):
    item = {'url': PAGE, 'supportingSources': {}}
    if error:
        item['error'] = error
    else:
        item.update(text=text, sha256=hashlib.sha256(text.encode()).hexdigest())
    return {'wallet_x': item}


class SchemaTests(unittest.TestCase):
    def test_platform_catalog_is_valid(self):
        validate_catalog(platform_catalog())

    def test_qr_standard_account_scope_and_section_are_checked(self):
        for name, mutate in {
            'unknown qr standard': lambda c: c['programs'][0]['rules'][0]['when']['all'].__setitem__(1, {'qrStandard': 'barcode'}),
            'unknown scope': lambda c: c['programs'][0]['rules'][0]['caps'][0].__setitem__('scope', 'person'),
            'section without end': lambda c: c['programs'][0]['evidence'][0]['section'].pop('end'),
            'section on pdf': lambda c: c['programs'][0]['evidence'][0].__setitem__('format', 'pdf'),
            'priced program without evidence': lambda c: c['programs'][0].__setitem__('evidence', []),
        }.items():
            with self.subTest(name):
                catalog = platform_catalog()
                mutate(catalog)
                with self.assertRaises(ValueError) as error:
                    validate_catalog(catalog)
                print(f'{name}: {error.exception}')

    def test_uncovered_only_program_needs_no_evidence(self):
        catalog = platform_catalog()
        catalog['programs'] = [catalog['programs'][1]]
        validate_catalog(catalog)


class SectionFingerprintTests(unittest.TestCase):
    def test_section_text_is_bounded_by_unique_markers(self):
        self.assertEqual(section_text(BODY, START, END), SECTION)
        with self.assertRaises(ValueError):
            section_text(BODY.replace(START, ''), START, END)
        with self.assertRaises(ValueError):
            section_text(BODY + ' ' + START, START, END)
        with self.assertRaises(ValueError):
            section_text(BODY.replace(END, ''), START, END)

    def test_changes_outside_the_section_keep_the_rule(self):
        moved = BODY.replace('已結束 7/2 輪播', '已結束 7/9 輪播 新橫幅')
        published, pending = build_programs(platform_catalog(), evidence(moved), NOW)
        self.assertEqual(pending, {})
        page = published[0]['evidence'][0]
        self.assertEqual(page['observedSha256'], SECTION_SHA)
        self.assertEqual([r['id'] for r in published[0]['rules']], ['w.scan'])

    def test_changes_inside_the_section_or_broken_markers_withdraw(self):
        for name, text in {
            'rate changed': BODY.replace('10% 回饋', '5% 回饋'),
            'period changed (anchors intact)': BODY.replace('2026/06/01', '2026/07/01'),
            'start missing': BODY.replace(START, '活動說明'),
            'end duplicated': BODY + ' ' + END,
        }.items():
            with self.subTest(name):
                published, pending = build_programs(platform_catalog(), evidence(text), NOW)
                self.assertIn('wallet_platform/w.scan', pending)
                self.assertFalse(published[0]['rules'])
                print(f'{name}: {pending["wallet_platform/w.scan"]}')
        _, pending = build_programs(platform_catalog(), evidence(BODY.replace('2026/06/01', '2026/07/01')), NOW)
        self.assertIn('source changed since approval', pending['wallet_platform/w.scan'])

    def test_anchor_outside_the_approved_section_withdraws(self):
        catalog = platform_catalog()
        catalog['programs'][0]['evidence'][0]['anchors']['rate']['contains'] = '頁首導覽'
        published, pending = build_programs(catalog, evidence(), NOW)
        self.assertIn('anchor rate not in approved section', pending['wallet_platform/w.scan'])

    def test_render_failure_withdraws_only_this_platform(self):
        published, pending = build_programs(platform_catalog(), evidence(error='render timeout'), NOW)
        self.assertEqual(set(pending), {'wallet_platform/w.scan'})
        other = next(p for p in published if p['id'] == 'other_platform')
        self.assertEqual([r['id'] for r in other['rules']], ['o.base'])


class RenderAndCollectTests(unittest.TestCase):
    def test_collect_renders_only_marked_sources(self):
        calls = []
        result = collect(REGISTRY + [{'cardId': 'plain', 'url': 'https://plain.example/a'}],
                         downloader=lambda url: calls.append(('fetch', url)) or 'plain ' * 50,
                         proof_downloader=lambda url: {},
                         renderer=lambda url: calls.append(('render', url)) or BODY * 3)
        self.assertEqual(calls, [('render', PAGE), ('fetch', 'https://plain.example/a')])
        self.assertEqual(result['wallet_x']['text'], BODY * 3)

    def test_unknown_render_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            collect([{'cardId': 'x', 'url': PAGE, 'render': 'firefox'}], downloader=str, proof_downloader=dict,
                    renderer=str)

    def test_renderer_errors_become_source_errors(self):
        def broken(url):
            raise ValueError('render timeout')
        result = collect(REGISTRY, downloader=str, proof_downloader=dict, renderer=broken)
        self.assertEqual(result['wallet_x']['error'], 'render timeout')

    def test_render_chrome_runs_headless_dump_dom_with_limits(self):
        seen = {}

        def runner(cmd, **kwargs):
            seen['cmd'], seen['kwargs'] = cmd, kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout=f'<html><body><p>{BODY * 3}</p><script>x()</script></body></html>'.encode(), stderr=b'')
        text = render_chrome(PAGE, chrome='/usr/bin/google-chrome', runner=runner)
        self.assertIn('享綠點 10% 回饋', text)
        self.assertNotIn('x()', text)
        cmd = seen['cmd']
        self.assertEqual(cmd[0], '/usr/bin/google-chrome')
        self.assertIn('--headless=new', cmd)
        self.assertIn('--dump-dom', cmd)
        self.assertEqual(cmd[-1], PAGE)
        self.assertTrue(any(a.startswith('--user-data-dir=') for a in cmd))
        self.assertTrue(any(a.startswith('--virtual-time-budget=') for a in cmd))
        self.assertLessEqual(seen['kwargs']['timeout'], 90)

    def test_render_chrome_rejects_unsafe_urls_failures_and_thin_pages(self):
        ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=b'<p>' + ('x' * 300).encode() + b'</p>', stderr=b'')
        for url in ('http://wallet.example/a', 'https://user:pw@wallet.example/a', 'file:///etc/passwd'):
            with self.subTest(url), self.assertRaises(ValueError):
                render_chrome(url, chrome='chrome', runner=ok)
        with self.assertRaises(ValueError):
            render_chrome(PAGE, chrome='chrome', runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b'', b'crash'))
        with self.assertRaises(ValueError):
            render_chrome(PAGE, chrome='chrome', runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b'<p>short</p>', b''))

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw['timeout'])
        with self.assertRaises(ValueError):
            render_chrome(PAGE, chrome='chrome', runner=slow)

    def test_missing_chrome_is_a_source_error_not_a_crash(self):
        with self.assertRaises(ValueError):
            render_chrome(PAGE, chrome=None, runner=None, finder=lambda: None)


class FeedTests(unittest.TestCase):
    def test_platform_evidence_feeds_programs_without_touching_v1(self):
        catalog = platform_catalog()
        check_sources(catalog, REGISTRY)
        base = build_feed([], {}, ['cathay_cube'], NOW)
        feed = build_feed([], {}, ['cathay_cube'], NOW, programs=catalog, platform_evidence=evidence())
        self.assertEqual(feed['programPending'], {})
        self.assertEqual({p['id'] for p in feed['programs']}, {'wallet_platform', 'other_platform'})
        self.assertEqual({k: v for k, v in feed.items() if k in base and k not in ('generatedAt', 'expiresAt', 'revision')},
                         {k: v for k, v in base.items() if k not in ('generatedAt', 'expiresAt', 'revision')})

    def test_platform_source_ids_may_not_shadow_card_sources(self):
        with self.assertRaises(ValueError):
            build_feed([], {'wallet_x': {'url': 'https://bank.example'}}, ['cathay_cube'], NOW,
                       programs=platform_catalog(), platform_evidence=evidence())

    def test_pipeline_finds_registered_platform_sources(self):
        self.assertTrue(hasattr(pipeline, 'load_platform_registry'))


class RealPlatformDataTests(unittest.TestCase):
    """The published platform programs (not test data)."""

    def setUp(self):
        from pathlib import Path
        from rules_v2 import load_catalog
        self.catalog = load_catalog(Path(__file__).resolve().parents[1] / 'programs')

    def test_unmodeled_platforms_state_a_reason_and_claim_nothing(self):
        ids = {'pxplus_platform', 'line_pay_platform', 'taiwan_pay_platform', 'easy_wallet_platform',
               'icash_pay_platform', 'pi_wallet_platform', 'esun_wallet_platform', 'plus_pay_platform'}
        programs = {p['id']: p for p in self.catalog['programs'] if p['id'] in ids}
        self.assertEqual(set(programs), ids)
        for pid, program in programs.items():
            with self.subTest(pid):
                self.assertEqual(program['evidence'], [])
                self.assertEqual(program['exclusions'], [])
                self.assertTrue(program['rules'])
                for rule in program['rules']:
                    self.assertEqual(rule['status'], 'uncovered')
                    self.assertEqual(rule['layer'], 'L2')
                    self.assertGreater(len(rule['notes']), 10)
                unit = next(u for u in self.catalog['rewardUnits'] if u['id'] == program['rewardUnit'])
                self.assertIsNone(unit['twdValue'], 'no unverified point value')

    def test_unmodeled_platforms_publish_without_sources(self):
        published, pending = build_programs(self.catalog, {}, NOW)
        ids = {p['id'] for p in published}
        self.assertTrue({'easy_wallet_platform', 'line_pay_platform'} <= ids)
        # Only the priced iPASS and JKO rules need their (here absent) sources; reason-only platforms never do.
        self.assertEqual({k.split('/')[0] for k in pending if k.split('/')[0].endswith('_platform')},
                         {'ipass_money_platform', 'jko_pay_platform'})


from project_evidence import requires_project_evidence  # noqa: E402
from test_rules_v2_cube import platform_registry  # noqa: E402


class RealIpassTests(unittest.TestCase):
    """iPASS MONEY platform program against the saved Chrome-rendered official text."""

    def setUp(self):
        from pathlib import Path
        from rules_v2 import load_catalog
        self.catalog = load_catalog(Path(__file__).resolve().parents[1] / 'programs')
        self.program = next(p for p in self.catalog['programs'] if p['id'] == 'ipass_money_platform')
        self.registry = [s for s in platform_registry() if s['cardId'] == 'ipass_money_platform']

    def rule(self):
        return next(r for r in self.program['rules'] if r['id'] == 'ipass.twqr.scan')

    def test_source_is_rendered_with_chrome_and_registered(self):
        from test_rules_v2_cube import registry
        self.assertEqual(self.registry, [{'cardId': 'ipass_money_platform', 'render': 'chrome',
                                          'url': 'https://www.i-pass.com.tw/Preferential/Detail/GMZTIMYKIEY7I64rUuoF'}])
        validate_catalog(self.catalog)
        check_sources(self.catalog, registry() + platform_registry())

    def test_rule_matches_the_official_terms(self):
        r = self.rule()
        self.assertEqual(r['layer'], 'L2')
        self.assertEqual(r['rate'], {'type': 'fixed', 'percent': '10'})
        self.assertEqual(r['period'], {'from': '2026-06-01T00:00:00+08:00', 'to': '2026-12-31T23:59:59.999999+08:00'})
        self.assertEqual(r['rounding'], {'mode': 'halfUp', 'basis': 'monthlyAggregate', 'scope': 'account'})
        self.assertEqual(r['caps'], [{'measure': 'reward', 'amount': '200', 'per': 'calendarMonth', 'scope': 'account',
                                      'overflowRule': None}])
        route = r['when']['all'][0]['route']
        self.assertEqual(route, {'platform': ['ipassMoney'], 'funding': ['storedBalance', 'creditCard']},
                         'official: 帳戶餘額與綁定信用卡 (not linked bank accounts)')
        self.assertIn({'qrStandard': 'twqr'}, r['when']['all'])
        spend = [c['spendType'] for c in r['when']['all'] if 'spendType' in c]
        excluded_types = {'utility', 'tax', 'government', 'fee', 'tuition'}
        self.assertEqual(spend, [['general', *sorted(excluded_types, key=['utility', 'tax', 'government', 'fee', 'tuition'].index)]],
                         'general spend plus the bill types that reach ipass.ex.payments; nothing else')
        self.assertEqual({q['id'] for q in r['requires']}, {'ipass.greenpoints.joined', 'ipass.campaign.quotaOpen'})
        self.assertFalse(r['includesBase'])
        self.assertEqual(r['combinable'], {'default': 'unknown'})
        excluded = {x['id']: x for x in self.program['exclusions']}
        self.assertEqual(set(excluded['ipass.ex.payments']['when']['spendType']), {'utility', 'tax', 'government', 'fee', 'tuition'})
        for named in ('停車費', '信用卡費', '菸品', '數位券', '服務費', '手續費'):
            self.assertIn(named, excluded['ipass.ex.items']['when']['issuerDecision'], named)
        self.assertIn('ipass.terms#excluded', excluded['ipass.ex.items']['evidenceRefs'])
        unit = next(u for u in self.catalog['rewardUnits'] if u['id'] == 'ipass_green_points')
        self.assertEqual((unit['twdValue'], unit['cashEquivalent']), ('1', 'restricted'))

    @requires_project_evidence
    def test_approved_sections_and_anchors_match_the_saved_render(self):
        from test_rules_v2_cube import IPASS_SNAPSHOT
        text = IPASS_SNAPSHOT.read_text()
        for item in self.program['evidence']:
            with self.subTest(item['id']):
                section = section_text(text, item['section']['start'], item['section']['end'])
                self.assertEqual(hashlib.sha256(section.encode()).hexdigest(), item['approvedSha256'])
                for name, anchor in item['anchors'].items():
                    self.assertIn(anchor['contains'], section, name)
        self.assertNotIn('綁定聯邦信用卡', section_text(text, '消費享10%回饋！', '活動總限量3,000萬點'),
                         'the rotating bank carousel is outside every approved section')

    @requires_project_evidence
    def test_publishes_from_the_saved_render_and_withdraws_on_change(self):
        from test_rules_v2_cube import IPASS_SNAPSHOT, reviewed_evidence, reviewed_platform_evidence
        cards = reviewed_evidence()
        ok = build_feed([], cards, ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                        platform_evidence=reviewed_platform_evidence())
        self.assertEqual(ok['programPending'], {})
        ipass = next(p for p in ok['programs'] if p['id'] == 'ipass_money_platform')
        self.assertIn('ipass.twqr.scan', {r['id'] for r in ipass['rules']})
        others = {p['id']: p for p in ok['programs'] if p['id'] != 'ipass_money_platform'}
        text = IPASS_SNAPSHOT.read_text()
        url = self.registry[0]['url']
        for name, changed in {
            'rate changed': text.replace('享一卡通綠點 10% 回饋', '享一卡通綠點 5% 回饋'),
            'cap changed': text.replace('每人每月回饋上限200點', '每人每月回饋上限100點'),
            'terms changed': text.replace('小數點後四捨五入。 如 A 用戶', '小數點後無條件捨去。 如 A 用戶'),
            'carousel only': text.replace('綁定聯邦信用卡', '綁定新銀行信用卡'),
        }.items():
            with self.subTest(name):
                evidence = collect(self.registry, downloader=str, proof_downloader=dict, renderer=lambda u: changed)
                feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                                  platform_evidence={**reviewed_platform_evidence(), **evidence})
                withdrawn = 'ipass_money_platform/ipass.twqr.scan' in feed['programPending']
                self.assertEqual(withdrawn, name != 'carousel only', feed['programPending'])
                self.assertEqual({p['id']: p for p in feed['programs'] if p['id'] != 'ipass_money_platform'}, others)
        def fail(u):
            raise ValueError('Official source render timed out')
        feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                          platform_evidence={**reviewed_platform_evidence(),
                                             **collect(self.registry, downloader=str, proof_downloader=dict, renderer=fail)})
        self.assertIn('ipass_money_platform/ipass.twqr.scan', feed['programPending'])
        self.assertTrue(url)


if __name__ == '__main__':
    unittest.main()
