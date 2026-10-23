"""Export todos to UTF-8 BOM CSV, merging user fields by (source, job_key)."""
import argparse
import csv
import os
from pathlib import Path
from workflow import read

MACHINE = ['source', 'job_key', 'company', 'job_name', 'location', 'salary', 'job_url',
           'action', 'priority', 'reason', 'state', 'questions', 'in_current_view']
HUMAN = ['user_choice', 'user_notes']


def key(row):
    if not row.get('source') or not row.get('job_key'):
        raise ValueError('CSV 合并需要 source 和稳定 job_key')
    return row['source'], row['job_key']


def safe_cell(value):
    text = str(value if value is not None else '')
    # Only machine-origin text is escaped. Human cells are preserved byte-for-value.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


def export(bundle, path, machine_fields=None):
    machine_fields = machine_fields or MACHINE
    path = Path(path); previous = {}; extras = []
    if path.exists():
        with path.open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not {'source', 'job_key'} <= set(reader.fieldnames):
                raise ValueError('旧 CSV 缺少身份列，停止以免丢失人工编辑')
            extras = [f for f in reader.fieldnames if f.startswith('user_') and f not in HUMAN]
            for row in reader:
                identity = key(row)
                if identity in previous:
                    raise ValueError('旧 CSV 存在重复岗位键，先解决冲突')
                previous[identity] = row
    fields = machine_fields + HUMAN + extras
    rows = []; seen = set()
    current = bundle['todos']
    archived = bundle.get('archived_todos', [])
    for active, items in ((True, current), (False, archived)):
        for todo in items:
            identity = key(todo)
            if identity in seen:
                raise ValueError('JSON 存在重复岗位键，停止导出')
            seen.add(identity)
            row = {f: safe_cell(todo.get(f, '')) for f in machine_fields}
            row['questions'] = safe_cell(' | '.join(todo.get('questions', [])))
            row['in_current_view'] = 'true' if active else 'false'
            row.update({f: previous.get(identity, {}).get(f, '') for f in HUMAN + extras})
            rows.append(row)
    for identity, old in previous.items():
        if identity not in seen:
            # Retain removed rows, including empty notes, instead of silently deleting selections.
            rows.append(dict(old, in_current_view='false'))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows); stream.flush(); os.fsync(stream.fileno())
    tmp.replace(path)
    return len(rows)


JOB_FIELDS = ['source','job_key','company','job_name','location','salary','experience','education',
              'company_info','keyword','jd_description','detail_state','detail_read_error',
              'jd_read_at','job_url','in_current_view']


def export_jobs(jobs, path):
    """Raw acquisition export, explicitly not a recommendation/application queue."""
    return export({'todos': jobs}, path, JOB_FIELDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--todos'); inputs.add_argument('--jobs')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    from runtime import writer_lock
    with writer_lock(Path(args.out).resolve().parent):
        print(export_jobs(read(args.jobs), args.out) if args.jobs else export(read(args.todos), args.out))


if __name__ == '__main__':
    main()
