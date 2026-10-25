"""Schema v2 reward programs: strict validation and evidence-bound publishing.

The format is specified in docs/plans/reward_rule_schema_v2.md. Rules are data;
each priced rule and exclusion cites reviewed official evidence, and a rule is
published only while every source it relies on still matches its approval.
"""
import fnmatch
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROGRAMS_SCHEMA_VERSION = 2

PLATFORMS = {'direct', 'applePay', 'googlePay', 'samsungPay', 'garminPay', 'linePay', 'jkoPay', 'taiwanPay',
             'pxPlus', 'pxPay', 'easyWallet', 'piWallet', 'icashPay', 'esunWallet', 'ipassMoney', 'plusPay', 'twqr',
             'openWallet', 'myFamiPay'}
FUNDING = {'creditCard', 'debitCard', 'bankAccount', 'storedBalance'}
PRESENCE = {'inPerson', 'online'}
SPEND_TYPES = {'general', 'insurance', 'installment', 'storedValue', 'utility', 'tax', 'tuition', 'government',
               'medical', 'topup', 'cashAdvance', 'fee', 'onboard'}
LAYERS = {'L1', 'L2', 'L3'}
RULE_KINDS = {'base', 'bonus'}
ROUNDING_MODES = {'halfUp', 'floor', 'ceil'}
ROUNDING_BASES = {'perTransaction', 'monthlyAggregate', 'perCampaign'}
SCOPES = {'card', 'primaryAndSupplementary', 'household', 'account'}
# QR code standards a scan can establish (V2-S6: TWQR stands).
QR_STANDARDS = {'twqr'}
PERIODS = {'calendarMonth', 'billingCycle', 'campaign', 'day'}
SELECTOR_KINDS = {'plan', 'tier', 'flag', 'variant'}
SELECTOR_SOURCES = {'userDeclared', 'issuerRecord'}
CASH = {'yes', 'no', 'restricted'}
CONFIDENCE = {'issuerList', 'brandOnly', 'mccOnly'}
EVIDENCE_FORMATS = {'aemModel', 'html', 'pdf'}
EFFECTIVE_RULES = {'sameDayMidnight', 'nextDay', 'nextMonthFirstDay', 'immediate'}
EFFECTIVE_BASES = {'authorizationDate', 'consumptionDate', 'postingDate'}
REQUIRE_FIELDS = {'per': PERIODS, 'deadline': {'monthLastDay', 'campaignEnd'},
                  'quota': {'limited', 'unlimited', 'unknown'},
                  'effectiveFrom': {'claimDay', 'registrationDay', 'approvalDay'}, 'source': SELECTOR_SOURCES}
MAX_DEPTH = 8

ID = re.compile(r'^[a-z][a-zA-Z0-9_.-]{0,79}$')
VALUE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
DECIMAL = re.compile(r'^(0|[1-9][0-9]*)(\.[0-9]{1,4})?$')
SHA = re.compile(r'^[0-9a-f]{64}$')
COUNTRY = re.compile(r'^[A-Z]{2}$')
CURRENCY = re.compile(r'^[A-Z]{3}$')
CAP_AMOUNT = re.compile(r'^((0|[1-9][0-9]*)(\.[0-9]{1,2})?|creditLimit(\+[1-9][0-9]*)?)$')
# One spelling on every Python version: 3.11+ fromisoformat also accepts Z,
# hour-only, basic forms and 1-6 digit fractions; 3.9 parses only 3 or 6 digits.
DATETIME = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.([0-9]{3}|[0-9]{6}))?[+-][0-9]{2}:[0-9]{2}$')


def _fail(where, message):
    raise ValueError(f'{where}: {message}')


def _keys(obj, where, required, optional=()):
    if not isinstance(obj, dict):
        _fail(where, 'must be an object')
    missing = set(required) - set(obj)
    extra = set(obj) - set(required) - set(optional)
    if missing:
        _fail(where, f'missing {sorted(missing)}')
    if extra:
        _fail(where, f'unknown {sorted(extra)}')


