"""Daily discovery of official payment-app campaigns and three-tier classification (V2-S10).

Discovery never publishes a reward. Tier A settles an item only from checkable
facts; tier B keyword rules only suggest a classification for a person to
confirm; tier C is left to a person. A tier-A rule that contradicts any
person-confirmed item (the golden sample) is demoted to a suggestion.
"""
import hashlib
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

from pipeline import fetch_api, fetch_raw, normalize
from rules_v2 import canonical_json

TIERS = ('A', 'B', 'C')
# Whole-run time budget so discovery can never push the daily job past its timeout.
DISCOVERY_BUDGET_SECONDS = 300


class BudgetExceeded(Exception):
    pass


def _check(expired):
    if expired is not None and expired():
        raise BudgetExceeded('discovery time budget exceeded')
# Words that make a stated "location" a website or app rather than a place.
ONLINE_GUARD = re.compile(r'網站|APP|App|app|電商|線上|網購|https?://')
# A nationwide "location" restricts nothing, so it is never settled as a place (card §5.5 「非全國」).
NATIONWIDE_GUARD = re.compile(r'全國|不限')
PLATFORM_MARKS = ('已結束', '(活動額滿)', '（活動額滿）', '(已額滿)', '（已額滿）')
# Official wording that the reward needs the customer's payment code (reverse scan),
# which this project does not cover. Read from the official target/terms text.
REVERSE_SCAN = re.compile(r'出示[^。]{0,15}付款(?:條)?碼|被掃交易|付款條碼(?:進行)?支付')
FORWARD_SCAN = re.compile(r'掃碼|主掃|掃描')
# Only wording that excludes every TWQR stand; 「其他支付之／其他機構」 limits which stands count.
TWQR_EXCLUDED = re.compile(r'不包含掃描TWQR立牌|不適用於透過該商店TWQR機制|如掃描TWQR立牌交易')

# Tier B: suggestions only. Each rule names the words it matched.
SUGGESTIONS = [
    ('suggest.overseas', 'outOfScope:跨境／海外', r'日本|韓國|PayPay|跨境|海外|でお支払い'),
    ('suggest.online', 'outOfScope:線上通路', r'網站|電商|購物網|網購|官網購物|eShop|博客來|friDay|91APP|PChome|momo|MyCard|Apple Rewards|電舖|手機點餐|線上'),
    ('suggest.transit', 'outOfScope:乘車', r'搭乘|捷運|公車|乘車碼|捷客'),
    ('suggest.contactless', 'outOfScope:感應／加值', r'嗶|自動加值|加值|感應'),
    ('suggest.transfer', 'outOfScope:轉帳', r'轉帳'),
    ('suggest.onboarding', 'outOfScope:新戶／任務', r'新戶|首綁|首次綁定|新會員|註冊|任務|挑戰賽|集點|推薦'),
    ('suggest.lottery', 'outOfScope:抽獎', r'抽獎|抽'),
    ('suggest.subsidy', 'outOfScope:政府補助', r'補助'),
    ('suggest.bank', 'deferred:V2-S8', r'銀行|Richart|玉山|聯名卡|e point|刷卡金|uniopen|信用卡|永豐|兆豐'),
    ('suggest.place', 'deferred:V2-S9', r'商圈|夜市|市場|老街|金門|套餐|特價|加價購|週年慶|指定餐廳|便利商店|藥局|中油|加油|門市|百貨|^\s*【[^】]+】'),
]


def load_sources(root):
    return json.loads((Path(root) / 'discovery_sources.json').read_text())


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _strip(value):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', value or '')).strip()


def _taiwanpay(source, getter, poster, pause, expired=None):
    _check(expired)
    listing = poster(source['listUrl'], {'body': {}})
    body = listing.get('body') or {}
    seen = {}
    for key in ('normalCampaigns', 'recommendedCampaigns'):
        for entry in body.get(key) or []:
            seen.setdefault(entry['systemSeq'], entry)
    if not seen:
        raise ValueError('listing yielded no campaigns')
    items = []
    for sid in seen:
        _check(expired)
        item = {'platform': source['id'], 'id': sid, 'sourceUrl': source['pageBase'] + sid}
        try:
            detail = poster(source['detailUrl'], {'body': {'systemSeq': sid}})['body']['campaignDetail']
            terms = _strip(detail.get('target')) + ' ' + _strip(detail.get('content'))
            reverse, forward = REVERSE_SCAN.search(terms), FORWARD_SCAN.search(terms)
            item.update(title=detail.get('title', ''), start=detail.get('startDate'),
                        end=detail.get('endDate') or seen[sid].get('endDate'),
                        scanMode='reverse' if reverse and not forward else ('mixed' if reverse else None),
                        scanEvidence=reverse.group(0) if reverse else None,
                        location=_strip(detail.get('location')), contentSha256=_sha(canonical_json(detail)))
        except Exception as error:
            item.update(title=seen[sid].get('title', ''), end=seen[sid].get('endDate'), location='',
                        contentSha256=None, error=str(error))
        items.append(item)
        if pause:
            time.sleep(pause)
    return items


