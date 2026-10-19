"""Monthly advertised salary checks; unknown amounts never pass automatically."""
import re


def salary_gate(text, minimum, range_mode='lower'):
    text = str(text or '').replace(',', '').replace('，', '')
    if minimum is None:
        return {'status': 'unset'}
    if any('\ue000' <= c <= '\uf8ff' for c in text) or not text.strip():
        return {'status': 'unknown', 'reason': '薪资缺失或网站字体编码'}
    if any(s in text for s in ('/时', '/天', '/年', '年薪', '元/时', '元/天', '日结', '面议')):
        return {'status': 'unknown', 'reason': '非明确固定月薪'}
    match = re.search(r'(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*([kK千万元]?)', text)
    if match:
        low, high = float(match[1]), float(match[2])
        unit = match[3]
    else:
        match = re.search(r'(\d+(?:\.\d+)?)\s*([kK千万])', text)
        if not match:
            match = re.search(r'(\d{4,})\s*元?', text)
        if not match:
            return {'status': 'unknown', 'reason': '无法确定月薪数值'}
        low = high = float(match[1])
        unit = match[2] if len(match.groups()) > 1 else ''
    multiplier = 10000 if unit == '万' else 1000 if unit in ('k','K','千') else 1
    low, high = low * multiplier, high * multiplier
    if high < minimum:
        status = 'below'
    elif low >= minimum:
        status = 'pass'
    else:
        status = 'negotiate' if range_mode == 'upper' else 'below'
    return dict(status=status, lower=low, upper=high, minimum=minimum, range_mode=range_mode)
