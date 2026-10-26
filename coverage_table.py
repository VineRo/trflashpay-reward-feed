"""Payment app x funding source coverage: every cell is modeled, withdrawn or reasoned (V2-S10).

The table is derived from the published programs, so it cannot claim a reward
the feed does not carry. A cell with no rule and no stated reason is an error.
"""

FUNDINGS = ('storedBalance', 'bankAccount', 'debitCard', 'creditCard')


def _routes(node):
    """Every route condition inside a rule's condition tree."""
    if isinstance(node, dict):
        if 'route' in node and isinstance(node['route'], dict):
            yield node['route']
        for value in node.values():
            yield from _routes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _routes(value)


def _covers(rule, platform, funding):
    for route in _routes(rule.get('when')):
        if platform in route.get('platform', [platform]) and funding in route.get('funding', [funding]):
            return True
    return False


def build_platform_coverage(catalog, published, pending, platform_map, as_of=None):
    """Return {'cells': [...], 'summary': {...}}; raise ValueError for any unclassified cell."""
    if not isinstance(platform_map, dict) or len(platform_map) != 10 or len(set(platform_map.values())) != 10:
        raise ValueError('platform map must name every payment app exactly once (10 apps)')
    programs = {p['id']: p for p in catalog['programs']}
    live = {p['id']: p for p in published}
    cells, problems = [], []
    for platform, program_id in sorted(platform_map.items()):
        program = programs.get(program_id)
        if program is None or program_id not in live:
            problems.append(f'{platform}: no program {program_id}')
            continue
        priced = [r for r in program['rules'] if r.get('status') != 'uncovered']
        published_ids = {r['id'] for r in live[program_id]['rules']}
        notes = program.get('fundingNotes', {})
        if priced and '*' in notes:
            problems.append(f'{platform}: a priced program may not use a wildcard reason')
        default = next((r['notes'] for r in program['rules'] if r.get('status') == 'uncovered'), None)
        for funding in FUNDINGS:
            rules = [r for r in priced if _covers(r, platform, funding)]
            cell = {'platform': platform, 'funding': funding, 'programId': program_id, 'asOf': as_of,
                    'sources': sorted({e['url'] for e in program['evidence']})}
            if any(r['id'] in published_ids for r in rules):
                cell.update(status='modeled', rules=sorted(r['id'] for r in rules if r['id'] in published_ids))
            elif rules:
                reasons = [pending.get(f"{program_id}/{r['id']}", 'withdrawn') for r in rules]
                cell.update(status='withdrawn', rules=sorted(r['id'] for r in rules), reason='; '.join(reasons))
            else:
                reason = notes.get(funding) or (None if priced else notes.get('*') or default)
                if not reason:
                    problems.append(f'{platform}/{funding}: no rule and no stated reason in {program_id}')
                    continue
                cell.update(status='reasoned', reason=reason)
            cells.append(cell)
    if problems:
        raise ValueError('platform coverage incomplete: ' + '; '.join(problems))
    summary = {s: sum(c['status'] == s for c in cells) for s in ('modeled', 'withdrawn', 'reasoned')}
    return {'cells': cells, 'summary': summary}
