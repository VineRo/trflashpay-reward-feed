"""Schema v2 programs: strict validation, evidence binding and per-rule withdrawal."""
import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from rules_v2 import build_programs, check_sources, load_catalog, resolve_pointer, validate_catalog

NOW = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
SHA_A, SHA_B = 'a' * 64, 'b' * 64
PRIMARY = 'https://bank.example/card.model.json'
SUPPORT = 'https://bank.example/terms.model.json'


def minimal_catalog():
    """Smallest catalog exercising every section of the v2 format."""
    return {
        'rewardUnits': [{'id': 'points', 'label': '點數', 'twdValue': '1', 'cashEquivalent': 'restricted'}],
        'merchantSets': [{'id': 'conv', 'label': '便利商店', 'match': {'brands': ['7-ELEVEN']},
                          'matchConfidence': 'issuerList', 'openEnded': True}],
        'programs': [{
            'id': 'bank_card', 'issuer': 'bank', 'name': '測試卡', 'rewardUnit': 'points',
            'selectors': [{'id': 'plan', 'kind': 'plan', 'source': 'userDeclared', 'history': True,
                           'values': ['fixed', 'other'],
                           'effective': {'rule': 'sameDayMidnight', 'basis': 'authorizationDate'}},
                          {'id': 'level', 'kind': 'tier', 'source': 'userDeclared', 'history': True,
                           'values': ['L1', 'L2'], 'effective': {'rule': 'nextMonthFirstDay'}}],
            'rules': [
                {'id': 'r1', 'layer': 'L1', 'kind': 'base', 'group': 'main',
                 'period': {'from': '2026-08-03T00:00:00+08:00', 'to': '2026-12-31T23:59:59.999999+08:00'},
                 'when': {'all': [{'selector': {'plan': 'fixed'}}, {'country': 'TW'}, {'currency': 'TWD'},
                                  {'route': {'platform': ['linePay', 'direct'], 'funding': ['creditCard']}}]},
                 'rate': {'type': 'fixed', 'percent': '1.2'}, 'includesBase': True, 'unit': 'points',
                 'rounding': {'mode': 'halfUp', 'basis': 'perTransaction'},
                 'caps': [{'measure': 'spend', 'amount': 'creditLimit+500000', 'per': 'calendarMonth',
                           'scope': 'primaryAndSupplementary', 'overflowRule': None}],
                 'combinable': {'default': 'unknown'}, 'evidenceRefs': ['e1#rate']},
                {'id': 'r2', 'layer': 'L1', 'kind': 'bonus', 'group': 'main',
                 'period': {'from': '2026-01-01T00:00:00+08:00', 'to': '2026-12-31T23:59:59.999999+08:00'},
                 'when': {'any': [{'not': {'country': 'TW'}}, {'merchantIn': 'conv'}]},
                 'rate': {'type': 'bySelector', 'selector': 'level', 'table': {'L1': '2', 'L2': '3'}},
                 'includesBase': True, 'unit': 'points', 'rounding': {'mode': 'halfUp', 'basis': 'perTransaction'},
                 'combinable': {'default': 'unknown'}, 'evidenceRefs': ['e2#terms']},
                {'id': 'r3', 'layer': 'L1', 'kind': 'bonus', 'group': 'coupon', 'status': 'uncovered',
                 'notes': '優惠券尚未建模'},
            ],
            'exclusions': [{'id': 'x1', 'appliesTo': ['r1'], 'when': {'merchantIn': 'conv'},
                            'effect': 'zero', 'evidenceRefs': ['e1#rate']}],
            'evidence': [
                {'id': 'e1', 'sourceCard': 'bank_card', 'url': PRIMARY, 'role': 'primary', 'format': 'aemModel',
                 'approvedSha256': SHA_A, 'anchors': {'rate': {'pointer': '/a/0', 'contains': '1.2%'}},
                 'approval': {'by': 'reviewer', 'at': '2026-10-24T15:00:00+08:00'}},
                {'id': 'e2', 'sourceCard': 'bank_card', 'url': SUPPORT, 'role': 'supporting', 'format': 'aemModel',
                 'approvedSha256': SHA_B, 'anchors': {'terms': {'pointer': '/b', 'contains': '條款'}},
                 'approval': {'by': 'reviewer', 'at': '2026-10-24T15:00:00+08:00'}},
            ],
        }],
    }


def valid_evidence():
    return {'bank_card': {'url': PRIMARY, 'text': 'x', 'sha256': SHA_A,
                          'supportingSources': {SUPPORT: {'url': SUPPORT, 'sha256': SHA_B}}}}


