"""Conservative issuer-specific observations, never confirmed transaction quotes.

A numeric observation is useful for refreshing displayed terms, but it cannot
establish member eligibility, issuer transaction coding, or remaining quota.
All adapter results MUST retain verificationRequiredReason.
"""
import re
import json
from pathlib import Path
from datetime import datetime, time, timezone, timedelta

TZ = timezone(timedelta(hours=8))
CALCULATION_APPROVALS = json.loads(Path(__file__).with_name('calculation_approvals.json').read_text())


def one(pattern, text):
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError('Expected exactly one authoritative terms section')
    return matches[0]


def consistent(pattern, text):
    matches = re.findall(pattern, text)
    if not matches or len(set(matches)) != 1:
        raise ValueError('Missing or conflicting repeated terms')
    return matches[0]


def validity(text, pattern, now, consistent_duplicates=False):
    parts = consistent(pattern, text) if consistent_duplicates else one(pattern, text)
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


def ctbc_terms(source, rate, expiry, debit=False):
    prefix = 'LINE Pay簽帳金融卡自' if debit else 'LINE Pay信用卡自'
    parts = one(re.escape(prefix) + r'(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', source['text'])
    y, m, d = map(int, parts[:3])
    return {'version': 1, 'kind': 'ctbcLinePayGeneral', 'rate': rate,
            'rounding': 'halfUp', 'validFrom': datetime(y, m, d, tzinfo=TZ).isoformat(),
            'validUntil': expiry}


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
    rule = template('ctbc_linepay', '中國信託 LINE Pay 信用卡', '中國信託', source,
                    rate, expiry, ['臺灣 LINE Pay 有效綁定', '限一般消費', '分期與除外交易不適用', '逐筆四捨五入至整數點'])
    if source.get('sha256') and source['sha256'] == CALCULATION_APPROVALS.get('ctbc_linepay'):
        rule['issuerTerms'] = ctbc_terms(source, rate, expiry)
    return rule


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


def ctbc_debit(source, now):
    rule = ctbc(source, now)
    credit, debit = one(
        r'國內外一般消費\s+國內外一般刷卡交易，不含本行\s*不回饋交易項目\s*。\s*([0-9]+(?:\.[0-9]+)?)%\s+([0-9]+(?:\.[0-9]+)?)%', source['text'])
    expiry = validity(source['text'],
        r'LINE Pay簽帳金融卡自(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', now)
    rule.update(cardId='ctbc_linepay_debit', cardName='中國信託 LINE Pay 簽帳金融卡',
                cardType='debitCard', promoRewardRate=float(debit), validUntil=expiry,
                promoDescription=f'簽帳金融卡一般消費公告 {float(debit):g}%；交易資格待確認',
                badgeText=f'簽帳一般消費公告 {float(debit):g}%')
    rule.pop('issuerTerms', None)
    if source.get('sha256') and source['sha256'] == CALCULATION_APPROVALS.get('ctbc_linepay_debit'):
        rule['issuerTerms'] = ctbc_terms(source, float(debit), expiry, debit=True)
    return rule



def cube_fixed_domestic(source, now):
    text = re.sub(r'\s+', '', source['text'])
    expiry = validity(text,
        r'「固定回饋」權益方案適用期間：(\d{4})/(\d{1,2})/(\d{1,2})-(\d{4})/(\d{1,2})/(\d{1,2})', now)
    value = float(consistent(r'一般消費\(不含保費\)([0-9]+(?:\.[0-9]+)?)%、海外實體消費', text))
    if any(term not in text for term in ['每筆消費請款入帳後', '小數點第一位四捨五入至整數位',
        '依該筆消費之授權日判斷適用權益方案', '信用額度', '50萬元', '不含CUBE簽帳金融卡']):
        raise ValueError('Missing CUBE recognition or calculation conditions')
    rule = ordinary_observation('cathay_cube', '國泰 CUBE 信用卡（固定回饋國內基本方案）', '國泰世華銀行', source,
        value, expiry, ['僅固定回饋方案之國內一般消費，不含保費及分期',
        '依授權日生效方案及銀行一般消費排除清單', '指定通路、海外及優惠券尚未納入',
        '每筆小樹點四捨五入；跨認列上限情境不試算'], ['linePay', 'jkoPay', 'applePay', 'googlePay'])
    rule['officialUrl'] = 'https://www.cathay-cube.com.tw/cathaybk/personal/product/credit-card/cards/cube'
    return rule


