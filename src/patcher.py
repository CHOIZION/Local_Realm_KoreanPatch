"""Small GUI for end users: find game, apply Korean, restore original."""
from __future__ import annotations
import argparse
import ctypes
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from common import RESOURCE_ROOT, VERSION, app_dir, atomic_json, load_translations, read_json
from engine import install, resolve_game, recover_original, manifest

def discover_games():
    roots=[]
    if os.name=='nt':
        import winreg
        for hive,key,value in [(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam','SteamPath'),(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam','InstallPath')]:
            try:
                with winreg.OpenKey(hive,key) as k: roots.append(Path(winreg.QueryValueEx(k,value)[0]))
            except OSError: pass
    roots.append(Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Steam')
    libraries=list(roots)
    for root in roots:
        vdf=root/'steamapps/libraryfolders.vdf'
        if vdf.exists():
            text=vdf.read_text(encoding='utf-8-sig',errors='replace')
            libraries.extend(Path(m.replace('\\\\','\\')) for m in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"',text))
    result=[]; seen=set()
    for root in libraries:
        game=root/'steamapps/common/Local Realm/local-realm.exe'
        key=str(game).casefold()
        if game.is_file() and key not in seen: result.append(game);seen.add(key)
    return result

class Patcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Local Realm 한글패치 · '+VERSION);self.geometry('780x520');self.minsize(690,480)
        self.folder=app_dir('Patcher');self.config_path=self.folder/'settings.json'
        try:config=read_json(self.config_path)
        except (OSError,ValueError):config={}
        found=discover_games();initial=config.get('game','')
        if not initial or not Path(initial).is_file(): initial=str(found[0]) if found else ''
        self.game=tk.StringVar(value=initial);self.pack=tk.StringVar(value='기본 포함 번역 사용')
        self.translation_path=None;self.busy=False;self.events=queue.Queue();self.widgets=[]
        self.status=tk.StringVar(value='게임을 종료한 뒤 한글패치 적용을 누르세요.')
        style=ttk.Style(self);style.theme_use('clam');style.configure('.',font=('맑은 고딕',11))
        style.configure('TButton',padding=(15,9));style.configure('Accent.TButton',background='#155e75',foreground='white',font=('맑은 고딕',13,'bold'))
        frame=ttk.Frame(self,padding=25);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Local Realm 한글패치',font=('맑은 고딕',23,'bold')).pack(anchor='w')
        ttk.Label(frame,text='게임 폴더 확인 → 한글패치 적용 → 평소처럼 게임 실행',foreground='#526579').pack(anchor='w',pady=(7,22))
        ttk.Label(frame,text='게임 실행 파일').pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(6,16))
        entry=ttk.Entry(row,textvariable=self.game);entry.pack(side='left',fill='x',expand=True);self.widgets.append(entry)
        self.button(row,'찾아보기',self.browse).pack(side='left',padx=(8,0))
        self.button(row,'자동 찾기',self.detect).pack(side='left',padx=(6,0))
        ttk.Label(frame,text='사용할 번역').pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(6,18))
        ttk.Label(row,textvariable=self.pack,wraplength=440).pack(side='left',fill='x',expand=True)
        self.button(row,'번역 파일 선택…',self.choose_pack).pack(side='left')
        self.button(row,'기본 번역',self.reset_pack).pack(side='left',padx=(6,0))
        self.button(frame,'한글패치 적용',lambda:self.start('apply'),'Accent.TButton').pack(fill='x',pady=(0,12))
        row=ttk.Frame(frame);row.pack(fill='x')
        self.button(row,'게임 실행',self.launch).pack(side='left')
        self.button(row,'원본으로 복원',self.restore).pack(side='left',padx=8)
        self.button(row,'사용 안내',lambda:os.startfile(RESOURCE_ROOT/'Patcher.txt')).pack(side='right')
        self.progress=ttk.Progressbar(frame,mode='indeterminate');self.progress.pack(fill='x',pady=(19,8))
        ttk.Label(frame,textvariable=self.status,wraplength=700,foreground='#354e61').pack(anchor='w')
        ttk.Label(frame,text='원본 자동 백업 · 게임 버전 확인 · 저장된 세계관은 수정하지 않습니다',foreground='#687985').pack(anchor='w',pady=(12,0))
        self.after(100,self.poll);self.protocol('WM_DELETE_WINDOW',self.close)

    def button(self,parent,text,command,style='TButton'):
        w=ttk.Button(parent,text=text,command=command,style=style);self.widgets.append(w);return w
    def browse(self):
        file=filedialog.askopenfilename(title='Local Realm의 local-realm.exe 선택',filetypes=[('Local Realm','local-realm.exe')])
        if file:self.game.set(file)
    def detect(self):
        found=discover_games()
        if found:self.game.set(str(found[0]));self.status.set('Steam 라이브러리에서 게임을 찾았습니다.')
        else:messagebox.showinfo('게임 위치','자동으로 찾지 못했습니다. 찾아보기에서 local-realm.exe를 선택하세요.')
    def choose_pack(self):
        file=filedialog.askopenfilename(title='검수 프로그램에서 내보낸 번역 JSON',filetypes=[('번역 JSON','*.json')])
        if not file:return
        try:rows=load_translations(file)
        except Exception as e:messagebox.showerror('번역 파일 오류',str(e));return
        self.translation_path=file;self.pack.set(f'{Path(file).name} · {len(rows):,}개 문구')
    def reset_pack(self):self.translation_path=None;self.pack.set('기본 포함 번역 사용')
    def restore(self):
        if messagebox.askyesno('원본 복원','실행 파일을 지원 버전의 원본으로 복원할까요?\n세계관과 검수 파일은 유지됩니다.'):self.start('restore')
    def start(self,action):
        if self.busy:return
        try:
            game=resolve_game(self.game.get());rows=load_translations(self.translation_path) if action=='apply' else None
            atomic_json(self.config_path,{'game':str(game)})
        except Exception as e:messagebox.showerror('준비 실패',str(e));return
        self.busy=True;self.status.set('파일 확인 및 처리 중… 잠시 기다려주세요.');self.progress.start(12)
        for w in self.widgets:w.configure(state='disabled')
        def work():
            try:self.events.put((action,install(game,rows,restore=action=='restore'),None))
            except PermissionError:
                self.events.put((action,None,'게임 폴더에 쓸 권한이 없거나 파일이 사용 중입니다.\n게임을 종료하고 이 도구를 마우스 오른쪽 클릭 → 관리자 권한으로 실행해 주세요.'))
            except Exception as e:self.events.put((action,None,str(e)))
        threading.Thread(target=work,daemon=True).start()
    def poll(self):
        try:
            action,result,error=self.events.get_nowait();self.busy=False;self.progress.stop()
            for w in self.widgets:w.configure(state='normal')
            if error:self.status.set('처리하지 못했습니다. 안내를 확인하세요.');messagebox.showerror('처리 실패',error)
            else:
                text='원본 복원 완료.' if action=='restore' else f"한국어 {result['translated']:,}개 적용 완료. 게임을 실행하세요."
                self.status.set(text);messagebox.showinfo('완료',text+'\n\n원본 백업: '+result['backup'])
        except queue.Empty:pass
        self.after(100,self.poll)
    def launch(self):
        try:
            game=resolve_game(self.game.get());subprocess.Popen([str(game)],cwd=game.parent)
        except Exception as e:messagebox.showerror('실행 실패',str(e))
    def close(self):
        if self.busy:messagebox.showinfo('처리 중','패치 작업이 끝난 뒤 종료하세요.')
        else:self.destroy()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-report',type=Path);parser.add_argument('--engine-test-game',type=Path)
    args=parser.parse_args()
    if args.engine_test_game and not args.smoke_report:parser.error('--engine-test-game에는 --smoke-report가 필요합니다.')
    if args.engine_test_game:
        # Explicit developer verification entry point, using a supplied copy only.
        before=args.engine_test_game.read_bytes();original=recover_original(before)
        first=install(args.engine_test_game,load_translations());second=install(args.engine_test_game,restore=True)
        if args.engine_test_game.read_bytes()!=original:raise RuntimeError('Executable round-trip failed')
        atomic_json(args.smoke_report,{'ok':True,'apply':first,'restore':second});return
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:pass
    app=Patcher()
    if args.smoke_report:
        app.after(700,lambda:(atomic_json(args.smoke_report,{'ok':True,'app':'patcher','version':VERSION,'translations':len(load_translations()),'tk':tk.TkVersion}),app.destroy()))
    app.mainloop()

if __name__=='__main__':
    try:main()
    except Exception:
        log=app_dir('Patcher')/'error.log';log.write_text(traceback.format_exc(),encoding='utf-8')
        messagebox.showerror('시작 실패',f'오류 기록: {log}')