def _text(value, where, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(where, 'must be non-empty text')


def _ident(value, where):
    if not isinstance(value, str) or not ID.match(value):
        _fail(where, 'invalid identifier')


def _list(value, where, minimum=1, maximum=200):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail(where, f'must be a list of {minimum}-{maximum} items')
    return value


def _decimal(value, where, positive=False, maximum=None):
    if not isinstance(value, str) or not DECIMAL.match(value):
        _fail(where, 'must be a decimal string')
    number = Decimal(value)
    if positive and number <= 0:
        _fail(where, 'must be positive')
    if maximum is not None and number > maximum:
        _fail(where, f'must not exceed {maximum}')
    return number


def _datetime(value, where):
    if not isinstance(value, str) or 'T' not in value:
        _fail(where, 'must be an ISO datetime')
    if not DATETIME.match(value):
        _fail(where, 'must be YYYY-MM-DDTHH:MM:SS[.fff|.ffffff]±HH:MM with an explicit UTC offset')
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        _fail(where, 'must be a real calendar datetime')


def _plain(value, where, depth=0):
    """Bounded descriptive JSON: objects, lists of objects, short text or small integers."""
    if depth > 4:
        _fail(where, 'nested too deeply')
    if isinstance(value, dict):
        if not value:
            _fail(where, 'must be non-empty')
        for key, child in value.items():
            if not isinstance(key, str) or not VALUE.match(key):
                _fail(where, f'invalid key {key!r}')
            _plain(child, f'{where}.{key}', depth + 1)
    elif isinstance(value, list):
        for child in _list(value, where, maximum=20):
            if not isinstance(child, dict):
                _fail(where, 'lists may only hold objects')
            _plain(child, where, depth + 1)
    elif type(value) is int:
        if not 0 <= value <= 10000:
            _fail(where, 'integer out of range')
    else:
        _text(value, where, 120)


def _unique(items, where):
    ids = [item.get('id') if isinstance(item, dict) else None for item in items]
    if len(set(ids)) != len(ids):
        _fail(where, 'duplicate id')


def resolve_pointer(document, pointer):
    """Resolve an RFC 6901 JSON pointer, raising ValueError when absent."""
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('JSON pointer must be absolute')
    node = document
    for raw in pointer[1:].split('/'):
        key = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            raise ValueError(f'JSON pointer {pointer} not found')
    return node


def _validate_unit(unit):
    where = f"rewardUnit {unit.get('id') if isinstance(unit, dict) else '?'}"
    _keys(unit, where, {'id', 'label', 'twdValue', 'cashEquivalent'}, {'redemptionNote', 'expiry'})
    _ident(unit['id'], where)
    _text(unit['label'], where, 80)
    if unit['twdValue'] is not None:
        _decimal(unit['twdValue'], where, positive=True)
    if unit['cashEquivalent'] not in CASH:
        _fail(where, 'invalid cashEquivalent')
    if 'redemptionNote' in unit:
        _text(unit['redemptionNote'], where)
    if 'expiry' in unit:
        _keys(unit['expiry'], where, {'rule'}, {'resetOn', 'accrualWindow'})
        for key, value in unit['expiry'].items():
            _text(value, f'{where} expiry {key}', 80)


def _validate_merchant_set(item):
    where = f"merchantSet {item.get('id') if isinstance(item, dict) else '?'}"
    _keys(item, where, {'id', 'label', 'match', 'matchConfidence', 'openEnded'}, {'notes'})
    _ident(item['id'], where)
    _text(item['label'], where, 80)
    _keys(item['match'], where, set(), {'brands', 'mcc', 'merchantIds'})
    if not item['match']:
        _fail(where, 'match needs brands, mcc or merchantIds')
    for key, values in item['match'].items():
        for value in _list(values, f'{where} {key}', maximum=2000):
            _text(value, f'{where} {key}', 120)
            if key == 'mcc' and not re.match(r'^[0-9]{4}$', value):
                _fail(where, 'mcc must be four digits')
    if item['matchConfidence'] not in CONFIDENCE or type(item['openEnded']) is not bool:
        _fail(where, 'invalid confidence or openEnded')
    if 'notes' in item:
        _text(item['notes'], where)


class _Context:
    def __init__(self, program, units, merchant_sets):
        self.program = program
        self.units = units
        self.merchant_sets = merchant_sets
        self.selectors = {s['id']: s for s in program['selectors']}
        self.rules = {r['id']: r for r in program['rules']}
        self.groups = {r['group'] for r in program['rules']}
        self.anchors = {e['id']: set(e['anchors']) for e in program['evidence']}


def _priced(ctx, rule_id):
    rule = ctx.rules.get(rule_id) if isinstance(rule_id, str) else None
    return rule is not None and rule.get('status') != 'uncovered'


def _selector_value_ok(selector, value):
    if selector['kind'] == 'flag':
        return type(value) is bool
    return isinstance(value, str) and value in selector['values']


def _validate_condition(node, ctx, where, depth=0):
    if depth > MAX_DEPTH:
        _fail(where, 'condition too deep')
    if not isinstance(node, dict) or len(node) != 1:
        _fail(where, 'condition node must have exactly one key')
    (key, value), = node.items()
    if key in ('all', 'any'):
        for child in _list(value, f'{where} {key}', maximum=30):
            _validate_condition(child, ctx, where, depth + 1)
    elif key == 'not':
        _validate_condition(value, ctx, where, depth + 1)
    elif key == 'route':
        _keys(value, f'{where} route', set(), {'platform', 'funding'})
        if not value:
            _fail(where, 'route needs platform or funding')
        for field, allowed in (('platform', PLATFORMS), ('funding', FUNDING)):
            if field in value and (not set(_list(value[field], f'{where} {field}', maximum=30)) <= allowed
                                   or len(set(value[field])) != len(value[field])):
                _fail(where, f'unknown {field}')
    elif key in ('merchantIn', 'merchantNotIn'):
        if value not in ctx.merchant_sets:
            _fail(where, f'unknown merchant set {value}')
    elif key == 'country':
        if not isinstance(value, str) or not COUNTRY.match(value):
            _fail(where, 'country must be ISO 3166 alpha-2')
    elif key == 'currency':
        if not isinstance(value, str) or not CURRENCY.match(value):
            _fail(where, 'currency must be ISO 4217')
    elif key == 'presence':
        if value not in PRESENCE:
            _fail(where, 'invalid presence')
    elif key == 'spendType':
        values = value if isinstance(value, list) else [value]
        if not values or not all(isinstance(v, str) and v in SPEND_TYPES for v in values):
            _fail(where, 'invalid spendType')
    elif key == 'selector':
        if not isinstance(value, dict) or len(value) != 1:
            _fail(where, 'selector condition names exactly one selector')
        (sid, expected), = value.items()
        selector = ctx.selectors.get(sid)
        if selector is None:
            _fail(where, f'unknown selector {sid}')
        expected_values = expected if isinstance(expected, list) else [expected]
        if not expected_values or not all(_selector_value_ok(selector, v) for v in expected_values):
            _fail(where, f'value not allowed for selector {sid}')
    elif key == 'qrStandard':
        values = value if isinstance(value, list) else [value]
        if not values or not all(isinstance(v, str) and v in QR_STANDARDS for v in values):
            _fail(where, 'invalid qrStandard')
    elif key == 'issuerDecision':
        _text(value, where, 300)
    else:
        _fail(where, f'unknown condition {key}')


def _validate_refs(refs, ctx, where):
    for ref in _list(refs, f'{where} evidenceRefs', maximum=20):
        if not isinstance(ref, str) or ref.count('#') != 1:
            _fail(where, 'evidence ref must be evidenceId#anchor')
        evidence_id, anchor = ref.split('#')
        if anchor not in ctx.anchors.get(evidence_id, ()):
            _fail(where, f'unknown evidence anchor {ref}')


def _validate_rate(rate, ctx, where):
    if not isinstance(rate, dict):
        _fail(where, 'rate must be an object')
    kind = rate.get('type')
    if kind == 'fixed':
        _keys(rate, where, {'type', 'percent'})
        _decimal(rate['percent'], f'{where} percent', positive=True, maximum=100)
    elif kind == 'bySelector':
        _keys(rate, where, {'type', 'selector', 'table'})
        selector = ctx.selectors.get(rate['selector'])
        if selector is None or selector['kind'] == 'flag':
            _fail(where, 'rate selector must be a declared non-flag selector')
        if not isinstance(rate['table'], dict) or not rate['table']:
            _fail(where, 'rate table must be non-empty')
        for key, percent in rate['table'].items():
            if key not in selector['values']:
                _fail(where, f'rate table key {key} is not a selector value')
            _decimal(percent, f'{where} percent', positive=True, maximum=100)
    elif kind == 'bySelectors':
        _keys(rate, where, {'type', 'selectors', 'table'})
        names = _list(rate['selectors'], where, minimum=2, maximum=5)
        if len(set(names)) != len(names) or any(n not in ctx.selectors for n in names):
            _fail(where, 'rate selectors must be distinct declared selectors')
        selectors = [ctx.selectors[n] for n in names]
        if not isinstance(rate['table'], dict) or not rate['table']:
            _fail(where, 'rate table must be non-empty')
        for key, percent in rate['table'].items():
            parts = key.split('|') if isinstance(key, str) else []
            if len(parts) != len(selectors):
                _fail(where, f'rate table key {key} must name one value per selector')
            for part, selector in zip(parts, selectors):
                if selector['kind'] == 'flag':
                    allowed = part in ('true', 'false')
                elif part.endswith('*'):
                    allowed = len(part) > 1 and any(v.startswith(part[:-1]) for v in selector['values'])
                else:
                    allowed = part in selector['values']
                if not allowed:
                    _fail(where, f"rate table key {key}: {part} is not a value of {selector['id']}")
            _decimal(percent, f'{where} percent', positive=True, maximum=100)
    else:
        _fail(where, 'unsupported rate type')


def _validate_cap(cap, ctx, where):
    _keys(cap, where, {'measure', 'amount', 'per', 'scope', 'overflowRule'}, {'when'})
    if cap['measure'] not in ('spend', 'reward') or not isinstance(cap['amount'], str) or not CAP_AMOUNT.match(cap['amount']):
        _fail(where, 'invalid cap measure or amount')
    if cap['per'] not in PERIODS or cap['scope'] not in SCOPES:
        _fail(where, 'invalid cap period or scope')
    if cap['overflowRule'] is not None and not _priced(ctx, cap['overflowRule']):
        _fail(where, 'overflow rule must be a priced rule')
    if 'when' in cap:
        _validate_condition(cap['when'], ctx, where)


def _validate_rule(rule, ctx):
    where = f"rule {rule.get('id') if isinstance(rule, dict) else '?'}"
    if isinstance(rule, dict) and rule.get('status') == 'uncovered':
        _keys(rule, where, {'id', 'layer', 'kind', 'group', 'status', 'notes'})
    else:
        _keys(rule, where, {'id', 'layer', 'kind', 'group', 'period', 'when', 'rate', 'includesBase', 'unit',
                            'rounding', 'combinable', 'evidenceRefs'}, {'caps', 'requires', 'notes'})
    _ident(rule['id'], where)
    _ident(rule['group'], where)
    if rule['layer'] not in LAYERS or rule['kind'] not in RULE_KINDS:
        _fail(where, 'invalid layer or kind')
    if rule.get('status') == 'uncovered':
        _text(rule['notes'], where)
        return
    _keys(rule['period'], where, {'from', 'to'})
    if _datetime(rule['period']['from'], where) >= _datetime(rule['period']['to'], where):
        _fail(where, 'period must end after it starts')
    _validate_condition(rule['when'], ctx, where)
    _validate_rate(rule['rate'], ctx, where)
    if type(rule['includesBase']) is not bool:
        _fail(where, 'includesBase must be boolean')
    if rule['unit'] not in ctx.units:
        _fail(where, f"unknown unit {rule['unit']}")
    _keys(rule['rounding'], where, {'mode', 'basis'}, {'scope'})
    if rule['rounding']['mode'] not in ROUNDING_MODES or rule['rounding']['basis'] not in ROUNDING_BASES:
        _fail(where, 'invalid rounding')
    if 'scope' in rule['rounding'] and rule['rounding']['scope'] not in SCOPES:
        _fail(where, 'invalid rounding scope')
    for cap in _list(rule['caps'], where, maximum=10) if 'caps' in rule else []:
        _validate_cap(cap, ctx, f'{where} cap')
    for item in _list(rule['requires'], where, maximum=10) if 'requires' in rule else []:
        _keys(item, f'{where} requires', {'id', 'label'}, set(REQUIRE_FIELDS))
        _ident(item['id'], where)
        _text(item['label'], where, 120)
        for field, allowed in REQUIRE_FIELDS.items():
            if field in item and (not isinstance(item[field], str) or item[field] not in allowed):
                _fail(where, f'requires {field} must be one of {sorted(allowed)}')
    combinable = rule['combinable']
    if combinable == {'default': 'unknown'}:
        pass
    elif isinstance(combinable, dict) and len(combinable) == 1 and set(combinable) <= {'with', 'notWith'}:
        groups = next(iter(combinable.values()))
        if not set(_list(groups, where, maximum=20)) <= ctx.groups:
            _fail(where, 'combinable names an unknown group')
    else:
        _fail(where, 'combinable must be {"default":"unknown"}, {"with":[...]} or {"notWith":[...]}')
    _validate_refs(rule['evidenceRefs'], ctx, where)
    if 'notes' in rule:
        _text(rule['notes'], where)


def _validate_exclusion(item, ctx):
    where = f"exclusion {item.get('id') if isinstance(item, dict) else '?'}"
    _keys(item, where, {'id', 'appliesTo', 'when', 'effect', 'evidenceRefs'}, {'to', 'notes'})
    _ident(item['id'], where)
    for pattern in _list(item['appliesTo'], where, maximum=30):
        if not isinstance(pattern, str) or not fnmatch.filter(ctx.rules, pattern):
            _fail(where, f'appliesTo {pattern} matches no rule')
    _validate_condition(item['when'], ctx, where)
    if item['effect'] == 'zero':
        if 'to' in item:
            _fail(where, 'zero effect cannot name a fallback rule')
    elif item['effect'] == 'fallback':
        if not _priced(ctx, item.get('to')):
            _fail(where, 'fallback rule must be a priced rule')
    else:
        _fail(where, 'effect must be zero or fallback')
    _validate_refs(item['evidenceRefs'], ctx, where)
    if 'notes' in item:
        _text(item['notes'], where)


def _validate_evidence(item):
    where = f"evidence {item.get('id') if isinstance(item, dict) else '?'}"
    _keys(item, where, {'id', 'sourceCard', 'url', 'role', 'format', 'approvedSha256', 'anchors', 'approval'},
          {'section'})
    _ident(item['id'], where)
    _ident(item['sourceCard'], where)
    if not isinstance(item['url'], str) or not item['url'].startswith('https://') or len(item['url']) > 500:
        _fail(where, 'url must be https')
    if item['role'] not in ('primary', 'supporting') or item['format'] not in EVIDENCE_FORMATS:
        _fail(where, 'invalid role or format')
    if not isinstance(item['approvedSha256'], str) or not SHA.match(item['approvedSha256']):
        _fail(where, 'approvedSha256 must be lowercase hex')
    if not isinstance(item['anchors'], dict) or not item['anchors']:
        _fail(where, 'anchors must be non-empty')
    for name, anchor in item['anchors'].items():
        _ident(name, where)
        _keys(anchor, f'{where} anchor {name}', {'pointer', 'contains'})
        if item['format'] in ('html', 'pdf') and anchor['pointer'] != '/text':
            _fail(where, 'HTML/PDF anchor pointer must be /text')
        if not isinstance(anchor['pointer'], str) or not anchor['pointer'].startswith('/'):
            _fail(where, 'anchor pointer must be absolute')
        _text(anchor['contains'], where, 200)
    if 'section' in item:
        # The approval covers only the terms between these markers (V2-S6).
        if item['format'] != 'html' or item['role'] != 'primary':
            _fail(where, 'section is only allowed on primary HTML evidence')
        _keys(item['section'], f'{where} section', {'start', 'end'})
        for marker in ('start', 'end'):
            _text(item['section'][marker], where, 200)
        if item['section']['start'] == item['section']['end']:
            _fail(where, 'section markers must differ')
    _keys(item['approval'], where, {'by', 'at'}, {'note'})
    _text(item['approval']['by'], where, 120)
    _datetime(item['approval']['at'], where)
    if 'note' in item['approval']:
        _text(item['approval']['note'], where)


def _validate_selector(item):
    where = f"selector {item.get('id') if isinstance(item, dict) else '?'}"
    _keys(item, where, {'id', 'kind', 'source'},
          {'label', 'values', 'history', 'effective', 'switching', 'derivation', 'appliesToSupplementary'})
    _ident(item['id'], where)
    if item['kind'] not in SELECTOR_KINDS or item['source'] not in SELECTOR_SOURCES:
        _fail(where, 'invalid kind or source')
    if item['kind'] == 'flag':
        if 'values' in item:
            _fail(where, 'flag selectors are boolean')
    else:
        values = _list(item.get('values'), where, maximum=50)
        if len(set(values)) != len(values) or not all(isinstance(v, str) and VALUE.match(v) for v in values):
            _fail(where, 'values must be unique identifiers')
    if 'label' in item:
        _text(item['label'], where, 120)
    if 'history' in item and type(item['history']) is not bool:
        _fail(where, 'history must be boolean')
    if 'effective' in item:
        _keys(item['effective'], f'{where} effective', {'rule'}, {'basis'})
        if item['effective']['rule'] not in EFFECTIVE_RULES or item['effective'].get('basis', 'authorizationDate') not in EFFECTIVE_BASES:
            _fail(where, 'invalid effective rule or basis')
    if 'switching' in item:
        if not isinstance(item['switching'], dict):
            _fail(where, 'switching must be an object')
        _plain(item['switching'], f'{where} switching')
    if 'derivation' in item:
        if not isinstance(item['derivation'], dict) or not item['derivation']:
            _fail(where, 'derivation must be a non-empty object')
        for value, text in item['derivation'].items():
            if value not in item.get('values', []):
                _fail(where, f'derivation names unknown value {value}')
            _text(text, where, 300)
    if 'appliesToSupplementary' in item and item['appliesToSupplementary'] not in ('followsPrimary', 'independent'):
        _fail(where, 'invalid supplementary rule')


def _validate_program(program, units, merchant_sets):
    where = f"program {program.get('id') if isinstance(program, dict) else '?'}"
    _keys(program, where, {'id', 'issuer', 'name', 'rewardUnit', 'selectors', 'rules', 'exclusions', 'evidence'},
          {'variants', 'notCovering'})
    _ident(program['id'], where)
    _ident(program['issuer'], where)
    _text(program['name'], where, 120)
    if program['rewardUnit'] not in units:
        _fail(where, 'unknown reward unit')
    for key in ('variants', 'notCovering'):
        for value in _list(program[key], where, maximum=20) if key in program else []:
            _text(value, where, 300)
    # A program of uncovered notes only makes no reward claim and needs no evidence.
    claims = any(isinstance(r, dict) and r.get('status') != 'uncovered' for r in program['rules']) or bool(program['exclusions'])
    for key, check, minimum in (('selectors', _validate_selector, 0), ('evidence', _validate_evidence, 1 if claims else 0)):
        for item in _list(program[key], f'{where} {key}', minimum=minimum, maximum=50):
            check(item)
        _unique(program[key], f'{where} {key}')
    rules = _list(program['rules'], f'{where} rules', maximum=300)
    exclusions = _list(program['exclusions'], f'{where} exclusions', minimum=0, maximum=300)
    for item in rules + exclusions:
        if not isinstance(item, dict):
            _fail(where, 'rules and exclusions must be objects')
    _unique(rules + exclusions, f'{where} rules/exclusions')
    ctx = _Context(program, units, merchant_sets)
    for rule in rules:
        _validate_rule(rule, ctx)
    for item in exclusions:
        _validate_exclusion(item, ctx)


def validate_catalog(catalog):
    """Raise ValueError unless the catalog follows schema v2 exactly."""
    try:
        _validate_catalog(catalog)
    except (TypeError, KeyError, AttributeError) as error:
        # Malformed shapes (lists where ids belong, missing keys) are schema
        # errors too; callers only need to handle one exception type.
        raise ValueError(f'catalog: malformed structure ({type(error).__name__}: {error})') from error


def _validate_catalog(catalog):
    _keys(catalog, 'catalog', {'rewardUnits', 'merchantSets', 'programs'})
    for key, check in (('rewardUnits', _validate_unit), ('merchantSets', _validate_merchant_set)):
        for item in _list(catalog[key], key, minimum=0, maximum=500):
            check(item)
        _unique(catalog[key], key)
    units = {u['id'] for u in catalog['rewardUnits']}
    merchant_sets = {m['id'] for m in catalog['merchantSets']}
    programs = _list(catalog['programs'], 'programs', minimum=0, maximum=500)
    for program in programs:
        _validate_program(program, units, merchant_sets)
    _unique(programs, 'programs')


def check_sources(catalog, registry):
    """Every evidence item must be a source the pipeline actually collects."""
    cards = {s['cardId']: s for s in registry}
    for program in catalog['programs']:
        for item in program['evidence']:
            source = cards.get(item['sourceCard'])
            if source is None:
                raise ValueError(f"evidence {item['id']}: source card not registered")
            if item['role'] == 'primary' and item['url'] != source['url']:
                raise ValueError(f"evidence {item['id']}: primary url differs from registry")
            if item['role'] == 'supporting' and item['url'] not in source.get('supportingUrls', []):
                raise ValueError(f"evidence {item['id']}: supporting url not registered")


def load_catalog(directory):
    directory = Path(directory)
    programs = [json.loads(path.read_text())
                for sub in ('cards', 'platforms') for path in sorted((directory / sub).glob('*.json'))]
    return {'rewardUnits': json.loads((directory / 'units.json').read_text()),
            'merchantSets': json.loads((directory / 'merchant_sets.json').read_text()),
            'programs': programs}


def section_text(text, start, end):
    """The approved terms: from the unique start marker through the next end marker."""
    if not isinstance(text, str) or text.count(start) != 1:
        raise ValueError('section start marker missing or repeated')
    begin = text.index(start)
    stop = text.find(end, begin)
    if stop < 0 or text.count(end) != 1:
        raise ValueError('section end marker missing or repeated')
    return text[begin:stop + len(end)]


def _evidence_problem(item, evidence):
    source = evidence.get(item['sourceCard'], {})
    if item['role'] == 'primary':
        observed = source
        if source.get('url') != item['url']:
            return None, 'primary source not collected'
    else:
        observed = source.get('supportingSources', {}).get(item['url'])
        if observed is None:
            return None, 'supporting source not collected'
    if observed.get('error'):
        return observed.get('sha256'), 'source unavailable'
    if 'section' in item:
        try:
            section = section_text(observed.get('text'), item['section']['start'], item['section']['end'])
        except ValueError as error:
            return None, f'section boundary: {error}'
        digest = hashlib.sha256(section.encode()).hexdigest()
        for name, anchor in item['anchors'].items():
            if anchor['contains'] not in section:
                return digest, f'anchor {name} not in approved section'
        return digest, None if digest == item['approvedSha256'] else 'source changed since approval'
    if observed.get('sha256') != item['approvedSha256']:
        return observed.get('sha256'), 'source changed since approval'
    return observed.get('sha256'), None


def build_programs(catalog, evidence, now):
    """Return (published programs, pending reasons) for the current evidence."""
    validate_catalog(catalog)
    published, pending = [], {}
    for program in catalog['programs']:
        status = {item['id']: _evidence_problem(item, evidence) for item in program['evidence']}

        def failures(refs):
            ids = sorted({ref.split('#')[0] for ref in refs})
            return [f'{i}: {status[i][1]}' for i in ids if status[i][1]]

        bad_exclusions = {x['id']: failures(x['evidenceRefs']) for x in program['exclusions']}

        def limiting(rule):
            return [x for x in program['exclusions'] if any(fnmatch.fnmatch(rule['id'], p) for p in x['appliesTo'])]

        withdrawn = {}
        priced = [r for r in program['rules'] if r.get('status') != 'uncovered']
        for rule in priced:
            reasons = failures(rule['evidenceRefs'])
            for item in limiting(rule):
                if bad_exclusions[item['id']]:
                    reasons.append(f"exclusion {item['id']} unverifiable ({'; '.join(bad_exclusions[item['id']])})")
            if datetime.fromisoformat(rule['period']['to']) <= now:
                reasons.append('expired')
            if reasons:
                withdrawn[rule['id']] = reasons
        # A published rule must never point at a withdrawn one: follow overflow
        # and fallback references until no further rule is affected.
        changed = True
        while changed:
            changed = False
            for rule in priced:
                if rule['id'] in withdrawn:
                    continue
                reasons = [f"overflow rule {c['overflowRule']} withdrawn" for c in rule.get('caps', [])
                           if c['overflowRule'] in withdrawn]
                reasons += [f"exclusion {x['id']} falls back to withdrawn rule {x['to']}" for x in limiting(rule)
                            if x['effect'] == 'fallback' and x['to'] in withdrawn]
                if reasons:
                    withdrawn[rule['id']] = reasons
                    changed = True
        for rule_id, reasons in withdrawn.items():
            pending[f"{program['id']}/{rule_id}"] = '; '.join(reasons)
        rules = [r for r in program['rules'] if r['id'] not in withdrawn]
        kept = {r['id'] for r in rules}
        exclusions = [x for x in program['exclusions'] if not bad_exclusions[x['id']]
                      and any(fnmatch.filter(kept, p) for p in x['appliesTo'])
                      and (x['effect'] != 'fallback' or x['to'] in kept)]
        published.append({**program, 'rules': rules, 'exclusions': exclusions,
                          'evidence': [{**item, 'observedSha256': status[item['id']][0]}
                                       for item in program['evidence']]})
    return published, pending
