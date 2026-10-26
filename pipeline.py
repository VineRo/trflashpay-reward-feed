#!/usr/bin/env python3
"""Download official evidence and publish only rules bound to reviewed evidence.

No percentages are inferred from advertising text. A changed page invalidates
its approval until its full eligibility and quota rules have been reviewed.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from rules_v2 import canonical_json, resolve_pointer, section_text  # noqa: F401  (section_text re-exported)

LIMIT = 2 * 1024 * 1024
CUBE_SCHEME_URL = 'https://www.cathay-cube.com.tw/content/cub-aem-cs/zh-tw/cathaybk/personal/product/credit-card/cards/cube-list.model.json'
CUBE_POINT_TERMS_URL = 'https://www.cathay-cube.com.tw/content/cub-aem-cs/zh-tw/cathaybk/personal/event/overview/credit-card/bonus/rule/conditions.model.json'


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def normalize(html):
    parser = VisibleText()
    parser.feed(html)
    return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()


def normalize_aem_model(data, expected_title="CUBE信用卡"):
    """Extract public authored text, excluding metadata, links and executable code."""
    model = json.loads(data)
    if not isinstance(model, dict) or not isinstance(model.get(':items'), dict) or model.get('title') != expected_title:
        raise ValueError('Unsupported official content model')
    fields = {'text', 'title', 'description', 'noticeContent'}
    parts = []
    def visit(value, depth=0):
        if depth > 40:
            raise ValueError('Official model exceeds nesting limit')
        if isinstance(value, dict):
            for key in sorted(value):
                item = value[key]
                if key in fields and isinstance(item, str):
                    parts.append(normalize(item))
                elif isinstance(item, (dict, list)):
                    visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
    visit(model)
    body = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
    # Fingerprint the complete model, including unknown/new authored fields.
    # Parsing only known text fields must never hide a changed eligibility rule.
    version = hashlib.sha256(json.dumps(model,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    return body + ' [official-model-sha256:' + version + ']'


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlparse(req.full_url), urlparse(newurl)
        if (new.scheme != 'https' or new.hostname != old.hostname
                or new.username or new.password):
            raise ValueError('Unapproved redirect: update the source after review')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only HTTPS sources without credentials are allowed')
    request = urllib.request.Request(url, headers={'User-Agent': 'TRFlashPay-Official-Rules-Monitor/1.0'})
    with urllib.request.build_opener(SameHostRedirect()).open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('Official source did not return 200')
        data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('Official source exceeds size limit')
        declared = re.search(rb'<meta[^>]+charset=[\"\']?([a-zA-Z0-9_-]+)', data, re.I)
        encoding = response.headers.get_content_charset() or (
            declared.group(1).decode('ascii') if declared else 'utf-8')
        if parsed.path.endswith('.model.json'):
            titles = {
                '/content/cub-aem-cs/zh-tw/cathaybk/personal/product/credit-card/cards/cube.model.json': 'CUBE信用卡',
                '/content/cub-aem-cs/zh-tw/cathaybk/personal/product/credit-card/cards/i-debit.model.json': 'CUBE 簽帳金融卡',
            }
            if parsed.hostname != 'www.cathay-cube.com.tw' or parsed.path not in titles:
                raise ValueError('Unregistered official content model')
            text = normalize_aem_model(data, expected_title=titles[parsed.path])
        else:
            text = normalize(data.decode(encoding, errors='strict'))
        if len(text) < 200:
            raise ValueError('Source has too little readable text; cannot verify')
        return text


API_BODY_LIMIT = 40 * 1024
API_TIMEOUT = 30
# These sites block headless browsers but serve the full terms to a plain
# download; a browser must never be pointed at them (no disguising, V2-S10).
PLAIN_DOWNLOAD_HOSTS = {'easywallet.easycard.com.tw', 'www.icashpay.com.tw'}


def _https_only(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only HTTPS sources without credentials are allowed')
    return parsed


def _api_transport(url, method, data, headers, timeout):
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.build_opener(SameHostRedirect()).open(request, timeout=timeout) as response:
        return response.status, response.read(LIMIT + 1)


def _json_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _json_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_strings(child)
    elif isinstance(value, str):
        yield value


def fetch_api(source, transport=_api_transport):
    """Call a registered official JSON API (V2-S10): no login, cookies or custom auth."""
    _https_only(source['url'])
    method = source.get('method', 'GET')
    if method not in ('GET', 'POST'):
        raise ValueError('API method must be GET or POST')
    if not isinstance(source.get('okPointer'), str) or 'okValue' not in source:
        raise ValueError('API source needs okPointer and okValue')
    data = None
    headers = {'User-Agent': 'TRFlashPay-Official-Rules-Monitor/1.0', 'Accept': 'application/json'}
    if method == 'POST':
        data = json.dumps(source.get('body', {}), ensure_ascii=False).encode()
        if len(data) > API_BODY_LIMIT:
            raise ValueError('API request body exceeds limit')
        headers['Content-Type'] = 'application/json'
    status, raw = transport(source['url'], method, data, headers, API_TIMEOUT)
    if status != 200:
        raise ValueError('Official API did not return 200')
    if len(raw) > LIMIT:
        raise ValueError('Official API response exceeds size limit')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ValueError('Official API did not return JSON')
    try:
        ok = resolve_pointer(value, source['okPointer'])
    except ValueError:
        raise ValueError('Official API status field missing')
    if ok != source['okValue']:
        raise ValueError(f'Official API reported failure ({ok!r})')
    text = re.sub(r'\s+', ' ', ' '.join(normalize(s) for s in _json_strings(value))).strip()
    return {'json': value, 'text': text}


def fetch_raw(url):
    """An official page's HTML as served (discovery listings), with the same HTTPS and size limits."""
    _https_only(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'TRFlashPay-Official-Rules-Monitor/1.0'})
    with urllib.request.build_opener(SameHostRedirect()).open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('Official source did not return 200')
        data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('Official source exceeds size limit')
        return data.decode(response.headers.get_content_charset() or 'utf-8', errors='strict')