DATE = r'(20\d\d)[-/](\d{1,2})[-/](\d{1,2})'
RANGE = DATE + r'\S*\s*(?:－|~|～|-|起至|至)\s*' + DATE


def _html_items(source, getter, pause, link, title_pattern, period_pattern, expired=None):
    _check(expired)
    ids = sorted(set(re.findall(link, getter(source['listUrl']))))
    if not ids:
        raise ValueError('listing yielded no campaigns')
    items = []
    for cid in ids:
        _check(expired)
        url = source['detailBase'] + cid
        item = {'platform': source['id'], 'id': cid, 'sourceUrl': url, 'location': ''}
        try:
            text = normalize(getter(url))
            title = re.search(title_pattern, text)
            # Only the period printed at its fixed place counts; a stray body range never does.
            period = re.search(period_pattern, text)
            groups = period.groups()[-6:] if period else None
            excluded = TWQR_EXCLUDED.search(text)
            item.update(title=title.group(1).strip() if title else '',
                        scanMode='nonTwqr' if excluded else None,
                        scanEvidence=excluded.group(0) if excluded else None,
                        start=f'{groups[0]}-{int(groups[1]):02d}-{int(groups[2]):02d}' if groups else None,
                        end=f'{groups[3]}-{int(groups[4]):02d}-{int(groups[5]):02d}' if groups else None,
                        contentSha256=_sha(text))
        except Exception as error:
            item.update(title='', end=None, contentSha256=None, error=str(error))
        items.append(item)
        if pause:
            time.sleep(pause)
    return items


def _easywallet(source, getter, poster, pause, expired=None):
    # The header period directly follows the title.
    title = r'店家申請Q&A ((?:(?!期間|辦法|[：:]).){2,80}?) '
    return _html_items(source, getter, pause, r'benefit/content(?:\.php)?\?id=(\d+)',
                       title + r'20\d\d-\d\d-\d\d', title + RANGE, expired)


def _icash(source, getter, poster, pause, expired=None):
    return _html_items(source, getter, pause, r'advertMessage/view/id/(\d+)', r'.*活動訊息 (.{2,80}?) 發布時間',
                       r'活動期間\s*[:：]\s*\S{0,2}?' + RANGE, expired)


EXTRACTORS = {'taiwanpay': _taiwanpay, 'easywallet': _easywallet, 'icash': _icash}


def _default_getter(url):
    return fetch_raw(url)


def _default_poster(url, body):
    return fetch_api({'url': url, 'method': 'POST', 'body': body, 'okPointer': '/header/returnCode',
                      'okValue': 'TF0000'})['json']


def collect_discovery(sources, getter=_default_getter, poster=_default_poster, pause=0.3,
                      budget=DISCOVERY_BUDGET_SECONDS, clock=time.monotonic):
    found = {}
    deadline = clock() + budget if budget is not None else None

    def expired():
        return deadline is not None and clock() > deadline
    for source in sources:
        try:
            _check(expired)
            found[source['id']] = {'listUrl': source['listUrl'],
                                   'items': EXTRACTORS[source['kind']](source, getter, poster, pause, expired)}
        except Exception as error:
            found[source['id']] = {'listUrl': source['listUrl'], 'items': [], 'error': str(error)}
    return found


def _fact_rules(item, today):
    """Tier A candidates: (rule id, classification, evidence) from checkable facts only."""
    out = []
    if item.get('end') and item['end'] < today:
        out.append(('ended.period', 'ended', f"官方結束日 {item['end']}"))
    mark = next((m for m in PLATFORM_MARKS if m in item.get('title', '')), None)
    if mark:
        out.append(('ended.platformMark', 'ended', f'官方標題標示「{mark}」'))
    if item.get('scanMode') == 'reverse':
        out.append(('reverseScan.official', 'outOfScope:反掃（出示付款碼）', f"官方條款：「{item.get('scanEvidence')}」"))
    # scanMode 'nonTwqr' (TWQR stands excluded) is recorded but settles nothing: the platform's
    # own non-TWQR stand is still a merchant-presented QR, so scope is not established.
    location = item.get('location') or ''
    if location and not ONLINE_GUARD.search(location) and not NATIONWIDE_GUARD.search(location):
        out.append(('restricted.location', 'deferred:V2-S9', f'官方地點欄位：{location[:200]}'))
    return out


def _suggestions(item):
    """Keyword hits on the title; places also on the official location field (where the
    word "網站" often only names the page that announces store changes)."""
    hits = []
    for rule_id, classification, pattern in SUGGESTIONS:
        fields = [('標題', item.get('title', ''))]
        if rule_id == 'suggest.place':
            fields.append(('地點', item.get('location', '')))
        for label, value in fields:
            match = re.search(pattern, value)
            if match:
                hits.append((rule_id, classification, f'{label}含「{match.group(0).strip()}」'))
                break
    return hits


