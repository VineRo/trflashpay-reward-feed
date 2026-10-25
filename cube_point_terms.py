"""Capture complete point terms without inferring transaction eligibility."""
import hashlib
import json
from pipeline import LIMIT, normalize
from cube_scheme_inventory import _unique_object, _pointer


def parse_point_terms(data):
    if len(data) > LIMIT:
        raise ValueError('Official point terms exceed size limit')
    model = json.loads(data, object_pairs_hook=_unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if (not isinstance(model, dict) or model.get('title') != '小樹點(信用卡) 累積辦法'
            or not isinstance(model.get(':items'), dict)):
        raise ValueError('Unsupported CUBE point terms model')
    credit = []

    def visit(node, path, depth=0):
        if depth > 40:
            raise ValueError('Official point terms exceed nesting limit')
        if isinstance(node, dict):
            # Select the authored credit terms, never the separate debit terms
            # or navigation/page metadata sharing a similar heading.
            if node.get('title') == '小樹點(信用卡)累積辦法' and 'text' in node:
                if not isinstance(node['text'], str):
                    raise ValueError('Invalid credit terms text')
                credit.append({'path': _pointer(path + ['text']), 'text': normalize(node['text'])})
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    visit(value, path + [key], depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, path + [index], depth + 1)
    visit(model, [])
    if len(credit) != 1 or not all(marker in credit[0]['text'] for marker in
            ('活動期間', '參與資格', '一般消費項目排除', '其他經本行公告排除之項目', '未盡事宜')):
        raise ValueError('Missing or ambiguous complete credit terms')
    digest = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(',', ':'),
                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return {'modelSha256': digest, 'creditTerms': credit[0], 'fullCardCoverage': False}
