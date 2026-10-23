"""Conservative issuer-specific observations, never confirmed transaction quotes.

A numeric observation is useful for refreshing displayed terms, but it cannot
establish member eligibility, issuer transaction coding, or remaining quota.
All adapter results MUST retain verificationRequiredReason.
"""
import re
from datetime import datetime, time, timezone, timedelta

TZ = timezone(timedelta(hours=8))


def one(pattern, text):
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError('Expected exactly one authoritative terms section')
    return matches[0]


def validity(text, pattern, now):
    parts = one(pattern, text)
    y, m, d, ey, em, ed = map(int, parts)
    start = datetime(y, m, d, tzinfo=TZ)
    end = datetime.combine(datetime(ey, em, ed).date(), time.max, TZ)
    if not start <= now <= end:
        raise ValueError('Announcement is not currently effective')
    return end.isoformat()


def template(cid, name, bank, source, rate, expiry, tags):
    if not 0 <= rate <= 30:
        raise ValueError('Observed rate outside supported range')
    return {
        'cardId': cid, 'cardName': name, 'bankName': bank, 'cardType': 'creditCard',
        'supportedPlatforms': ['linePay'], 'defaultRewardRate': 0.0,
        'promoRewardRate': rate,
        'promoDescription': f'官方公告最高 {rate:g}%；適用條件與此筆交易資格待確認',
        'badgeText': f'公告最高 {rate:g}%', 'conditionTags': tags,
        'officialUrl': source['url'], 'validUntil': expiry,
        'schemes': None, 'merchantRates': None, 'requiresAccount': False,
        'accountTierRate': None, 'bonusAllowedPlatforms': None, 'activeDaysOfWeek': None,
        'verificationRequiredReason': '公告數字已擷取；一般消費認列、會員資格、排除交易與剩餘額度待確認',
    }


def ctbc(source, now):
    text = source['text']
    credit, debit = one(
        r'國內外一般消費\s+國內外一般刷卡交易，不含本行\s*不回饋交易項目\s*。\s*([0-9]+(?:\.[0-9]+)?)%\s+([0-9]+(?:\.[0-9]+)?)%', text)
    rate = float(credit)
    references = re.findall(r'國內外一般消費([0-9]+(?:\.[0-9]+)?)%', text)
    if not references or any(float(value) != rate for value in references):
        raise ValueError('Conflicting general-spend rates in announcement')
    if any(term not in text for term in ['臺灣 LINE Pay', '分期付款交易恕不適用', '四捨五入至整數']):
        raise ValueError('Missing known eligibility/rounding terms')
    expiry = validity(text,
        r'LINE Pay信用卡自(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})為止', now)
    return template('ctbc_linepay', '中國信託 LINE Pay 信用卡', '中國信託', source,
                    rate, expiry, ['臺灣 LINE Pay 有效綁定', '限一般消費', '分期與除外交易不適用', '逐筆四捨五入至整數點'])


def ubear(source, now):
    text = re.sub(r'\s+', '', source['text'])
    expiry = validity(text, r'(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', now)
    base = float(one(r'國內外一般消費([0-9]+(?:\.[0-9]+)?)%現金回饋，需同時', text))
    bonus, cap = one(r'網路消費加碼([0-9]+(?:\.[0-9]+)?)%回饋採正附卡合併計算，每期回饋上限([0-9]+)元', text)
    totals = re.findall(r'行動支付、網路消費\s*最高([0-9]+(?:\.[0-9]+)?)%現金回饋', text)
    total = base + float(bonus)
    if not totals or any(abs(float(value) - total) > 1e-9 for value in totals) or int(cap) <= 0:
        raise ValueError('Conflicting total, base, bonus or cap')
    if any(term not in text for term in ['帳單e化', '自動扣繳', '不列入網路消費回饋計算', '無條件捨去']):
        raise ValueError('Missing known eligibility/exclusion/rounding terms')
    rule = template('esun_ubear', '玉山 U Bear 卡', '玉山銀行', source, total, expiry,
        ['電子帳單及自動扣繳資格待確認', f'每期網路加碼上限 {cap} 元（非日曆月）',
         '超商、保費等除外交易不適用網路加碼', '逐筆無條件捨去至整數元', '當期剩餘額度待確認'])
    rule['supportedPlatforms'] = ['linePay', 'jkoPay', 'pxPlus', 'icashPay']
    return rule


ADAPTERS = {'ctbc_linepay': ctbc, 'esun_ubear': ubear}


def observe(evidence, now):
    rules, errors = [], {}
    from validator import validate
    for cid, adapter in ADAPTERS.items():
        source = evidence.get(cid, {})
        try:
            if source.get('error') or not source.get('text'):
                raise ValueError('Official source unavailable')
            rule = adapter(source, now)
            if not rule.get('verificationRequiredReason'):
                raise ValueError('Observations cannot establish transaction eligibility')
            validate(rule)
            rules.append(rule)
        except (KeyError, ValueError, TypeError) as error:
            errors[cid] = str(error)
    return rules, errors
