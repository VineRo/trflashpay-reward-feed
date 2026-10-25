"""Manual fixture builder from saved reviewed evidence; no download or publish.

Run from the project root with PYTHONPATH=reward_feed. Dart tests rebase only
transport lease/revision fields; bank periods and formula evidence stay fixed.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from pipeline import CUBE_SCHEME_URL, CUBE_POINT_TERMS_URL, build_feed, normalize_aem_model, supporting_proof
from assessment_profiles import APPROVALS, SUPPORT_APPROVALS


def main():
    root = Path(__file__).resolve().parents[2]
    evidence_dir = root/'audit/c02_cube_overseas_20261024'
    primary_data = (evidence_dir/'cube.model.json').read_bytes()
    secondary_data = (evidence_dir/'cube-list.model.json').read_bytes()
    text = normalize_aem_model(primary_data)
    sha = hashlib.sha256(text.encode()).hexdigest()
    proof = supporting_proof(CUBE_SCHEME_URL, secondary_data)
    point_data = (root/'audit/c02_cube_point_terms_20261024/conditions.model.json').read_bytes()
    point_proof = supporting_proof(CUBE_POINT_TERMS_URL, point_data)
    assert sha == APPROVALS['cathay_cube']
    assert proof['sha256'] == SUPPORT_APPROVALS['cathay_cube'][CUBE_SCHEME_URL]
    assert point_proof['sha256'] == SUPPORT_APPROVALS['cathay_cube'][CUBE_POINT_TERMS_URL]
    source = {'url': 'https://www.cathay-cube.com.tw/content/cub-aem-cs/zh-tw/cathaybk/personal/product/credit-card/cards/cube.model.json',
              'text': text, 'sha256': sha, 'supportingSources': {
                  CUBE_SCHEME_URL: proof, CUBE_POINT_TERMS_URL: point_proof}}
    changed = json.loads(secondary_data)
    changed['contractTestUnknownEligibilityField'] = 'unreviewed change'
    changed_proof = supporting_proof(CUBE_SCHEME_URL, json.dumps(changed).encode())
    supports = source['supportingSources']
    variants = {'valid': source, 'supportChanged': {**source, 'supportingSources': {**supports, CUBE_SCHEME_URL: changed_proof}},
                'supportMissing': {**source, 'supportingSources': {**supports, CUBE_SCHEME_URL: {'error': 'test timeout'}}},
                'pointTermsChanged': {**source, 'supportingSources': {**supports, CUBE_POINT_TERMS_URL: {'sha256': '0'*64}}},
                'pointTermsMissing': {**source, 'supportingSources': {CUBE_SCHEME_URL: proof}},
                'pointTermsUnavailable': {**source, 'supportingSources': {**supports, CUBE_POINT_TERMS_URL: {'error': 'test timeout'}}},
                'primaryUnavailable': {'url': source['url'], 'error': 'test primary unavailable'}}
    coverage = json.loads((root/'reward_feed/catalog.json').read_text())
    now = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
    feeds = {name: build_feed([], {'cathay_cube': copy.deepcopy(value)}, coverage, now)
             for name, value in variants.items()}
    for name, feed in feeds.items():
        assert (feed['assessmentProfiles']['cathay_cube']['calculation'] is not None) == (name == 'valid')
    result = {'purpose': 'Backend/App contract fixture, not current eligibility or a published feed',
              'snapshotDirectory': str(evidence_dir.relative_to(root)),
              'rawSourceSha256': {'primary': hashlib.sha256(primary_data).hexdigest(),
                                  'support': hashlib.sha256(secondary_data).hexdigest(),
                                  'pointTerms': hashlib.sha256(point_data).hexdigest()},
              'feeds': feeds}
    target = root/'test/fixtures/cube_dependency_feed_contract.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(f'Generated {len(feeds)} backend snapshots, coverage {len(coverage)}, {target.stat().st_size} bytes')


if __name__ == '__main__':
    main()
