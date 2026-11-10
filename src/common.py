"""Shared local paths, translation format and validation. No network calls."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

VERSION = '1.0.0'
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'resources'

def app_dir(name):
    root = Path(os.environ.get('LRKO_WORK_DIR') or Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LocalRealmKorean' / name)
    root.mkdir(parents=True, exist_ok=True)
    return root

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def atomic_json(path, value):
    atomic_bytes(Path(path), json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))

def atomic_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def validate_rows(rows, baseline=None):
    if not isinstance(rows, list) or not rows: raise ValueError('번역 목록이 비어 있거나 형식이 잘못되었습니다.')
    errors, ids, sources = [], set(), set()
    known = {r['id']: r['source'] for r in baseline} if baseline is not None else None
    for r in rows:
        if not isinstance(r, dict) or not all(isinstance(r.get(k), str) for k in ('id', 'source', 'translation')):
            errors.append('문구 형식 오류'); continue
        identifier, source, translation = r['id'], r['source'], r['translation']
        if identifier in ids or source in sources: errors.append(identifier+': 중복 ID/원문')
        ids.add(identifier); sources.add(source)
        if known is not None and known.get(identifier) != source: errors.append(identifier+': 지원하지 않는 ID/원문')
        if not translation.strip(): continue
        if sorted(re.findall(r'\{\w+\}', source)) != sorted(re.findall(r'\{\w+\}', translation)):
            errors.append(identifier+': 자리표시자 불일치')
        if sorted(re.findall(r'</?[a-z][^>]*>', source, re.I)) != sorted(re.findall(r'</?[a-z][^>]*>', translation, re.I)):
            errors.append(identifier+': HTML 서식 태그 불일치')
        if '\0' in translation: errors.append(identifier+': NUL 문자')
    if known is not None and ids != set(known): errors.append('번역 파일의 문구 목록이 이 패치 버전과 다릅니다.')
    if errors: raise ValueError('\n'.join(errors[:20]))
    return rows

def load_translations(path=None):
    baseline = read_json(RESOURCE_ROOT / 'catalog.json')
    if path is None: return validate_rows(baseline)
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024: raise ValueError('번역 파일이 너무 큽니다(최대 16 MB).')
    value = read_json(path)
    if isinstance(value, dict):
        if value.get('format') != 'localrealm-korean-translations-v1': raise ValueError('지원하지 않는 번역 형식입니다.')
        if value.get('game_sha256') != read_json(RESOURCE_ROOT / 'release.json')['game_sha256']:
            raise ValueError('지원 게임 버전이 다른 번역 파일입니다.')
        value = value.get('entries')
    return validate_rows(value, baseline)

def translation_package(rows):
    validate_rows(rows, read_json(RESOURCE_ROOT / 'catalog.json'))
    return {'format': 'localrealm-korean-translations-v1', 'version': VERSION,
            'game_sha256': read_json(RESOURCE_ROOT / 'release.json')['game_sha256'],
            'exported_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'entries': rows}

def initialize_review_project():
    root = app_dir('Reviewer')
    data = root / 'data'; data.mkdir(exist_ok=True)
    if not (data/'catalog.json').exists(): atomic_json(data/'catalog.json', read_json(RESOURCE_ROOT/'catalog.json'))
    if not (data/'qa.json').exists(): atomic_json(data/'qa.json', [])
    return root, data
