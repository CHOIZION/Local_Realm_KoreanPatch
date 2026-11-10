"""Version-pinned Tauri asset patcher. Reads assets from the user's own game."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import datetime as dt
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import brotli
from common import RESOURCE_ROOT, atomic_json, read_json, sha, validate_rows

class PatchError(RuntimeError): pass

def manifest(): return read_json(RESOURCE_ROOT/'manifest.json')
def js(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def replace_once(text, before, after):
    if text.count(before) != 1: raise PatchError('게임의 내부 구조가 예상과 다릅니다. 새 버전용 패치가 필요합니다.')
    return text.replace(before, after, 1)

def recover_original(data):
    m = manifest()
    if sha(data) == m['sha256']: return data
    if len(data) <= m['original_size']: raise PatchError('지원하지 않는 게임 버전 또는 변경된 파일입니다.')
    try:
        section_table = m['opt'] + struct.unpack_from('<H', data, m['pe']+20)[0]
        section = section_table + 40 * len(m['sections'])
        if data[section:section+8].rstrip(b'\0') != b'.lrko': raise ValueError()
        if struct.unpack_from('<H', data, m['pe']+6)[0] != len(m['sections'])+1: raise ValueError()
        original = bytearray(data[:m['original_size']])
        header = base64.b64decode(m['original_header'])
        original[:len(header)] = header
        for item in m['original_slices']:
            raw = base64.b64decode(item['bytes']); original[item['offset']:item['offset']+len(raw)] = raw
        if sha(original) != m['sha256']: raise ValueError()
        return bytes(original)
    except (ValueError, struct.error, IndexError):
        raise PatchError('지원하지 않는 파일입니다. 다른 변경 사항은 덮어쓰지 않았습니다.') from None

def asset_text(original, name, m):
    a = next(a for a in m['assets'] if a['name'] == name)
    return brotli.decompress(original[a['offset']:a['offset']+a['length']]).decode('utf-8')

def localized_assets(original, rows):
    m = manifest(); validate_rows(rows, read_json(RESOURCE_ROOT/'catalog.json'))
    dictionary = {r['source']:r['translation'] for r in rows if r['translation'].strip()}
    dictionary_name = '/_app/immutable/chunks/CAtGDqPC.js'
    s = asset_text(original, dictionary_name, m)
    s = replace_once(s, 'Q={en:null,es:Re,ru:xe}', 'Q={en:null,es:Re,ru:xe,ko:'+js(dictionary)+'}')
    s = replace_once(s, 'Le=[{id:"en"', 'Le=[{id:"ko",name:"한국어",locale:"ko-KR"},{id:"en"')
    s = replace_once(s, 'this.apply(a??(n.startsWith("es")?"es":n.startsWith("ru")?"ru":"en"))', 'this.apply(localStorage.getItem("local-realm-ko-language")||"ko")')
    s = replace_once(s, 'async choose(a){this.apply(a),await T.appLanguageSet(a)}', 'async choose(a){this.apply(a);localStorage.setItem("local-realm-ko-language",a);await T.appLanguageSet(a==="ko"?"en":a)}')
    s += '\n' + (RESOURCE_ROOT/'korean-display.js').read_text(encoding='utf-8')
    ui_name = '/_app/immutable/chunks/TmZhAqyv.js'
    u = asset_text(original, ui_name, m)
    # Offsets in the original tooling are UTF-16 code units. Work on UTF-16 bytes,
    # so non-BMP characters in the game cannot shift a patch location in Python.
    encoded = u.encode('utf-16-le')
    for patch in read_json(RESOURCE_ROOT/'render-patches.json'):
        start, end = patch['start']*2, patch['end']*2
        if encoded[start:end].decode('utf-16-le') != patch['before']: raise PatchError('화면 번역 위치 검증 실패')
        encoded = encoded[:start] + patch['after'].encode('utf-16-le') + encoded[end:]
    u = encoded.decode('utf-16-le')
    u = replace_once(u, 'async function S($){if(!l(a))return;const X={...l(a),...$};', 'async function S($){if(!l(a))return;$=globalThis.__localRealmKoreanQA.typeEdit(l(a),$);if(!Object.keys($).length)return;const X={...l(a),...$};')
    u = replace_once(u, 'function Ff(r){return mC.includes(r.kind)||r.kind==="search"&&!r.ref?b(globalThis.__localRealmKoreanQA.display(r,"title")):r.title}', 'function Ff(r){return mC.includes(r.kind)||r.kind==="search"&&!r.ref?b(r.title):globalThis.__localRealmKoreanQA.display(r,"title")}')
    u = replace_once(u, 'ee=H?W.typeById(H)?.name:null;return ee?`${j} → ${ee}`:j', 'ee=H?globalThis.__localRealmKoreanQA.display(W.typeById(H),"name"):null;return ee?`${j} → ${ee}`:j')
    u = replace_once(u, 'title:ni.language==="es"?"Switch to English":"Cambiar a español",hint:b("Language"),run:()=>ni.choose(ni.language==="es"?"en":"es")', 'title:ni.language==="ko"?"Switch to English":"한국어로 전환",hint:b("Language"),run:()=>ni.choose(ni.language==="ko"?"en":"ko")')
    css_name = '/_app/immutable/assets/0.FcAO0f64.css'
    css = asset_text(original, css_name, m) + '\nhtml:lang(ko) body{font-family:"Malgun Gothic","Segoe UI",sans-serif}html:lang(ko) button,html:lang(ko) input,html:lang(ko) select,html:lang(ko) textarea{font-family:inherit}\n'
    changed = {dictionary_name:s, ui_name:u, css_name:css}
    for item in read_json(RESOURCE_ROOT/'overrides.json'):
        row = next((r for r in rows if r['id']==item['id']), None)
        if not row or not row['translation'].strip(): continue
        name = item['asset']
        text = changed.get(name) or asset_text(original, name, m)
        changed[name] = replace_once(text, item['before'], item['template'].replace('{{translationJSON}}', js(row['translation'])))
    return {name:text.encode('utf-8') for name,text in changed.items()}

def build(original, rows):
    m = manifest()
    if sha(original) != m['sha256']: raise PatchError('원본 해시 불일치')
    changed = localized_assets(original, rows)
    b = bytearray(original); opt, pe = m['opt'], m['pe']
    u16 = lambda p: struct.unpack_from('<H', b, p)[0]
    u32 = lambda p: struct.unpack_from('<I', b, p)[0]
    put16 = lambda p,v: struct.pack_into('<H', b, p, v)
    put32 = lambda p,v: struct.pack_into('<I', b, p, v)
    align = lambda value, alignment: (value+alignment-1)//alignment*alignment
    count=u16(pe+6); header=opt+u16(pe+20)+40*count
    if header+40>min(s['raw'] for s in m['sections']): raise PatchError('섹션 헤더 공간 부족')
    file_align, section_align = u32(opt+36), u32(opt+32)
    rva=align(max(s['rva']+max(s['vs'],s['size']) for s in m['sections']),section_align)
    raw=align(len(b),file_align); offset=0; chunks=[]; reports=[]
    for name,data in changed.items():
        a=next(a for a in m['assets'] if a['name']==name)
        packed=brotli.compress(data,quality=9)
        struct.pack_into('<QQ',b,a['entry']+16,m['base']+rva+offset,len(packed))
        reports.append(dict(name=name,offset=raw+offset,length=len(packed),sha256=sha(data)))
        chunks.append(packed); offset+=len(packed)
    size=align(offset,file_align); b[header:header+40]=bytes(40); b[header:header+5]=b'.lrko'
    for delta,value in [(8,offset),(12,rva),(16,size),(20,raw),(36,0x40000040)]: put32(header+delta,value)
    put16(pe+6,count+1); put32(opt+56,align(rva+offset,section_align)); put32(opt+8,u32(opt+8)+size);put32(opt+64,0)
    b[opt+112+32:opt+112+40]=bytes(8)
    output=bytes(b)+bytes(raw-len(b))+b''.join(chunks)+bytes(size-offset)
    for a in reports:
        if sha(brotli.decompress(output[a['offset']:a['offset']+a['length']])) != a['sha256']: raise PatchError('생성된 자산 검증 실패')
    if recover_original(output) != original: raise PatchError('원본 복구 검증 실패')
    return output, dict(original=m['sha256'],patched=sha(output),total=len(rows),translated=sum(bool(r['translation'].strip()) for r in rows),assets=reports)

def game_running():
    if os.name != 'nt': return False
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    tasklist=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/tasklist.exe'
    p=subprocess.run([str(tasklist),'/FI','IMAGENAME eq local-realm.exe','/FO','CSV','/NH'],capture_output=True,creationflags=flags,timeout=15)
    if p.returncode: raise PatchError('게임 실행 여부를 확인하지 못했습니다.')
    return b'"local-realm.exe"' in p.stdout.lower()

def resolve_game(value):
    p=Path(value).expanduser().resolve()
    if p.is_dir(): p=p/'local-realm.exe'
    if p.name.lower()!='local-realm.exe' or not p.is_file(): raise PatchError('Local Realm 폴더 또는 local-realm.exe를 선택하세요.')
    return p

@contextmanager
def game_lock(folder):
    folder.mkdir(parents=True,exist_ok=True)
    with open(folder/'patch.lock','a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise PatchError('다른 패치 작업이 진행 중입니다.') from None
        try: yield
        finally:
            if os.name=='nt': lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def install(game, rows=None, restore=False):
    game=resolve_game(game)
    if game_running(): raise PatchError('게임을 완전히 종료한 뒤 다시 시도하세요.')
    folder=game.parent/'.localrealm-ko-backup'
    with game_lock(folder):
        current=game.read_bytes(); current_hash=sha(current); original=recover_original(current)
        backup=folder/'local-realm.original.exe'
        if backup.exists():
            if sha(backup.read_bytes()) != manifest()['sha256']: raise PatchError('기존 원본 백업이 변경되었습니다. 백업을 덮어쓰지 않았습니다.')
        else:
            with open(backup,'xb') as f: f.write(original);f.flush();os.fsync(f.fileno())
        if restore: output,report=original,{'restored':True,'sha256':sha(original)}
        else: output,report=build(original,rows)
        fd,tmp=tempfile.mkstemp(prefix='local-realm-ko-',suffix='.tmp',dir=game.parent)
        try:
            with os.fdopen(fd,'wb') as f: f.write(output);f.flush();os.fsync(f.fileno())
            if sha(Path(tmp).read_bytes()) != sha(output): raise PatchError('임시 파일 검증 실패')
            if game_running(): raise PatchError('작업 중 게임이 실행되었습니다. 종료하고 다시 시도하세요.')
            if sha(game.read_bytes()) != current_hash: raise PatchError('작업 중 게임 파일이 변경되었습니다. 덮어쓰지 않았습니다.')
            os.replace(tmp,game)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        if sha(game.read_bytes()) != sha(output): raise PatchError('적용 후 파일 검증 실패')
        report.update(backup=str(backup),completed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        try: atomic_json(folder/'last-operation.json',report)
        except OSError: report['log_warning']='적용은 완료했지만 작업 기록을 저장하지 못했습니다.'
        return report
