"""Structured CUBE scheme evidence, never executable reward rates or eligibility.

The caller must separately review and approve the complete model fingerprint.
No scheme inventory is wired into the published reward feed by this module.
"""
import hashlib
import json
from pipeline import normalize


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate official model key')
        result[key] = value
    return result


def _pointer(parts):
    return '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def parse_scheme_inventory(data):
    model = json.loads(data, object_pairs_hook=_unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if not isinstance(model, dict) or model.get('title') != 'CUBE卡權益方案' or not isinstance(model.get(':items'), dict):
        raise ValueError('Unsupported CUBE scheme model')
    schemes = []
    anchors = set()
    fields = {'mainTitle', 'paragraphNoticeContent', 'categoryName',
              'text', 'title', 'description', 'noticeContent'}

    def authored(node, path, depth=0):
        if depth > 40:
            raise ValueError('Official model exceeds nesting limit')
        result = []
        if isinstance(node, dict):
            for key in sorted(node):
                value = node[key]
                if key in fields:
                    if not isinstance(value, str):
                        raise ValueError('Invalid authored text type')
                    text = normalize(value)
                    if text:
                        result.append({'field': key, 'path': _pointer(path + [key]), 'text': text})
                elif isinstance(value, (dict, list)):
                    result.extend(authored(value, path + [key], depth + 1))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                result.extend(authored(value, path + [index], depth + 1))
        return result

    def visit(node, path, depth=0):
        if depth > 40:
            raise ValueError('Official model exceeds nesting limit')
        if isinstance(node, dict):
            if 'mainTitle' in node:
                anchor = node.get('anchorKey')
                notice = node.get('paragraphNoticeContent')
                title = node['mainTitle']
                if not isinstance(anchor, str) or not anchor.strip() or anchor in anchors or not isinstance(title, str) or not normalize(title) or not isinstance(notice, str) or not normalize(notice):
                    raise ValueError('Missing or duplicated scheme identity/terms')
                entries = authored(node, path)
                if not any(e['field'] == 'categoryName' for e in entries):
                    raise ValueError('Scheme has no channel categories')
                anchors.add(anchor)
                schemes.append({'anchor': anchor, 'path': _pointer(path),
                                'heading': normalize(title), 'notice': normalize(notice),
                                'authoredFields': entries, 'calculation': None})
            for key in sorted(node):
                if isinstance(node[key], (dict, list)):
                    visit(node[key], path + [key], depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, path + [index], depth + 1)
    visit(model, [])
    if not schemes:
        raise ValueError('No CUBE schemes found')
    digest = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(',', ':'),
                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {'modelSha256': digest, 'schemes': schemes, 'fullCardCoverage': False}


def reviewed_scheme_inventory(data, *, approved_sha256):
    """Withdraw the entire inventory on any model change; never approve a new hash."""
    inventory = parse_scheme_inventory(data)
    if inventory['modelSha256'] != approved_sha256:
        return None
    return inventory
