"""Manual fixture builder: the schema v2 block exactly as build_feed emits it.

Run from the project root with PYTHONPATH=reward_feed. Uses the saved reviewed
CUBE and Costco evidence and the saved Chrome-rendered iPASS text; no download or publish. Dart tests merge this block into the
existing contract feed to prove installed apps still accept the feed.
"""
import json
from pathlib import Path

from pipeline import build_feed
from rules_v2 import load_catalog
from test_rules_v2_cube import NOW, ROOT, reviewed_evidence, reviewed_platform_evidence

KEYS = ('programsSchemaVersion', 'rewardUnits', 'merchantSets', 'programs', 'programPending')


def main():
    feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=load_catalog(ROOT / 'reward_feed/programs'),
                      platform_evidence=reviewed_platform_evidence())
    assert feed['programPending'] == {}, feed['programPending']
    block = {key: feed[key] for key in KEYS}
    target = ROOT / 'test/fixtures/rules_v2_programs_block.json'
    target.write_text(json.dumps(block, ensure_ascii=False, indent=2) + '\n')
    print(f"Generated v2 block with {len(block['programs'])} program(s), {target.stat().st_size} bytes")


if __name__ == '__main__':
    main()
