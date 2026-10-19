"""Check Skill metadata, links, JSON Schema, and source syntax without website IO."""
import ast
import json
from pathlib import Path
import re
import yaml
from jsonschema import Draft202012Validator

root=Path(__file__).resolve().parents[1]
skill=root/'job-search'
content=(skill/'SKILL.md').read_text(encoding='utf-8')
match=re.match(r'^---\n(.*?)\n---',content,re.S)
assert match,'Missing Skill frontmatter'
metadata=yaml.safe_load(match.group(1))
assert metadata['name']=='job-search' and metadata['description'].strip()
for path in skill.rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path))
for path in skill.rglob('*.json'):
    json.loads(path.read_text(encoding='utf-8-sig'))
for path in [root/'README.md',*skill.rglob('*.md')]:
    for target in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8')):
        if not target.startswith(('https:','http:','#')):
            assert (path.parent/target.split('#')[0]).exists(),(path,target)
schema=json.loads((skill/'assets/review_policy.schema.json').read_text(encoding='utf-8'))
Draft202012Validator.check_schema(schema)
Draft202012Validator(schema).validate(json.loads((skill/'assets/review_policy.template.json').read_text(encoding='utf-8')))
print('Skill metadata, source syntax, JSON, links and policy schema: passed')