def fubon_j(source, now):
    text = re.sub(r'\s+', '', source['text'])
    rate = float(consistent(r'國內及其他海外地區一般消費享最高([0-9]+(?:\.[0-9]+)?)%', text))
    fallback = float(consistent(r'尚未生效或無持續符合條件，則享([0-9]+(?:\.[0-9]+)?)%回饋', text))
    if not 0 <= fallback <= rate:
        raise ValueError('Conflicting qualified and unqualified rates')
    expiry = validity(text,
        r'基本回饋優惠期間自(\d{4})/(\d{1,2})/(\d{1,2})至(\d{4})/(\d{1,2})/(\d{1,2})止',
        now, consistent_duplicates=True)
    if any(term not in text for term in ['JCash', 'JPoints', '所有消費回饋皆須綁定LINEPay後生效',
                                          '電子帳單', '自動扣繳', '無條件捨去至整數']):
        raise ValueError('Missing card variant, qualification or rounding terms')
    rule = template('fubon_j', '台北富邦 J 卡（回饋版本待確認）', '富邦銀行', source, rate, expiry,
        ['需確認 J Cash／J Points 卡別', '限國內一般消費，非日韓泰旅遊加碼',
         '自動扣繳／電子帳單設定及生效時點待確認', f'未達公告條件時標示 {fallback:g}%，仍需認列一般消費',
         'J Points 須有效綁定臺灣 LINE Pay', 'J Points 每筆取整至整數；J Cash 結算不同，不互套', '資格生效時點有不同敘述，異動情境暫不試算'])
    rule['promoDescription'] = f'國內一般消費公告最高 {rate:g}%；不是旅遊或限量活動加碼，適用資格待確認'
    return rule


def ordinary_observation(cid, name, bank, source, rate, expiry, tags, platforms):
    rule = template(cid, name, bank, source, rate, expiry,
                    ['僅記錄一般消費基本方案，不含其他加碼', *tags])
    rule['supportedPlatforms'] = platforms
    rule['badgeText'] = f'一般消費公告最高 {rate:g}%'
    rule['promoDescription'] = f'一般消費基本方案公告最高 {rate:g}%；指定通路、新戶及限量加碼未納入，行動支付認列與資格待確認'
    return rule


def federal_jihe(source, now):
    text = re.sub(r'\s+', '', source['text'])
    first, second = one(r'國內消費([0-9]+(?:\.[0-9]+)?)%回饋無上限係指聯邦吉鶴卡原權益國內一般消費([0-9]+(?:\.[0-9]+)?)%回饋', text)
    if first != second or '需以新台幣請款' not in text:
        raise ValueError('Conflicting domestic base rate or currency scope')
    parts = consistent(r'活動期間：(\d{3})/(\d{1,2})/(\d{1,2})-(\d{3})/(\d{1,2})/(\d{1,2})', text)
    y, m, d, ey, em, ed = map(int, parts)
    window = f'{y + 1911}/{m}/{d}~{ey + 1911}/{em}/{ed}'
    expiry = validity(window, r'(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', now)
    return ordinary_observation('federal_jihe', '聯邦吉鶴卡', '聯邦銀行', source, float(first), expiry,
        ['需以新臺幣請款且認列國內一般消費', '不含日本、日系名店、新戶及需登錄加碼'], ['linePay', 'jkoPay'])


