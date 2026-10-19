"""Validate AI analysis against collected jobs and create a local HTML report."""
import argparse
import html
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

ORDER = {'recommended': 0, 'needs_review': 1, 'excluded': 2}
LABELS = {'recommended': '推荐', 'needs_review': '待核实', 'excluded': '排除'}


def validate(analysis, jobs):
    if not isinstance(analysis, list) or not isinstance(jobs, list):
        raise ValueError('analysis 和 jobs 都必须为数组')
    originals = {job['job_key']: job for job in jobs}
    if len(originals) != len(jobs):
        raise ValueError('原始岗位 job_key 重复')
    seen, result = set(), []
    for row in analysis:
        key = row.get('job_key')
        if key not in originals or key in seen:
            raise ValueError(f'未知或重复 job_key: {key}')
        seen.add(key)
        if row.get('status') not in ORDER:
            raise ValueError(f'{key}: status 无效')
        score = row.get('priority_score')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError(f'{key}: priority_score 必须为 0–100 数值')
        if not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError(f'{key}: 缺少分析依据')
        for field in ('gaps', 'risks'):
            if not isinstance(row.get(field), list) or any(not isinstance(v, str) for v in row[field]):
                raise ValueError(f'{key}: {field} 必须是字符串数组')
        # Source facts always come from collection, never from AI rewrites.
        enriched = dict(row)
        for field in ('job_name', 'company', 'salary', 'location', 'job_url', 'source'):
            enriched[field] = originals[key].get(field, '')
        result.append(enriched)
    if seen != set(originals):
        raise ValueError(f'分析未覆盖所有岗位，缺少 {len(set(originals) - seen)} 条')
    return sorted(result, key=lambda r: (ORDER[r['status']], -r['priority_score']))


def render(rows):
    esc = lambda value: html.escape(str(value or '未知'), quote=True)
    body = []
    for row in rows:
        url = str(row.get('job_url') or '')
        label = esc(row['job_name'])
        if urlsplit(url).scheme in ('http', 'https'):
            label = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'
        gaps = '<br>'.join(esc(x) for x in row['gaps']) or '未发现明确缺口'
        risks = '<br>'.join(esc(x) for x in row['risks']) or '暂无已知风险'
        salary = str(row['salary'] or '')
        if any('\ue000' <= char <= '\uf8ff' for char in salary):
            salary = '待核实（网站字体编码）'
        body.append(f'<tr><td>{LABELS[row["status"]]}</td><td>{row["priority_score"]}</td><td>{label}</td>'
                    f'<td>{esc(row["company"])}</td><td>{esc(salary)}</td><td>{esc(row["location"])}</td>'
                    f'<td>{esc(row["source"])}</td><td>{esc(row["reason"])}</td><td>{gaps}</td><td>{risks}</td></tr>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>求职岗位分析</title>
<style>body{font-family:"Microsoft YaHei",sans-serif;background:#f5f7fa;color:#223;padding:24px}
h1{font-size:26px}p{color:#567}.table{overflow:auto;background:white;border-radius:12px}
table{border-collapse:collapse;min-width:1200px;width:100%}th,td{padding:12px;border-bottom:1px solid #dde3ea;text-align:left;vertical-align:top;font-size:14px}
th{background:#eef3f8}a{color:#1764b5}td:nth-child(8){min-width:240px}td:nth-child(9),td:nth-child(10){min-width:160px}</style>
<h1>求职岗位分析</h1><p>按推荐状态及 AI 投递优先级排序。城市沿用网站设置；地址仅展示，不计算通勤分。缺失信息需核实。</p>
<div class="table"><table><thead><tr><th>状态</th><th>优先级</th><th>岗位</th><th>公司</th><th>薪资</th><th>地点</th><th>来源</th><th>匹配依据</th><th>能力缺口</th><th>风险与待核实</th></tr></thead><tbody>''' + ''.join(body) + '</tbody></table></div></html>'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis', required=True)
    p.add_argument('--jobs', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows = validate(read(args.analysis), read(args.jobs))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows), encoding='utf-8')
    output.with_suffix('.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'报告生成成功：{output}（{len(rows)} 条）')


if __name__ == '__main__':
    main()