CHROME_CANDIDATES = ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser',
                     '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
RENDER_TIMEOUT = 60


def find_chrome():
    """The preinstalled Chrome (GitHub runners ship one); TRFLASHPAY_CHROME overrides."""
    for name in (os.environ.get('TRFLASHPAY_CHROME'),) + CHROME_CANDIDATES:
        if name and (shutil.which(name) or (os.path.isabs(name) and os.access(name, os.X_OK))):
            return shutil.which(name) or name
    return None


def render_chrome(url, chrome=None, runner=subprocess.run, finder=find_chrome, timeout=RENDER_TIMEOUT):
    """Render an official page that builds its text with JavaScript (V2-S6).

    Uses the preinstalled Chrome command line only; any failure is raised as a
    source error so the rules citing it are withdrawn, never guessed.
    """
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only HTTPS sources without credentials are allowed')
    chrome = chrome or finder()
    if not chrome or runner is None:
        raise ValueError('Chrome is not available to render this official source')
    with tempfile.TemporaryDirectory() as profile:
        cmd = [chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
               '--disable-extensions', '--mute-audio', f'--user-data-dir={profile}',
               '--virtual-time-budget=15000', '--timeout=30000']
        if sys.platform.startswith('linux'):
            # Ubuntu 24.04 runners block the unprivileged user-namespace sandbox.
            cmd.append('--no-sandbox')
        cmd += ['--dump-dom', url]
        try:
            done = runner(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError('Official source render timed out') from None
    if done.returncode != 0:
        raise ValueError(f'Official source render failed (exit {done.returncode})')
    if len(done.stdout) > LIMIT:
        raise ValueError('Official source exceeds size limit')
    try:
        html = done.stdout.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('Rendered official source is not valid UTF-8') from None
    text = normalize(html)
    if len(text) < 200:
        raise ValueError('Source has too little readable text; cannot verify')
    return text


def fetch_proof(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Only explicit HTTPS document sources are allowed')
    request = urllib.request.Request(url, headers={'User-Agent': 'TRFlashPay-Official-Rules-Monitor/1.0'})
    with urllib.request.build_opener(SameHostRedirect()).open(request, timeout=20) as response:
        data = response.read(LIMIT + 1)
        if response.status != 200 or len(data) > LIMIT:
            raise ValueError('Official supporting source is unavailable or too large')
        return supporting_proof(url, data)


def supporting_proof(url, data):
    """Only explicitly registered models may supply structured evidence."""
    if len(data) > LIMIT:
        raise ValueError('Official supporting source exceeds size limit')
    if url == CUBE_SCHEME_URL:
        from cube_scheme_inventory import parse_scheme_inventory
        inventory = parse_scheme_inventory(data)
        return {'sha256': inventory['modelSha256'], 'format': 'cubeSchemeModel',
                'byteCount': len(data)}
    if url == CUBE_POINT_TERMS_URL:
        from cube_point_terms import parse_point_terms
        terms = parse_point_terms(data)
        return {'sha256': terms['modelSha256'], 'format': 'cubePointTermsModel',
                'byteCount': len(data)}
    if not data.startswith(b'%PDF-'):
        raise ValueError('Official terms document is unavailable or not a bounded PDF')
    return {'sha256': hashlib.sha256(data).hexdigest(), 'format': 'pdf', 'byteCount': len(data)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')
        name = file.name
    os.replace(name, path)


def collect(sources, downloader=fetch, proof_downloader=fetch_proof, renderer=None, api_fetcher=None):
    result, documents, proofs = {}, {}, {}
    renderer = renderer or render_chrome
    api_fetcher = api_fetcher or fetch_api
    for source in sources:
        cid = source['cardId']
        if cid in result:
            raise ValueError('Duplicate source card ID')
        url = source['url']
        mode = source.get('render')
        if mode not in (None, 'chrome'):
            raise ValueError('Unknown render mode')
        api = source.get('fetch')
        if api not in (None, 'api') or (api and mode):
            raise ValueError('Unknown fetch mode, or fetch combined with render')
        if mode and urlparse(url).hostname in PLAIN_DOWNLOAD_HOSTS:
            raise ValueError('This site blocks headless browsers; register it for plain download')
        key = (url, mode, api, json.dumps(source.get('body'), sort_keys=True))
        if key not in documents:
            try:
                if api:
                    fetched = api_fetcher(source)
                    documents[key] = {'url': url, 'json': fetched['json'], 'text': fetched['text'], 'fetch': 'api',
                                      'sha256': hashlib.sha256(canonical_json(fetched['json']).encode()).hexdigest()}
                else:
                    text = (renderer if mode else downloader)(url)
                    documents[key] = {'url': url, 'text': text,
                                      'sha256': hashlib.sha256(text.encode()).hexdigest()}
                if mode:
                    documents[key]['render'] = mode
            except Exception as error:
                documents[key] = {'url': url, 'error': str(error)}
        # Cards sharing a terms page use one consistent source snapshot.
        result[cid] = dict(documents[key])
        support = source.get('supportingUrls', [])
        if not isinstance(support,list) or len(support)>5 or any(not isinstance(u,str) for u in support) or len(set(support))!=len(support):
            raise ValueError('Invalid supporting source registry')
        result[cid]['supportingSources'] = {}
        for proof_url in support:
            if proof_url not in proofs:
                try:
                    proofs[proof_url] = {'url': proof_url, **proof_downloader(proof_url)}
                except Exception as error:
                    proofs[proof_url] = {'url': proof_url, 'error': str(error)}
            result[cid]['supportingSources'][proof_url] = dict(proofs[proof_url])
    return result


def compile_rules(approved, evidence, now=None):
    now = now or datetime.now(timezone.utc)
    if not approved:
        raise ValueError('No reviewed issuer rules; publishing is disabled')
    rules, ids = [], set()
    for entry in approved:
        rule = entry['rule']
        cid = rule['cardId']
        if cid in ids:
            raise ValueError('Duplicate approved card ID')
        ids.add(cid)
        source = evidence.get(cid, {})
        if (not source.get('sha256') or source['sha256'] != entry['sourceSha256']
                or source['url'] != rule['officialUrl']):
            raise ValueError(f'{cid}: source changed, unavailable, or not reviewed')
        expiry = datetime.fromisoformat(rule['validUntil'].replace('Z', '+00:00'))
        if expiry.tzinfo is None or expiry <= now:
            raise ValueError(f'{cid}: expired or ambiguous validity')
        # Import the same publishing validator shipped with this service.
        from validator import validate
        validate(rule)
        rules.append(rule)
    return rules


def load_platform_registry(root):
    """Official payment-app sources for schema v2 platform programs (V2-S6)."""
    path = Path(root) / 'platform_sources.json'
    return json.loads(path.read_text()) if path.exists() else []


def load_platform_map(root):
    """Payment app -> platform program, mirroring the App's platformPrograms (V2-S10)."""
    return json.loads((Path(root) / 'platform_map.json').read_text())


def build_feed(approved, evidence, coverage, now=None, wallet_evidence=None, programs=None, platform_evidence=None,
               platform_map=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(coverage, list) or not coverage or len(set(coverage)) != len(coverage):
        raise ValueError('Invalid managed catalog')
    rules, pending, ids = [], {}, set()
    for entry in approved:
        cid = entry['rule']['cardId']
        if cid not in coverage or cid in ids:
            raise ValueError('Unknown or duplicate approval')
        ids.add(cid)
        try:
            rules.extend(compile_rules([entry], evidence, now))
        except (ValueError, KeyError, TypeError) as error:
            pending[cid] = str(error)
    from adapters import observe
    observations, observation_errors = observe(evidence, now)
    accepted = {rule['cardId'] for rule in rules}
    for observation in observations:
        if observation['cardId'] not in coverage:
            raise ValueError('Adapter card missing from managed catalog')
        if observation['cardId'] not in accepted:
            rules.append(observation)
            pending.pop(observation['cardId'], None)
    for cid, message in observation_errors.items():
        if cid in coverage and cid not in accepted:
            pending[cid] = message
    verified = {rule['cardId'] for rule in rules}
    for cid in coverage:
        if cid not in verified:
            from pending_status import pending_reason
            pending.setdefault(cid, pending_reason(cid, evidence.get(cid, {})))
    from assessment_profiles import build_profiles, validate_profile
    profiles = build_profiles(evidence, rules, coverage)
    for cid, profile in profiles.items():
        validate_profile(profile, cid)
    from wallet_campaigns import build_wallet_campaigns
    campaigns, campaign_pending = build_wallet_campaigns(wallet_evidence or {}, now)
    # Schema v2 programs ride alongside the v1 feed: installed apps require
    # schemaVersion 1 and ignore unknown top-level keys.
    v2 = {}
    if programs is not None:
        from rules_v2 import PROGRAMS_SCHEMA_VERSION, build_programs
        platform_evidence = platform_evidence or {}
        if set(platform_evidence) & set(evidence):
            raise ValueError('Platform source IDs must not shadow card sources')
        published, program_pending = build_programs(programs, {**evidence, **platform_evidence}, now)
        v2 = {'programsSchemaVersion': PROGRAMS_SCHEMA_VERSION, 'rewardUnits': programs['rewardUnits'],
              'merchantSets': programs['merchantSets'], 'programs': published, 'programPending': program_pending}
        if platform_map is not None:
            # Every payment app x funding cell must be modeled, withdrawn or reasoned.
            from coverage_table import build_platform_coverage
            v2['platformCoverage'] = build_platform_coverage(programs, published, program_pending, platform_map,
                                                             as_of=now.isoformat())
    return {
        **v2,
        'walletCampaigns': campaigns, 'walletCampaignPending': campaign_pending,
        'assessmentProfiles': profiles,
        'schemaVersion': 1,
        'revision': int(now.timestamp() * 1000000),
        'generatedAt': now.isoformat(),
        'expiresAt': (now + timedelta(hours=36)).isoformat(),
        'coverage': coverage,
        'rules': rules,
        'pending': pending,
    }


DISCOVERY_PREVIOUS_URL = 'https://vinero.github.io/trflashpay-reward-feed/discovery.json'


def run_discovery(root, generated_at, collector=None, previous_loader=None):
    """Campaign discovery for people to classify; it never changes the rules and never fails the run."""
    import discovery
    today = generated_at[:10]
    try:
        today = datetime.fromisoformat(generated_at).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        sources = (collector or discovery.collect_discovery)(discovery.load_sources(root))
        known = json.loads((Path(root) / 'discovery_known.json').read_text())
        try:
            previous = (previous_loader or (lambda: json.loads(fetch_raw(DISCOVERY_PREVIOUS_URL))))()
        except Exception:
            previous = None
        items = [i for v in sources.values() for i in v['items']]
        report = discovery.diff(discovery.classify(items, known, today), known, previous, today,
                                failed={k for k, v in sources.items() if v.get('error')})
        summary = discovery.summary_markdown(report, sources, today)
    except Exception as error:
        sources, report = {}, {'generatedFor': today, 'error': f'discovery failed: {error}'}
        summary = f'## 活動自動發現\n\n- ❌ 發現流程失敗：{error}\n'
    step = os.environ.get('GITHUB_STEP_SUMMARY')
    if step:
        try:
            with open(step, 'a', encoding='utf-8') as file:
                file.write(summary)
        except OSError:
            pass
    return report, sources


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--root', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    root = args.root
    registry = json.loads((root / 'sources.json').read_text())
    from rules_v2 import check_sources, load_catalog, validate_catalog
    programs = load_catalog(root / 'programs')
    validate_catalog(programs)
    platform_registry = load_platform_registry(root)
    check_sources(programs, registry + platform_registry)
    platform_map = load_platform_map(root)
    evidence = collect(registry)
    write_json(root / 'artifacts' / 'evidence.json', evidence)
    platform_evidence = collect(platform_registry)
    write_json(root / 'artifacts' / 'platform_evidence.json', platform_evidence)
    wallet_evidence = collect(json.loads((root / 'wallet_sources.json').read_text()))
    write_json(root / 'artifacts' / 'wallet_evidence.json', wallet_evidence)
    if args.publish:
        feed = build_feed(json.loads((root / 'approved.json').read_text()), evidence,
                          json.loads((root / 'catalog.json').read_text()), wallet_evidence=wallet_evidence,
                          programs=programs, platform_evidence=platform_evidence, platform_map=platform_map)
        platform_coverage = feed.pop('platformCoverage')
        from eligibility_audit import build_eligibility_audit
        audit = build_eligibility_audit(feed, evidence)
        write_json(root / 'public' / 'eligibility-audit.json', audit)
        write_json(root / 'public' / 'rules.json', feed)
        write_json(root / 'public' / 'coverage.json', {'generatedAt': feed['generatedAt'], **platform_coverage})
        # Discovery runs only after the feed is on disk, under its own time budget.
        discovery_report, discovery_sources = run_discovery(root, feed['generatedAt'])
        write_json(root / 'public' / 'discovery.json', discovery_report)
        write_json(root / 'public' / 'status.json', {
            'generatedAt': feed['generatedAt'], 'expiresAt': feed['expiresAt'],
            'publishedRuleCount': len(feed['rules']),
            'confirmedRuleCount': sum(not r.get('verificationRequiredReason') for r in feed['rules']),
            'eligibilityPending': [r['cardId'] for r in feed['rules'] if r.get('verificationRequiredReason')],
            'coverageCount': len(feed['coverage']), 'pending': feed['pending'],
            'sourceCount': len(evidence),
            'walletCampaignCount': len(feed['walletCampaigns']),
            'walletCampaignPending': feed['walletCampaignPending'],
            'walletSourceCount': len(wallet_evidence),
            'walletSources': {sid:{'url':v['url'],'sha256':v.get('sha256'),'error':v.get('error')} for sid,v in wallet_evidence.items()},
            'assessmentProfileCount': len(feed['assessmentProfiles']),
            'conditionalBasicPlanCount': sum(p['calculation'] is not None and p['calculation']['rounding'] != 'notModeled' for p in feed['assessmentProfiles'].values()),
            'conditionalRateOnlyPlanCount': sum(p['calculation'] is not None and p['calculation']['rounding'] == 'notModeled' for p in feed['assessmentProfiles'].values()),
            'fullyModeledCardCount': 0,
            'readableSourceCount': sum(bool(value.get('text')) for value in evidence.values()),
            'platformSources': {sid: {'url': v['url'], 'render': v.get('render'), 'fetch': v.get('fetch'),
                                      'sha256': v.get('sha256'), 'error': v.get('error')}
                                for sid, v in platform_evidence.items()},
            'platformCoverage': platform_coverage['summary'],
            'discovery': {'new': len(discovery_report.get('new', [])), 'changed': len(discovery_report.get('changed', [])),
                          'gone': len(discovery_report.get('gone', [])),
                          'sourceErrors': {k: v['error'] for k, v in discovery_sources.items() if v.get('error')},
                          'error': discovery_report.get('error')},
            'sources': {cid: {'url': value['url'], 'sha256': value.get('sha256'),
                               'error': value.get('error'),
                               'supportingSources': value.get('supportingSources', {})}
                        for cid, value in evidence.items()},
        })
        # Changed sources revoke their rules in the new feed rather than leave
        # an old precise offer online indefinitely. Pending cards remain explicit.
    elif any('error' in item for item in evidence.values()):
        raise SystemExit('Some official sources could not be verified; see evidence.json')


if __name__ == '__main__':
    main()
