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
import tempfile
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

LIMIT = 2 * 1024 * 1024


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


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlparse(req.full_url), urlparse(newurl)
        if new.scheme != 'https' or new.hostname != old.hostname:
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
        text = normalize(data.decode(encoding, errors='strict'))
        if len(text) < 200:
            raise ValueError('Source has too little readable text; cannot verify')
        return text


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')
        name = file.name
    os.replace(name, path)


def collect(sources, downloader=fetch):
    result = {}
    for source in sources:
        cid = source['cardId']
        if cid in result:
            raise ValueError('Duplicate source card ID')
        try:
            text = downloader(source['url'])
            result[cid] = {'url': source['url'], 'text': text,
                           'sha256': hashlib.sha256(text.encode()).hexdigest()}
        except Exception as error:
            result[cid] = {'url': source['url'], 'error': str(error)}
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


def build_feed(approved, evidence, coverage, now=None):
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
    verified = {rule['cardId'] for rule in rules}
    for cid in coverage:
        if cid not in verified:
            pending.setdefault(cid, 'Issuer terms have not been reviewed')
    return {
        'schemaVersion': 1,
        'revision': int(now.timestamp() * 1000000),
        'generatedAt': now.isoformat(),
        'expiresAt': (now + timedelta(hours=36)).isoformat(),
        'coverage': coverage,
        'rules': rules,
        'pending': pending,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--root', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    root = args.root
    evidence = collect(json.loads((root / 'sources.json').read_text()))
    write_json(root / 'artifacts' / 'evidence.json', evidence)
    if args.publish:
        feed = build_feed(json.loads((root / 'approved.json').read_text()), evidence,
                          json.loads((root / 'catalog.json').read_text()))
        write_json(root / 'public' / 'rules.json', feed)
        write_json(root / 'public' / 'status.json', {
            'generatedAt': feed['generatedAt'], 'expiresAt': feed['expiresAt'],
            'publishedRuleCount': len(feed['rules']),
            'coverageCount': len(feed['coverage']), 'pending': feed['pending'],
            'sources': {cid: {'url': value['url'], 'sha256': value.get('sha256'),
                               'error': value.get('error')}
                        for cid, value in evidence.items()},
        })
        # Changed sources revoke their rules in the new feed rather than leave
        # an old precise offer online indefinitely. Pending cards remain explicit.
    elif any('error' in item for item in evidence.values()):
        raise SystemExit('Some official sources could not be verified; see evidence.json')


if __name__ == '__main__':
    main()