def unicard(source, now):
    text = re.sub(r'\s+', '', source['text'])
    parts = one(r'一般消費最高享([0-9]+(?:\.[0-9]+)?)%活動期間：(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', text)
    rate = float(parts[0])
    qualified = float(consistent(r'一般消費享([0-9]+(?:\.[0-9]+)?)%玉山epoint回饋，需同時申辦帳單e化及申辦玉山銀行臺幣帳戶自動扣繳', text))
    if rate != qualified or '成功扣繳卡費' not in text or '逐筆四捨五入' not in text:
        raise ValueError('Missing or conflicting base qualification terms')
    y, m, d, ey, em, ed = parts[1:]
    expiry = validity(f'{y}/{m}/{d}~{ey}/{em}/{ed}',
        r'(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', now)
    return ordinary_observation('esun_unicard', '玉山 Unicard', '玉山銀行', source, rate, expiry,
        ['需電子帳單及臺幣帳戶成功自扣', '指定方案、特店與支付類別加碼需另行核對', '一般消費排除項目與交易認列待確認'],
        ['linePay', 'jkoPay', 'pxPlus'])


def fubon_costco(source, now):
    text = re.sub(r'\s+', '', source['text'])
    rate, y, m, d = one(r'店外一般消費([0-9]+(?:\.[0-9]+)?)%好多金回饋無上限活動日期：即日起~(\d{4})/(\d{1,2})/(\d{1,2})活動內容', text)
    repeated = consistent(r'於店外一般消費享([0-9]+(?:\.[0-9]+)?)%好多金回饋無上限', text)
    if rate != repeated or '不予計算好多金' not in text:
        raise ValueError('Conflicting outside-store rate or missing exclusions')
    expiry = datetime.combine(datetime(int(y), int(m), int(d)).date(), time.max, TZ)
    if now > expiry:
        raise ValueError('Outside-store offer expired')
    return ordinary_observation('fubon_costco', '富邦 Costco 聯名卡', '富邦銀行', source,
        float(rate), expiry.isoformat(), ['僅店外一般消費好多金方案', '不含店內、加油、交通及限量登錄加碼',
        '請款期限、好多金資格及行動支付認列待確認'], ['linePay', 'jkoPay', 'pxPlus'])


def sport(source, now):
    text = re.sub(r'\s+', '', source['text'])
    add, qualified, base = one(r'國內外一般消費基本回饋電子化帳單加碼([0-9]+(?:\.[0-9]+)?)%，須於當月帳單結帳日前，完成設定電子或行動帳單，方可符合最高([0-9]+(?:\.[0-9]+)?)%回饋資格，否則僅享([0-9]+(?:\.[0-9]+)?)%回饋', text)
    if abs(float(base) + float(add) - float(qualified)) > 1e-9 or '大咖DACARD' not in text:
        raise ValueError('Inconsistent electronic-statement base rate')
    y, m, d, em, ed = one(r'一[.．、]活動期間：(\d{4})/(\d{1,2})/(\d{1,2})-(\d{1,2})/(\d{1,2})二[.．、]活動內容', text)
    expiry = validity(f'{y}/{m}/{d}~{y}/{em}/{ed}',
        r'(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', now)
    return ordinary_observation('sinopac_sport', '永豐 SPORT 卡', '永豐銀行', source,
        float(qualified), expiry, ['需於結帳前設定電子或行動帳單',
        '運動獎勵、指定支付、達標及新戶加碼未納入', '大咖 DACARD 與其他門檻仍須另核對'], ['linePay', 'jkoPay'])


def taishin_richart(source, now):
    text = re.sub(r'\s+', '', source['text'])
    # The legacy @GoGo ID now follows the issuer's explicitly named migration.
    if '@GoGo卡' not in text or '台新Richart卡' not in text:
        raise ValueError('Missing legacy card migration scope')
    rate = float(consistent(r'一般消費享([0-9]+(?:\.[0-9]+)?)%台新P(?:oint|iont)\(信用卡\)', text))
    expiry = validity(text,
        r'活動期間：(\d{4})/(\d{1,2})/(\d{1,2})[–~－-](\d{4})/(\d{1,2})/(\d{1,2})之消費適用', now, consistent_duplicates=True)
    if '小數點後無條件捨去' not in text or '不回饋交易' not in text:
        raise ValueError('Missing rounding or exclusions')
    return ordinary_observation('taishin_gogo', '台新 Richart 卡（原 @GoGo）', '台新銀行', source, rate, expiry,
        ['原 @GoGo 已納入 Richart 權益', '僅一般消費基本台新 Point，不含切換方案', '依逐筆無條件捨去取整，支付認列與排除交易待核對'],
        ['linePay', 'jkoPay', 'pxPlus', 'plusPay'])


