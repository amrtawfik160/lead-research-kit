#!/usr/bin/env python3
"""Check a public source tree, without printing any suspected secret values."""
import json
import re
import sys
from pathlib import Path

SKIP={'.git','__pycache__','.venv','venv','build','dist'}
PRIVATE_NAMES={'credentials.json','secrets.json','provider-state.json','research.sqlite3','providers.json'}
PRIVATE_SUFFIXES={'.sqlite','.sqlite3','.db','.log','.p12','.pfx','.pem','.key'}
EMAIL=re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+)")
RESERVED=('example.com','example.org','example.net','example','test','invalid','localhost')
PATTERNS={
 'host_specific_path':re.compile('/' + r'Users/[^/\s]+/|' + '/' + r'home/[^/\s]+/|[A-Za-z]:\\' + r'Users\\'),
 'jwt_literal':re.compile(r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),
 'known_token_literal':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{30,})\b'),
 'credential_literal':re.compile(r'''(?i)(?:api[_-]?key|access[_-]?token|password|secret)["']?\s*[:=]\s*["']([A-Za-z0-9_./+=-]{24,})["']'''),
}

def check(root):
 root=Path(root).resolve();findings=[];count=0
 for p in sorted(root.rglob('*')):
  rel=p.relative_to(root)
  if any(part in SKIP or part.endswith('.egg-info') for part in rel.parts):continue
  name=p.name
  if p.is_symlink():findings.append({'path':str(rel),'rule':'symlink_not_public_source'});continue
  if not p.is_file():continue
  count+=1
  example='examples' in rel.parts
  if name in PRIVATE_NAMES and not example or p.suffix in PRIVATE_SUFFIXES or name.endswith(('-wal','-shm')) or (name.startswith('.env') and name!='.env.example'):
   findings.append({'path':str(rel),'rule':'private_runtime_file'});continue
  if p.suffix in {'.csv','.json','.jsonl'} and not example:
   findings.append({'path':str(rel),'rule':'data_outside_synthetic_examples'});continue
  try:text=p.read_text(encoding='utf-8')
  except UnicodeError:findings.append({'path':str(rel),'rule':'unexpected_binary'});continue
  if name=='.env.example':
   if any('=' in line and line.split('=',1)[1].strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')):
    findings.append({'path':str(rel),'rule':'filled_environment_template'})
  for rule,pattern in PATTERNS.items():
   for match in pattern.finditer(text):
    if rule=='credential_literal' and any(marker in match.group(1).upper() for marker in ('SYNTHETIC','EXAMPLE','TEST','PLACEHOLDER')):continue
    findings.append({'path':str(rel),'rule':rule});break
  for match in EMAIL.finditer(text):
   domain=match.group(1).lower().rstrip('.')
   if any(domain==d or domain.endswith('.'+d) for d in RESERVED):continue
   # Security tests contain invalid URL userinfo on an official hostname, not contact data
   prefix=text[max(0,match.start()-80):match.start()]
   if rel.parts[0]=='tests' and re.search(r'https?://[^\s\'"<>]*:$',prefix):continue
   findings.append({'path':str(rel),'rule':'non_synthetic_email_literal'});break
  if example and p.suffix in {'.json','.jsonl'}:
   try:
    items=[json.loads(l) for l in text.splitlines() if l.strip()] if p.suffix=='.jsonl' else json.loads(text)
    if isinstance(items,list) and any(isinstance(r,dict) and 'name' in r and r.get('synthetic') is not True for r in items):
     findings.append({'path':str(rel),'rule':'contact_example_not_marked_synthetic'})
   except (ValueError,TypeError):findings.append({'path':str(rel),'rule':'invalid_example_data'})
 return {'passed':not findings,'files_checked':count,'findings':findings,'scope':'source-tree guardrail, not Git-history or universal secret certification'}

if __name__=='__main__':
 result=check(sys.argv[1] if len(sys.argv)>1 else '.')
 print(json.dumps(result,indent=2))
 raise SystemExit(0 if result['passed'] else 2)