def classify(items, known, today):
    known_by = {(k['platform'], k['id']): k for k in known}
    # Golden sample: a tier-A rule that disagrees with any confirmed item is disabled.
    # Only the deciding fact counts, judged as of the confirmation date, and only
    # while the content is the one the person saw.
    disabled = set()
    for item in items:
        confirmed = known_by.get((item['platform'], item['id']))
        if confirmed and item.get('contentSha256') and confirmed.get('contentSha256') == item['contentSha256']:
            facts = _fact_rules(item, confirmed.get('at', today))
            if facts and facts[0][1] != confirmed['classification']:
                disabled.add(facts[0][0])
    results = []
    for item in items:
        confirmed = known_by.get((item['platform'], item['id']))
        unchanged = bool(confirmed) and item.get('contentSha256') and confirmed.get('contentSha256') == item['contentSha256']
        failed = bool(item.get('error'))
        result = {**item, 'changed': bool(confirmed) and not unchanged and not failed}
        facts = [f for f in _fact_rules(item, today) if f[0] not in disabled]
        demoted = [f for f in _fact_rules(item, today) if f[0] in disabled]
        if failed and confirmed:
            # A failed fetch is not a content change: keep the person's classification.
            result.update(tier='A', rule='known.fetchError', classification=confirmed['classification'],
                          title=item.get('title') or confirmed.get('title', ''), fetchError=True,
                          evidence=f"細則抓取失敗（{item['error'][:80]}），沿用 {confirmed.get('at', '?')} 的分類")
        elif failed:
            result.update(tier='C', rule=None, classification='unclassified', fetchError=True,
                          evidence=f"細則抓取失敗（{item['error'][:80]}）")
        elif unchanged:
            result.update(tier='A', rule='known.unchanged', classification=confirmed['classification'],
                          evidence=f"內容與 {confirmed.get('by', '?')} {confirmed.get('at', '?')} 分類時相同")
        elif facts:
            rule_id, classification, evidence = facts[0]
            result.update(tier='A', rule=rule_id, classification=classification, evidence=evidence)
        else:
            hits = demoted + _suggestions(item)
            classes = sorted({h[1] for h in hits})
            if len(classes) == 1:
                result.update(tier='B', rule=hits[0][0], classification=classes[0],
                              evidence='；'.join(h[2] for h in hits))
            else:
                result.update(tier='C', rule=None, classification='unclassified',
                              evidence='；'.join(f'{h[0]}→{h[1]}（{h[2]}）' for h in hits) or '無規則命中',
                              suggestions=[{'rule': h[0], 'classification': h[1]} for h in hits])
        results.append(result)
    return {'items': results, 'disabledRules': sorted(disabled)}


def diff(classified, known, previous, today, failed=()):
    """new / changed / gone against the confirmed list, with first-seen dates."""
    previous_items = (previous or {}).get('items', [])
    seen_before = {(i['platform'], i['id']): i.get('firstSeen') for i in previous_items}
    known_keys = {(k['platform'], k['id']) for k in known}
    items = []
    for item in classified['items']:
        key = (item['platform'], item['id'])
        items.append({**item, 'firstSeen': seen_before.get(key) or today})
    # A failed platform keeps yesterday's items (and their first-seen dates) instead of resetting them.
    carried = [{**i, 'carried': True} for i in previous_items if i.get('platform') in failed]
    items += carried
    current = {(i['platform'], i['id']) for i in items}
    return {
        'generatedFor': today,
        'items': items,
        'disabledRules': classified['disabledRules'],
        'carried': carried,
        'new': [i for i in items if (i['platform'], i['id']) not in known_keys],
        'changed': [i for i in items if i.get('changed')],
        'gone': [k for k in known if (k['platform'], k['id']) not in current and k['platform'] not in failed],
    }


def summary_markdown(report, sources, today):
    stale = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    lines = ['## 活動自動發現', '']
    for platform, value in sorted(sources.items()):
        if value.get('error'):
            lines.append(f"- ❌ {platform} 清單抓取失敗：{value['error']}")
    counts = {t: sum(i['tier'] == t for i in report['new'] + report['changed']) for t in TIERS}
    lines.append(f"- 新出現 {len(report['new'])}、內容改變 {len(report['changed'])}、消失 {len(report['gone'])}；"
                 f"待處理中 A 自動定案 {counts['A']}、B 待確認 {counts['B']}、C 待分類 {counts['C']}")
    if report['disabledRules']:
        lines.append(f"- 已停用的自動規則（與人工分類不符）：{', '.join(report['disabledRules'])}")
    for label, group in (('待確認（B）', 'B'), ('待分類（C）', 'C')):
        pending = [i for i in report['new'] + report['changed'] if i['tier'] == group]
        if pending:
            lines += ['', f'### {label}']
            for i in pending:
                warn = ' ⚠ 待分類超過 7 天' if i['firstSeen'] <= stale else ''
                lines.append(f"- {i['platform']} {i['id']}｜{i.get('title', '')}｜建議 {i['classification']}｜"
                             f"{i['evidence']}｜首次 {i['firstSeen']}{warn}")
    if report['gone']:
        lines += ['', '### 已從官方清單消失'] + [f"- {k['platform']} {k['id']}｜{k.get('title', '')}" for k in report['gone']]
    return '\n'.join(lines) + '\n'
