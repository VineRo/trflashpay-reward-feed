"""Publisher checks: format validation is not a substitute for issuer review."""
import math
import re
from datetime import datetime
from urllib.parse import urlparse

PLATFORMS = {'linePay', 'jkoPay', 'taiwanPay', 'pxPlus', 'easyWallet', 'piWallet',
             'icashPay', 'esunWallet', 'ipassMoney', 'plusPay', 'applePay', 'googlePay'}
SCOPES = {'perTransaction', 'calendarMonthly', 'billingCycle', 'totalCampaign', 'unspecified', 'uncapped'}
BASIS = {'totalReward', 'bonusOnly'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rate(value):
    return (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 30)


def texts(value):
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def routes(value):
    return isinstance(value, list) and all(isinstance(v, str) and v in PLATFORMS for v in value)


def date(value):
    require(isinstance(value, str), 'Date must be a string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'Dates require explicit timezone')
    return parsed


def validate(rule):
    require(isinstance(rule, dict), 'Rule must be an object')
    for field in ('cardId', 'cardName', 'bankName', 'promoDescription', 'badgeText', 'officialUrl'):
        require(isinstance(rule.get(field), str) and rule[field].strip(), f'Missing {field}')
    require(rule.get('cardType') in ('creditCard', 'debitCard', 'bankAccount', 'storedBalance'), 'Invalid card type')
    require(routes(rule.get('supportedPlatforms')) and rule['supportedPlatforms'], 'Invalid supported platforms')
    require(rate(rule.get('defaultRewardRate')) and rate(rule.get('promoRewardRate')), 'Invalid reward rate')
    require(texts(rule.get('conditionTags')), 'Missing conditions')
    require(rule.get('isCustomCard', False) is False, 'Issuer feed cannot contain custom cards')
    url = urlparse(rule['officialUrl'])
    require(url.scheme == 'https' and url.hostname and not url.username and not url.password, 'Invalid official URL')
    # Complete snapshots must explicitly clear removed legacy restrictions.
    for field in ('schemes', 'merchantRates', 'requiresAccount', 'accountTierRate',
                  'bonusAllowedPlatforms', 'activeDaysOfWeek', 'verificationRequiredReason'):
        require(field in rule, f'Missing snapshot field {field}')
    reason = rule['verificationRequiredReason']
    require(reason is None or (isinstance(reason, str) and reason.strip()), 'Invalid verification reason')
    date(rule['validUntil'])
    terms = rule.get('issuerTerms')
    if terms is not None:
        require(isinstance(terms, dict) and set(terms) == {
            'version', 'kind', 'rate', 'rounding', 'validFrom', 'validUntil'}, 'Invalid calculation schema')
        require(type(terms['version']) is int and terms['version'] == 1 and
                terms['kind'] == 'ctbcLinePayGeneral' and terms['rounding'] == 'halfUp', 'Unsupported calculation')
        require(rule['cardId'] in {'ctbc_linepay', 'ctbc_linepay_debit'}, 'Wrong issuer calculation')
        require(rate(terms['rate']) and terms['rate'] == rule['promoRewardRate'], 'Inconsistent calculation rate')
        require(date(terms['validFrom']) < date(terms['validUntil']) == date(rule['validUntil']), 'Inconsistent calculation validity')
        require(bool(rule.get('verificationRequiredReason')), 'Conditional plan must retain qualification warning')
    if 'activeDaysOfWeek' in rule and rule['activeDaysOfWeek'] is not None:
        require(isinstance(rule['activeDaysOfWeek'], list) and all(type(d) is int and 1 <= d <= 7 for d in rule['activeDaysOfWeek']), 'Invalid weekday')
    if 'requiresAccount' in rule:
        require(type(rule['requiresAccount']) is bool, 'Invalid qualification')
    if rule.get('accountTierRate') is not None:
        require(rate(rule['accountTierRate']), 'Invalid qualified rate')
    if rule.get('bonusAllowedPlatforms') is not None:
        require(routes(rule['bonusAllowedPlatforms']), 'Invalid bonus routes')
    for key, id_field, rate_field in [('schemes', 'schemeId', 'promoRate'), ('merchantRates', 'ruleId', 'ratePercentage')]:
        entries = rule.get(key)
        if entries is None:
            continue
        require(isinstance(entries, list), f'Invalid {key}')
        ids = set()
        for entry in entries:
            require(isinstance(entry, dict), 'Invalid nested rule')
            entry_id = entry.get(id_field)
            require(isinstance(entry_id, str) and entry_id.strip() and entry_id not in ids, 'Invalid or duplicate nested ID')
            ids.add(entry_id)
            require(rate(entry.get(rate_field)), 'Invalid nested rate')
            for name in ('merchantKeywords', 'targetMccs', 'applicableCategories'):
                if name in entry:
                    require(texts(entry[name]), f'Invalid {name}')
            require(all(re.fullmatch(r'\d{4}', v) for v in entry.get('targetMccs', [])), 'Invalid MCC')
            if 'bonusAllowedPlatforms' in entry:
                require(routes(entry['bonusAllowedPlatforms']), 'Invalid nested bonus routes')
            if key == 'merchantRates':
                require(entry.get('merchantKeywords') or entry.get('targetMccs'), 'Missing merchant scope')
                start = date(entry['validFrom']) if entry.get('validFrom') else None
                end = date(entry['validUntil']) if entry.get('validUntil') else None
                require(not start or not end or start <= end, 'Reversed validity')
                cap = entry.get('capAmount')
                if cap is not None:
                    require(type(cap) in (int, float) and math.isfinite(cap) and cap > 0, 'Invalid cap')
                    require(entry.get('capScope') in SCOPES - {'unspecified', 'uncapped'}, 'Missing cap cycle')
                    require(entry.get('capBasis') in BASIS, 'Missing cap basis')
                    require(isinstance(entry.get('quotaPoolId'), str) and entry['quotaPoolId'].strip(), 'Missing quota pool')