def program(catalog):
    return catalog['programs'][0]


def rule(catalog, rid):
    return next(r for r in program(catalog)['rules'] if r['id'] == rid)


class ValidatorTests(unittest.TestCase):
    def test_minimal_catalog_is_valid(self):
        validate_catalog(minimal_catalog())

    def test_by_selectors_table_with_flags_and_wildcards_is_valid(self):
        catalog = minimal_catalog()
        program(catalog)['selectors'].append({'id': 'autopay', 'kind': 'flag', 'source': 'userDeclared'})
        rule(catalog, 'r2')['rate'] = {'type': 'bySelectors', 'selectors': ['level', 'autopay'],
                                       'table': {'L1|false': '2', 'L1|true': '5', 'L*|true': '6'}}
        rule(catalog, 'r1')['requires'] = [{'id': 'reg', 'label': '登錄', 'per': 'calendarMonth',
                                            'deadline': 'monthLastDay', 'quota': 'unknown',
                                            'effectiveFrom': 'claimDay', 'source': 'userDeclared'}]
        program(catalog)['selectors'][1]['derivation'] = {'L2': '本人帳戶自動扣繳'}
        validate_catalog(catalog)

    def test_millisecond_and_microsecond_fractions_are_valid(self):
        for value in ('2026-12-31T23:59:59.999+08:00', '2026-12-31T23:59:59.999999+08:00'):
            with self.subTest(value):
                catalog = minimal_catalog()
                rule(catalog, 'r1')['period']['to'] = value
                validate_catalog(catalog)

    def test_rejects_invalid_shapes_and_dangling_references(self):
        cases = {
            'unknown top-level key': lambda c: c.update(extra=1),
            'unknown program key': lambda c: program(c).update(extra=1),
            'unknown rule key': lambda c: rule(c, 'r1').update(extra=1),
            'missing rule field': lambda c: rule(c, 'r1').pop('rounding'),
            'unknown unit': lambda c: rule(c, 'r1').update(unit='cash'),
            'unknown program unit': lambda c: program(c).update(rewardUnit='cash'),
            'unknown merchant set': lambda c: program(c)['exclusions'][0].update(when={'merchantIn': 'nope'}),
            'unknown selector': lambda c: rule(c, 'r1')['when']['all'].__setitem__(0, {'selector': {'nope': 'x'}}),
            'selector value not allowed': lambda c: rule(c, 'r1')['when']['all'].__setitem__(0, {'selector': {'plan': 'gold'}}),
            'unknown platform': lambda c: rule(c, 'r1')['when']['all'][3]['route'].update(platform=['cashApp']),
            'unknown funding': lambda c: rule(c, 'r1')['when']['all'][3]['route'].update(funding=['crypto']),
            'unknown leaf': lambda c: rule(c, 'r1')['when']['all'].append({'weather': 'sunny'}),
            'leaf with two keys': lambda c: rule(c, 'r1')['when']['all'].append({'country': 'TW', 'currency': 'TWD'}),
            'empty all': lambda c: rule(c, 'r1').update(when={'all': []}),
            'too deep': lambda c: rule(c, 'r1').update(when=nested_not(20)),
            'bad country': lambda c: rule(c, 'r1')['when']['all'].__setitem__(1, {'country': 'Taiwan'}),
            'float percent': lambda c: rule(c, 'r1')['rate'].update(percent=1.2),
            'zero percent': lambda c: rule(c, 'r1')['rate'].update(percent='0'),
            'over 100 percent': lambda c: rule(c, 'r1')['rate'].update(percent='101'),
            'text percent': lambda c: rule(c, 'r1')['rate'].update(percent='1.2%'),
            'table key not a selector value': lambda c: rule(c, 'r2')['rate']['table'].update(L9='4'),
            'table selector unknown': lambda c: rule(c, 'r2')['rate'].update(selector='nope'),
            'naive datetime': lambda c: rule(c, 'r1')['period'].update({'from': '2026-08-03T00:00:00'}),
            'date only': lambda c: rule(c, 'r1')['period'].update({'from': '2026-08-03'}),
            'reversed period': lambda c: rule(c, 'r1')['period'].update({'from': '2027-01-01T00:00:00+08:00'}),
            'bad layer': lambda c: rule(c, 'r1').update(layer='L4'),
            'non-bool includesBase': lambda c: rule(c, 'r1').update(includesBase='yes'),
            'bad rounding mode': lambda c: rule(c, 'r1')['rounding'].update(mode='bankers'),
            'bad rounding basis': lambda c: rule(c, 'r1')['rounding'].update(basis='yearly'),
            'bad cap amount': lambda c: rule(c, 'r1')['caps'][0].update(amount='lots'),
            'unknown overflow rule': lambda c: rule(c, 'r1')['caps'][0].update(overflowRule='nope'),
            'bad combinable': lambda c: rule(c, 'r1').update(combinable={'default': 'maybe'}),
            'combinable unknown group': lambda c: rule(c, 'r1').update(combinable={'with': ['nope']}),
            'no evidence refs': lambda c: rule(c, 'r1').update(evidenceRefs=[]),
            'unknown evidence': lambda c: rule(c, 'r1').update(evidenceRefs=['e9#rate']),
            'unknown anchor': lambda c: rule(c, 'r1').update(evidenceRefs=['e1#nope']),
            'ref without anchor': lambda c: rule(c, 'r1').update(evidenceRefs=['e1']),
            'duplicate rule id': lambda c: program(c)['rules'].append(copy.deepcopy(rule(c, 'r1'))),
            'rule id reused by exclusion': lambda c: program(c)['exclusions'][0].update(id='r1'),
            'uncovered with rate': lambda c: rule(c, 'r3').update(rate={'type': 'fixed', 'percent': '1'}),
            'uncovered without notes': lambda c: rule(c, 'r3').pop('notes'),
            'exclusion matches nothing': lambda c: program(c)['exclusions'][0].update(appliesTo=['zz*']),
            'fallback to unknown rule': lambda c: program(c)['exclusions'][0].update(effect='fallback', to='nope'),
            'zero effect with target': lambda c: program(c)['exclusions'][0].update(to='r2'),
            'exclusion without evidence': lambda c: program(c)['exclusions'][0].update(evidenceRefs=[]),
            'unit bad twdValue': lambda c: c['rewardUnits'][0].update(twdValue='one'),
            'unit bad cashEquivalent': lambda c: c['rewardUnits'][0].update(cashEquivalent='sometimes'),
            'duplicate unit': lambda c: c['rewardUnits'].append(dict(c['rewardUnits'][0])),
            'merchant set bad confidence': lambda c: c['merchantSets'][0].update(matchConfidence='guess'),
            'merchant set empty match': lambda c: c['merchantSets'][0].update(match={}),
            'evidence not https': lambda c: program(c)['evidence'][0].update(url='http://bank.example/x'),
            'evidence bad sha': lambda c: program(c)['evidence'][0].update(approvedSha256='A' * 64),
            'evidence bad role': lambda c: program(c)['evidence'][0].update(role='hint'),
            'evidence pointer not absolute': lambda c: program(c)['evidence'][0]['anchors']['rate'].update(pointer='a/0'),
            'evidence anchor without text': lambda c: program(c)['evidence'][0]['anchors']['rate'].update(contains=' '),
            'evidence missing approval': lambda c: program(c)['evidence'][0].pop('approval'),
            'selector bad kind': lambda c: program(c)['selectors'][0].update(kind='mood'),
            'selector duplicate values': lambda c: program(c)['selectors'][0].update(values=['fixed', 'fixed']),
            'program without rules': lambda c: program(c).update(rules=[], exclusions=[]),
            'Z suffix datetime': lambda c: rule(c, 'r1')['period'].update({'from': '2026-08-03T00:00:00Z'}),
            'hour-only datetime': lambda c: rule(c, 'r1')['period'].update({'from': '2026-08-03T00+08:00'}),
            'basic-format datetime': lambda c: rule(c, 'r1')['period'].update({'from': '20260803T000000+0800'}),
            'one-digit fraction': lambda c: rule(c, 'r1')['period'].update({'to': '2026-12-31T23:59:59.9+08:00'}),
            'two-digit fraction': lambda c: rule(c, 'r1')['period'].update({'to': '2026-12-31T23:59:59.12+08:00'}),
            'four-digit fraction': lambda c: rule(c, 'r1')['period'].update({'to': '2026-12-31T23:59:59.1234+08:00'}),
            'requires bad per': lambda c: rule(c, 'r1').update(requires=[{'id': 'reg', 'label': '登錄', 'per': 'calenderMonth'}]),
            'requires nested deadline': lambda c: rule(c, 'r1').update(requires=[{'id': 'reg', 'label': '登錄', 'deadline': {'a': [1]}}]),
            'requires bad quota': lambda c: rule(c, 'r1').update(requires=[{'id': 'reg', 'label': '登錄', 'quota': 'lots'}]),
            'requires bad source': lambda c: rule(c, 'r1').update(requires=[{'id': 'reg', 'label': '登錄', 'source': 'rumour'}]),
            'selector bad effective rule': lambda c: program(c)['selectors'][0].update(effective={'rule': 'whenever'}),
            'selector derivation for unknown value': lambda c: program(c)['selectors'][1].update(derivation={'L9': '說明'}),
            'selector switching with bad leaf': lambda c: program(c)['selectors'][0].update(switching={'default': {'maxPerDay': [1]}}),
            'bySelectors unknown selector': lambda c: rule(c, 'r2').update(rate={'type': 'bySelectors', 'selectors': ['level', 'nope'], 'table': {'L1|x': '1'}}),
            'bySelectors key wrong arity': lambda c: rule(c, 'r2').update(rate={'type': 'bySelectors', 'selectors': ['level', 'plan'], 'table': {'L1': '1'}}),
            'bySelectors key bad value': lambda c: rule(c, 'r2').update(rate={'type': 'bySelectors', 'selectors': ['level', 'plan'], 'table': {'L1|gold': '1'}}),
            'bySelectors wildcard matches nothing': lambda c: rule(c, 'r2').update(rate={'type': 'bySelectors', 'selectors': ['level', 'plan'], 'table': {'Z*|fixed': '1'}}),
            'list as unit': lambda c: rule(c, 'r1').update(unit=['points']),
            'list as merchant set': lambda c: program(c)['exclusions'][0].update(when={'merchantIn': ['conv']}),
            'rule without group': lambda c: rule(c, 'r1').pop('group'),
            'list as rule id': lambda c: rule(c, 'r1').update(id=['r1']),
            'list as selector id': lambda c: program(c)['selectors'][0].update(id=['plan']),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                catalog = minimal_catalog()
                mutate(catalog)
                with self.assertRaises(ValueError):
                    validate_catalog(catalog)


def nested_not(depth):
    node = {'country': 'TW'}
    for _ in range(depth):
        node = {'not': node}
    return node


class PublishingTests(unittest.TestCase):
    def published_ids(self, evidence, catalog=None, now=NOW):
        programs, pending = build_programs(catalog or minimal_catalog(), evidence, now)
        return {r['id'] for r in programs[0]['rules']}, pending

    def test_valid_evidence_publishes_every_rule(self):
        ids, pending = self.published_ids(valid_evidence())
        self.assertEqual(ids, {'r1', 'r2', 'r3'})
        self.assertEqual(pending, {})

    def test_only_rules_citing_failed_evidence_are_withdrawn(self):
        for name, mutate in {
            'changed': lambda s: s.update(sha256='c' * 64),
            'error': lambda s: s.update(error='timeout'),
            'error with old digest': lambda s: s.update(error='stale'),
        }.items():
            with self.subTest(name):
                evidence = valid_evidence()
                mutate(evidence['bank_card']['supportingSources'][SUPPORT])
                ids, pending = self.published_ids(evidence)
                self.assertEqual(ids, {'r1', 'r3'})
                self.assertEqual(set(pending), {'bank_card/r2'})
                self.assertIn('e2', pending['bank_card/r2'])

    def test_missing_supporting_source_withdraws_its_rules(self):
        evidence = valid_evidence()
        evidence['bank_card']['supportingSources'] = {}
        ids, pending = self.published_ids(evidence)
        self.assertEqual(ids, {'r1', 'r3'})

    def test_primary_failure_withdraws_rules_and_exclusions_citing_it(self):
        evidence = {'bank_card': {'url': PRIMARY, 'error': 'down'}}
        ids, pending = self.published_ids(evidence)
        self.assertEqual(ids, {'r3'})
        self.assertEqual(set(pending), {'bank_card/r1', 'bank_card/r2'})

    def test_failed_exclusion_evidence_withdraws_the_rules_it_limits(self):
        catalog = minimal_catalog()
        program(catalog)['exclusions'][0]['evidenceRefs'] = ['e2#terms']
        evidence = valid_evidence()
        evidence['bank_card']['supportingSources'][SUPPORT]['sha256'] = 'c' * 64
        ids, pending = self.published_ids(evidence, catalog)
        self.assertEqual(ids, {'r3'})
        self.assertIn('x1', pending['bank_card/r1'])

    def test_rule_is_withdrawn_when_its_fallback_target_is_withdrawn(self):
        catalog = minimal_catalog()
        program(catalog)['exclusions'][0].update(effect='fallback', to='r2')
        evidence = valid_evidence()
        evidence['bank_card']['supportingSources'][SUPPORT]['sha256'] = 'c' * 64
        programs, pending = build_programs(catalog, evidence, NOW)
        self.assertEqual({r['id'] for r in programs[0]['rules']}, {'r3'})
        self.assertIn('r2', pending['bank_card/r1'])
        self.assertEqual(programs[0]['exclusions'], [])

    def test_rule_is_withdrawn_when_its_overflow_target_is_withdrawn(self):
        catalog = minimal_catalog()
        rule(catalog, 'r1')['caps'][0]['overflowRule'] = 'r2'
        evidence = valid_evidence()
        evidence['bank_card']['supportingSources'][SUPPORT]['sha256'] = 'c' * 64
        ids, pending = self.published_ids(evidence, catalog)
        self.assertEqual(ids, {'r3'})
        self.assertIn('r2', pending['bank_card/r1'])

    def test_withdrawal_follows_reference_chains(self):
        catalog = minimal_catalog()
        r4 = copy.deepcopy(rule(catalog, 'r1'))
        r4.update(id='r4', evidenceRefs=['e1#rate'])
        r4['caps'][0]['overflowRule'] = 'r1'
        program(catalog)['rules'].append(r4)
        rule(catalog, 'r1')['caps'][0]['overflowRule'] = 'r2'
        evidence = valid_evidence()
        evidence['bank_card']['supportingSources'][SUPPORT]['sha256'] = 'c' * 64
        ids, pending = self.published_ids(evidence, catalog)
        self.assertEqual(ids, {'r3'})
        self.assertIn('r1', pending['bank_card/r4'])

    def test_expired_rules_are_not_published(self):
        ids, pending = self.published_ids(valid_evidence(), now=datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(ids, {'r3'})
        self.assertIn('expired', pending['bank_card/r1'])

    def test_published_program_records_observed_digests_and_drops_withdrawn_exclusions(self):
        evidence = valid_evidence()
        evidence['bank_card']['sha256'] = 'c' * 64
        programs, _ = build_programs(minimal_catalog(), evidence, NOW)
        self.assertEqual(programs[0]['exclusions'], [])
        observed = {e['id']: e['observedSha256'] for e in programs[0]['evidence']}
        self.assertEqual(observed, {'e1': 'c' * 64, 'e2': SHA_B})

    def test_invalid_catalog_is_rejected_before_publishing(self):
        catalog = minimal_catalog()
        rule(catalog, 'r1')['unit'] = 'cash'
        with self.assertRaises(ValueError):
            build_programs(catalog, valid_evidence(), NOW)


class SourceRegistryTests(unittest.TestCase):
    registry = [{'cardId': 'bank_card', 'url': PRIMARY, 'supportingUrls': [SUPPORT]}]

    def test_registered_sources_pass(self):
        check_sources(minimal_catalog(), self.registry)

    def test_unregistered_or_misassigned_sources_are_rejected(self):
        for name, mutate in {
            'unknown card': lambda c: program(c)['evidence'][0].update(sourceCard='other'),
            'primary url mismatch': lambda c: program(c)['evidence'][0].update(url=SUPPORT),
            'supporting url not registered': lambda c: program(c)['evidence'][1].update(url='https://bank.example/z'),
        }.items():
            with self.subTest(name):
                catalog = minimal_catalog()
                mutate(catalog)
                with self.assertRaises(ValueError):
                    check_sources(catalog, self.registry)


class PointerTests(unittest.TestCase):
    def test_resolves_escaped_json_pointer(self):
        doc = {':items': {'a/b': [{'~k': 'value'}]}}
        self.assertEqual(resolve_pointer(doc, '/:items/a~1b/0/~0k'), 'value')

    def test_missing_path_raises(self):
        with self.assertRaises(ValueError):
            resolve_pointer({'a': []}, '/a/0')


class CatalogFileTests(unittest.TestCase):
    def test_load_catalog_reads_units_sets_and_card_programs(self):
        root = Path(__file__).resolve().parents[1] / 'programs'
        catalog = load_catalog(root)
        self.assertEqual(set(catalog), {'rewardUnits', 'merchantSets', 'programs'})
        validate_catalog(catalog)


if __name__ == '__main__':
    unittest.main()