def richart_debit(source, now):
    text = re.sub(r'\s+', '', source['text'])
    parts = one(r'(\d{4})/(\d{1,2})/(\d{1,2})-(\d{4})/(\d{1,2})/(\d{1,2})RichartMastercard簽帳金融卡一般消費享([0-9]+(?:\.[0-9]+)?)%無上限', text)
    y, m, d, ey, em, ed = parts[:6]
    expiry = validity(f'{y}/{m}/{d}~{ey}/{em}/{ed}',
        r'(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})', now)
    if any(term not in text for term in ['不回饋消費項目', '晶片金融卡消費扣款功能並非刷卡消費', '入帳日', '每月結算後']):
        raise ValueError('Missing debit-specific transaction or settlement scope')
    rule = ordinary_observation('taishin_richart_debit', '台新 Richart Mastercard 簽帳金融卡', '台新銀行', source,
        float(parts[6]), expiry, ['限 Mastercard 簽帳購物，不是晶片金融卡扣款', '每卡依入帳日按月結算',
        '超商、全聯及其他除外消費不適用', '青年、新戶與 LINE Pay 加碼未納入'], ['linePay'])
    rule['cardType'] = 'debitCard'
    return rule


def jko_card(source, now):
    text = re.sub(r'\s+', '', source['text'])
    # Current and archived campaigns coexist in this page. Scope each card
    # campaign before reading rates; bill-payment campaigns are separate.
    period = r'活動期間：(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})適用卡別：街口聯名卡'
    starts = list(re.finditer(period, text))
    active = []
    for i, match in enumerate(starts):
        section = text[match.start():starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        if '【一般消費享' not in section:
            continue
        try:
            validity(section, period, now)
        except ValueError:
            continue
        active.append(section)
    if len(active) != 1:
        raise ValueError('Missing or ambiguous active general-spend campaign')
    text = active[0]
    rate = float(one(r'【一般消費享([0-9]+(?:\.[0-9]+)?)%街口幣無上限】', text))
    references = re.findall(r'一般消費：依每筆消費金額逐筆以([0-9]+(?:\.[0-9]+)?)%計算回饋', text)
    if not references or any(float(value) != rate for value in references):
        raise ValueError('Missing or conflicting general-spend calculation')
    expiry = validity(text,
        r'活動期間：交易日(\d{4})/(\d{1,2})/(\d{1,2})~(\d{4})/(\d{1,2})/(\d{1,2})之消費適用本活動辦法', now)
    if any(term not in text for term in ['交易當下和回饋當下', '四捨五入至個位數', '街口APP繳費交易不適用']):
        raise ValueError('Missing binding, rounding or bill-payment scope')
    return ordinary_observation('jkopay_card', '台新街口聯名卡', '台新銀行', source, rate, expiry,
        ['交易及回饋當下均須有效綁定街口卡', '超商與 Apple 媒體服務限街口綁定消費',
        '不含街口 APP 繳費、精選通路及新戶加碼', '一般消費逐筆四捨五入至整數街口幣'], ['jkoPay', 'linePay'])


ADAPTERS = {'ctbc_linepay': ctbc, 'ctbc_linepay_debit': ctbc_debit,
            'esun_ubear': ubear, 'fubon_j': fubon_j, 'cathay_cube': cube_fixed_domestic,
            'taishin_gogo': taishin_richart, 'taishin_richart_debit': richart_debit, 'jkopay_card': jko_card,
            'federal_jihe': federal_jihe, 'esun_unicard': unicard,
            'fubon_costco': fubon_costco, 'sinopac_sport': sport}


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
