"""Local Realm Korean translation workbench. All data stays on this PC."""
from __future__ import annotations
import csv, ctypes, datetime as dt, io, json, os, re, shutil, subprocess, sys, threading, uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from common import RESOURCE_ROOT, VERSION, initialize_review_project, translation_package, load_translations
ROOT, DATA = initialize_review_project()
STATUS = {'draft': '초벌', 'reviewed': '검수 완료', 'qa': 'QA 확인 필요'}
REV_STATUS = {v: k for k, v in STATUS.items()}

def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def atomic_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)

def validate_row(row):
    a, b = row['source'], row['translation']
    if not b.strip():
        return '번역이 비어 있습니다. 게임에서는 원문으로 표시됩니다.'
    if sorted(re.findall(r'\{\w+\}', a)) != sorted(re.findall(r'\{\w+\}', b)):
        return '오류: {name}, {n} 같은 자리표시자가 원문과 다릅니다.'
    if sorted(re.findall(r'</?[a-z][^>]*>', a, re.I)) != sorted(re.findall(r'</?[a-z][^>]*>', b, re.I)):
        return '오류: <code>, <strong> 등의 서식 태그가 원문과 다릅니다.'
    if '\0' in b:
        return '오류: 사용할 수 없는 NUL 문자가 있습니다.'
    return '자리표시자·서식 태그 정상'

class Workbench(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Local Realm 번역 검수 · '+VERSION)
        self.geometry('1320x900')
        self.minsize(1050, 740)
        self.configure(bg='#f3f5f8')
        self.rows = read_json(DATA / 'catalog.json', [])
        self.qa = read_json(DATA / 'qa.json', [])
        self.catalog_stamp = (DATA / 'catalog.json').stat().st_mtime_ns
        self.qa_stamp = (DATA / 'qa.json').stat().st_mtime_ns
        self.current = None
        self.loading = False
        self.busy = False
        self.catalog_dirty = False
        self.search_job = None
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('맑은 고딕', 10))
        style.configure('TFrame', background='#f3f5f8')
        style.configure('TLabel', background='#f3f5f8', foreground='#26344b')
        style.configure('TButton', padding=(12, 7))
        style.configure('Accent.TButton', background='#155e75', foreground='white')
        style.configure('Treeview', rowheight=31, font=('맑은 고딕', 10), background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('맑은 고딕', 10, 'bold'), padding=7)
        style.map('Treeview', background=[('selected', '#d8eaf1')], foreground=[('selected', '#122b39')])
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='LOCAL REALM  /  한국어 번역 검수', font=('맑은 고딕', 19, 'bold')).pack(side='left')
        ttk.Button(header, text='사용 안내', command=self.help).pack(side='right')
        ttk.Label(outer, text='원문 대조 → 문장 검수 → 번역 파일 내보내기 → 적용 도구에서 사용', foreground='#607085').pack(anchor='w', pady=(5, 12))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill='x', pady=(0, 12))
        for text, callback, accent in [('전체 저장  Ctrl+S', self.save, False), ('패치용 번역 내보내기', self.export_translation, True), ('번역 JSON 가져오기', self.import_translation, False), ('작업 폴더', lambda: os.startfile(ROOT), False)]:
            ttk.Button(toolbar, text=text, command=callback, style='Accent.TButton' if accent else 'TButton').pack(side='left', padx=(0, 8))
        self.stats = tk.StringVar()
        ttk.Label(toolbar, textvariable=self.stats).pack(side='right')
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='both', expand=True)
        self.review_tab = ttk.Frame(self.notebook, padding=12)
        self.qa_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.review_tab, text='  원문 · 번역 검수  ')
        self.notebook.add(self.qa_tab, text='  게임 QA / 누락 목록  ')
        self.make_review()
        self.make_qa()
        self.message = tk.StringVar(value='준비 완료 · 수정 후 저장하고, 패치용 번역 내보내기로 적용 도구에 전달하세요.')
        ttk.Label(outer, textvariable=self.message, wraplength=1240, foreground='#486278').pack(side='bottom', before=self.notebook, anchor='w', pady=(10, 0))
        self.bind('<Control-s>', lambda e: self.save())
        self.bind('<Control-Return>', lambda e: self.mark_next())
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        self.refresh_qa()

    def make_review(self):
        top = ttk.Frame(self.review_tab)
        top.pack(fill='x', pady=(0, 10))
        ttk.Label(top, text='검색').pack(side='left', padx=(0, 8))
        self.query = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query, width=44)
        entry.pack(side='left', fill='x', expand=True)
        self.query.trace_add('write', self.schedule_search)
        self.filter = tk.StringVar(value='전체')
        combo = ttk.Combobox(top, state='readonly', textvariable=self.filter, values=['전체', '초벌', '검수 완료', 'QA 확인 필요', '미번역', '검사 오류'], width=15)
        combo.pack(side='left', padx=8)
        combo.bind('<<ComboboxSelected>>', lambda e: self.refresh())
        ttk.Button(top, text='CSV 내보내기', command=self.export_catalog).pack(side='left', padx=3)
        ttk.Button(top, text='CSV 가져오기', command=self.import_catalog).pack(side='left', padx=3)
        panes = ttk.Panedwindow(self.review_tab, orient='vertical')
        panes.pack(fill='both', expand=True)
        table = ttk.Frame(panes)
        self.tree = ttk.Treeview(table, columns=('id','status','en','ko'), show='headings', selectmode='browse', height=12)
        for col, name, width in [('id','ID',80), ('status','상태',115), ('en','영어 원문',465), ('ko','한국어 번역',465)]:
            self.tree.heading(col, text=name)
            self.tree.column(col, width=width, minwidth=50, stretch=col in ('en','ko'))
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        panes.add(table, weight=3)
        editor = ttk.Frame(panes, padding=(0, 12, 0, 0))
        panes.add(editor, weight=2)
        self.current_label = tk.StringVar(value='목록에서 문장을 선택하세요')
        bar = ttk.Frame(editor)
        bar.pack(fill='x', pady=(0, 6))
        ttk.Label(bar, textvariable=self.current_label, font=('맑은 고딕', 10, 'bold')).pack(side='left')
        ttk.Button(bar, text='검수 완료 · 다음  Ctrl+Enter', command=self.mark_next).pack(side='right')
        self.edit_status = tk.StringVar(value='초벌')
        self.status_combo = ttk.Combobox(bar, state='readonly', textvariable=self.edit_status, values=list(STATUS.values()), width=14)
        self.status_combo.pack(side='right', padx=8)
        self.status_combo.bind('<<ComboboxSelected>>', lambda e: self.changed())
        fields = ttk.Frame(editor)
        fields.pack(fill='both', expand=True)
        fields.columnconfigure(0, weight=1)
        fields.columnconfigure(1, weight=1)
        fields.rowconfigure(1, weight=1)
        ttk.Label(fields, text='영어 원문 · 읽기 전용').grid(row=0,column=0,sticky='w',pady=3)
        ttk.Label(fields, text='한국어 번역 · 자유롭게 수정').grid(row=0,column=1,sticky='w',padx=(12,0),pady=3)
        self.source = tk.Text(fields, wrap='word', height=6, font=('맑은 고딕', 11), padx=12, pady=10, relief='flat', bg='#e9edf2')
        self.trans = tk.Text(fields, wrap='word', height=6, font=('맑은 고딕', 11), padx=12, pady=10, relief='solid', bd=1, undo=True)
        self.source.grid(row=1,column=0,sticky='nsew')
        self.trans.grid(row=1,column=1,sticky='nsew',padx=(12,0))
        self.source.configure(state='disabled')
        self.trans.bind('<<Modified>>', self.text_modified)
        note_frame=ttk.Frame(editor)
        note_frame.pack(side='bottom',before=fields,fill='x',pady=7)
        ttk.Label(note_frame,text='검수 메모').pack(side='left',padx=(0,8))
        self.note=tk.StringVar()
        ttk.Entry(note_frame,textvariable=self.note).pack(side='left',fill='x',expand=True)
        self.note.trace_add('write',lambda *a:self.changed())
        self.validation=tk.StringVar()
        ttk.Label(editor,textvariable=self.validation,foreground='#a04e27').pack(side='bottom',before=note_frame,anchor='w')

    def make_qa(self):
        ttk.Label(self.qa_tab,text='영어 원문을 한 줄씩 적거나 TXT / CSV / JSON 목록을 가져오세요. 기존 문구는 연결하고, 처음 보는 문구는 추가 분석 대상으로 보관합니다.',wraplength=1150).pack(anchor='w',pady=(0,8))
        controls=ttk.Frame(self.qa_tab);controls.pack(fill='x')
        ttk.Button(controls,text='QA 목록 가져오기',command=self.import_qa).pack(side='left',padx=(0,8))
        ttk.Button(controls,text='QA 목록 내보내기',command=self.export_qa).pack(side='left',padx=(0,8))
        ttk.Button(controls,text='연결된 번역 열기',command=self.open_qa_translation).pack(side='left',padx=(0,8))
        ttk.Button(controls,text='해결 / 미해결 전환',command=self.toggle_qa).pack(side='left')
        table=ttk.Frame(self.qa_tab);table.pack(fill='both',expand=True,pady=10)
        self.qa_tree=ttk.Treeview(table,columns=('state','source','location','note'),show='headings',selectmode='browse')
        for col,name,width in [('state','처리 상태',160),('source','발견한 영어 문구',420),('location','발견 위치',180),('note','QA 메모',350)]:
            self.qa_tree.heading(col,text=name);self.qa_tree.column(col,width=width)
        scroll=ttk.Scrollbar(table,command=self.qa_tree.yview);self.qa_tree.configure(yscrollcommand=scroll.set)
        self.qa_tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.qa_tree.bind('<Double-1>',lambda e:self.open_qa_translation())
        ttk.Label(self.qa_tab,text='새 QA 기록 · 영어 문구를 줄마다 하나씩 입력').pack(anchor='w')
        self.qa_input=tk.Text(self.qa_tab,height=4,wrap='word',font=('맑은 고딕',11))
        self.qa_input.pack(fill='x',pady=5)
        row=ttk.Frame(self.qa_tab);row.pack(fill='x')
        ttk.Label(row,text='발견 위치').pack(side='left')
        self.qa_location=tk.StringVar();ttk.Entry(row,textvariable=self.qa_location,width=30).pack(side='left',padx=8)
        ttk.Label(row,text='메모').pack(side='left')
        self.qa_note=tk.StringVar();ttk.Entry(row,textvariable=self.qa_note).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(row,text='QA 기록 추가',command=self.add_qa_text).pack(side='left')
        ttk.Label(self.qa_tab,text='추가 분석 대상은 아직 게임에 연결되지 않은 문구입니다. QA 목록을 전달해주시면 코드 위치를 확인해 번역 대상으로 추가합니다.',foreground='#607085',wraplength=1150).pack(anchor='w',pady=9)

    def schedule_search(self,*_):
        if self.search_job:self.after_cancel(self.search_job)
        self.search_job=self.after(200,self.refresh)

    def commit_editor(self):
        if not self.current or self.loading:return
        r=self.current
        values={'translation':self.trans.get('1.0','end-1c'),'note':self.note.get(),'status':REV_STATUS[self.edit_status.get()]}
        if any(r.get(k,'')!=v for k,v in values.items()):
            r.update(values);self.catalog_dirty=True
            if self.tree.exists(r['id']):self.tree.item(r['id'],values=self.row_values(r))

    def row_values(self,r):
        return (r['id'],STATUS.get(r.get('status'),'초벌'),r['source'].replace('\n',' ⏎ '),r['translation'].replace('\n',' ⏎ '))

    def refresh(self):
        self.search_job=None;self.commit_editor()
        selected=self.current['id'] if self.current else None
        self.loading=True
        self.tree.delete(*self.tree.get_children())
        q=self.query.get().casefold().strip();f=self.filter.get()
        for r in self.rows:
            if q and q not in '\n'.join(str(r.get(k,'')) for k in ('id','source','translation','note')).casefold():continue
            if f in REV_STATUS and r.get('status')!=REV_STATUS[f]:continue
            if f=='미번역' and r['translation'].strip():continue
            if f=='검사 오류' and not validate_row(r).startswith('오류:'):continue
            self.tree.insert('', 'end', iid=r['id'], values=self.row_values(r))
        self.loading=False
        items=self.tree.get_children()
        if items:
            target=selected if selected in items else items[0]
            self.tree.selection_set(target);self.tree.see(target)
            self.select()
        else:self.load_row(None)
        self.update_stats()

    def update_stats(self):
        done=sum(r.get('status')=='reviewed' for r in self.rows)
        self.stats.set(f'전체 {len(self.rows):,}  ·  검수 {done:,}  ·  표시 {len(self.tree.get_children()):,}')

    def select(self,event=None):
        if self.loading:return
        sel=self.tree.selection()
        if not sel:return
        r=next(r for r in self.rows if r['id']==sel[0])
        if r is self.current:return
        self.commit_editor();self.load_row(r)

    def load_row(self,r):
        self.loading=True;self.current=r
        self.source.configure(state='normal');self.source.delete('1.0','end');self.trans.delete('1.0','end')
        if r:
            self.source.insert('1.0',r['source']);self.trans.insert('1.0',r['translation'])
        self.source.configure(state='disabled')
        self.trans.edit_reset();self.trans.edit_modified(False)
        self.note.set(r.get('note','') if r else '')
        self.edit_status.set(STATUS.get(r.get('status'),'초벌') if r else '초벌')
        self.current_label.set(f"{r['id']} · {r.get('origin','게임 언어 사전')}" if r else '검색 결과가 없습니다')
        self.validation.set(validate_row(r) if r else '')
        self.loading=False

    def text_modified(self,event=None):
        if self.trans.edit_modified():
            self.trans.edit_modified(False);self.changed(translation=True)

    def changed(self,translation=False):
        if self.loading or not self.current:return
        if translation and self.edit_status.get()=='검수 완료':self.edit_status.set('초벌')
        self.commit_editor();self.validation.set(validate_row(self.current));self.update_stats()
        self.message.set('수정됨 · Ctrl+S로 저장하세요. 검수가 끝나면 패치용 번역 내보내기를 누르세요.')

    def save(self):
        self.commit_editor()
        try:
            if self.catalog_dirty:
                if (DATA/'catalog.json').stat().st_mtime_ns!=self.catalog_stamp:
                    raise RuntimeError('다른 작업에서 번역 파일이 변경되었습니다. 덮어쓰지 않았습니다. CSV로 현재 내용을 내보낸 후 프로그램을 다시 여세요.')
                folder=ROOT/'backups'/'reviews';folder.mkdir(parents=True,exist_ok=True)
                shutil.copy2(DATA/'catalog.json',folder/(dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json'))
                atomic_json(DATA/'catalog.json',self.rows)
                self.catalog_stamp=(DATA/'catalog.json').stat().st_mtime_ns;self.catalog_dirty=False
            self.message.set('저장 완료 · '+dt.datetime.now().strftime('%H:%M:%S'))
            return True
        except Exception as e:messagebox.showerror('저장 실패',str(e));return False

    def mark_next(self):
        if not self.current:return
        self.commit_editor()
        error=validate_row(self.current)
        if error.startswith('오류:') or not self.current['translation'].strip():messagebox.showerror('검수 표시 불가',error);return
        current_id=self.current['id'];items=list(self.tree.get_children());index=items.index(current_id)
        self.edit_status.set('검수 완료');self.commit_editor()
        if not self.save():return
        if index+1<len(items):self.tree.selection_set(items[index+1]);self.tree.see(items[index+1]);self.select()
        self.update_stats()

    def export_translation(self):
        if not self.save():return
        try:package=translation_package(self.rows)
        except Exception as e:messagebox.showerror('번역 검사 실패',str(e));return
        file=filedialog.asksaveasfilename(defaultextension='.json',initialfile='LocalRealm-Korean-translations.json',filetypes=[('패치용 번역','*.json')])
        if not file:return
        try:
            atomic_json(Path(file),package)
            self.message.set('내보내기 완료 · 한글패치 도구에서 번역 파일 선택으로 불러오세요: '+file)
        except Exception as e:messagebox.showerror('내보내기 실패',str(e))

    def import_translation(self):
        if not self.save():return
        file=filedialog.askopenfilename(filetypes=[('번역 JSON','*.json')])
        if not file:return
        try:rows=load_translations(file)
        except Exception as e:messagebox.showerror('가져오기 실패',str(e));return
        if not messagebox.askyesno('번역 가져오기','현재 검수 내용을 선택한 번역 파일로 바꿀까요? 기존 내용은 백업됩니다.'):return
        self.load_row(None);self.rows=rows;self.catalog_dirty=True;self.refresh();self.save()

    def export_catalog(self):
        self.commit_editor()
        file=filedialog.asksaveasfilename(defaultextension='.csv',initialfile='LocalRealm_번역검수.csv',filetypes=[('CSV','*.csv')])
        if not file:return
        with open(file,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['id','source','translation','status','note','origin'],extrasaction='ignore');writer.writeheader();writer.writerows(self.rows)
        self.message.set('번역 CSV 내보내기 완료: '+file)

    def import_catalog(self):
        if not self.save():return
        file=filedialog.askopenfilename(filetypes=[('CSV','*.csv')])
        if not file:return
        try:
            with open(file,encoding='utf-8-sig',newline='') as f:incoming=list(csv.DictReader(f))
            by_id={r['id']:r for r in self.rows};updates=[]
            for r in incoming:
                old=by_id.get(r.get('id'))
                if not old or r.get('source')!=old['source']:raise ValueError('ID/원문 불일치: '+str(r.get('id')))
                new=dict(old);new.update({k:r[k] for k in ('translation','status','note') if k in r})
                if new['status'] not in STATUS:raise ValueError('알 수 없는 상태: '+new['status'])
                if validate_row(new).startswith('오류:'):raise ValueError(new['id']+' '+validate_row(new))
                updates.append((old,new))
            if len({n['id'] for _,n in updates})!=len(updates):raise ValueError('중복 ID가 있습니다.')
            for old,new in updates:old.update(new)
            self.catalog_dirty=True;self.load_row(None);self.refresh();self.save()
            self.message.set(f'CSV {len(updates)}개 가져오기 완료')
        except Exception as e:messagebox.showerror('가져오기 실패',str(e))

    def persist_qa(self):
        if (DATA/'qa.json').stat().st_mtime_ns!=self.qa_stamp:raise RuntimeError('QA 파일이 외부에서 변경되었습니다. 프로그램을 다시 열어주세요.')
        atomic_json(DATA/'qa.json',self.qa);self.qa_stamp=(DATA/'qa.json').stat().st_mtime_ns

    def add_issues(self,items):
        by_source={r['source']:r for r in self.rows};added=0
        for item in items:
            if isinstance(item,str):item={'source':item}
            source=str(item.get('source','')).strip()
            if not source:continue
            location=str(item.get('location',''))
            if any(q['source']==source and q.get('location','')==location for q in self.qa):continue
            match=by_source.get(source)
            self.qa.append({'id':uuid.uuid4().hex[:12],'source':source,'location':location,'note':str(item.get('note','')),'catalog_id':match['id'] if match else None,'resolved':False,'created':dt.datetime.now().isoformat(timespec='seconds')})
            if match:match['status']='qa';self.catalog_dirty=True
            added+=1
        self.persist_qa();self.load_row(None);self.refresh();self.refresh_qa();self.save()
        self.message.set(f'QA {added}개 추가 · 일치하지 않는 문구는 추가 분석 대상으로 보관했습니다.')

    def add_qa_text(self):
        self.commit_editor()
        try:
            self.add_issues([{'source':s,'location':self.qa_location.get(),'note':self.qa_note.get()} for s in self.qa_input.get('1.0','end-1c').splitlines() if s.strip()])
            self.qa_input.delete('1.0','end')
        except Exception as e:messagebox.showerror('QA 저장 실패',str(e))

    def import_qa(self):
        self.commit_editor()
        file=filedialog.askopenfilename(filetypes=[('QA 목록','*.txt *.csv *.json'),('모든 파일','*.*')])
        if not file:return
        try:
            text=Path(file).read_text(encoding='utf-8-sig')
            if file.lower().endswith('.json'):items=json.loads(text)
            elif file.lower().endswith('.csv'):items=list(csv.DictReader(io.StringIO(text)))
            else:items=text.splitlines()
            if not isinstance(items,list):raise ValueError('JSON은 목록 형식이어야 합니다.')
            self.add_issues(items)
        except Exception as e:messagebox.showerror('QA 가져오기 실패',str(e))

    def export_qa(self):
        file=filedialog.asksaveasfilename(defaultextension='.csv',initialfile='LocalRealm_QA.csv',filetypes=[('CSV','*.csv'),('JSON','*.json')])
        if not file:return
        if file.lower().endswith('.json'):atomic_json(Path(file),self.qa)
        else:
            with open(file,'w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['id','source','location','note','catalog_id','resolved','created'],extrasaction='ignore');writer.writeheader();writer.writerows(self.qa)
        self.message.set('QA 목록 내보내기 완료: '+file)

    def refresh_qa(self):
        self.qa_tree.delete(*self.qa_tree.get_children())
        for q in self.qa:
            state='해결' if q.get('resolved') else ('번역 연결됨' if q.get('catalog_id') else '추가 분석 대상')
            self.qa_tree.insert('','end',iid=q['id'],values=(state,q['source'],q.get('location',''),q.get('note','')))

    def selected_qa(self):
        ids=self.qa_tree.selection()
        return next((q for q in self.qa if ids and q['id']==ids[0]),None)

    def open_qa_translation(self):
        q=self.selected_qa()
        if not q:return
        if not q.get('catalog_id'):messagebox.showinfo('추가 분석 대상','이 문구는 내장 언어 사전에 없습니다. QA 목록을 전달해주시면 코드 위치를 확인하여 추가 번역합니다.');return
        self.query.set(q['catalog_id']);self.filter.set('전체');self.refresh();self.notebook.select(self.review_tab)

    def toggle_qa(self):
        q=self.selected_qa()
        if q:
            q['resolved']=not q.get('resolved');self.persist_qa();self.refresh_qa()

    def help(self):
        os.startfile(RESOURCE_ROOT/'Reviewer.txt')

    def close(self):
        if self.busy:messagebox.showinfo('처리 중','적용 또는 복원이 끝난 뒤 종료하세요.');return
        if self.save():self.destroy()

if __name__=='__main__':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:pass
    # Prevent two editors from overwriting each other's changes.
    lock=open(DATA/'reviewer.lock','a+b')
    try:
        import msvcrt
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        root=tk.Tk();root.withdraw();messagebox.showinfo('이미 실행 중','번역 검수 프로그램이 이미 열려 있습니다.');sys.exit(0)
    try:
        import argparse
        parser=argparse.ArgumentParser();parser.add_argument('--smoke-report',type=Path)
        args=parser.parse_args();app=Workbench()
        if args.smoke_report:
            app.after(700,lambda:(atomic_json(args.smoke_report,{'ok':True,'app':'reviewer','version':VERSION,'translations':len(app.rows),'tk':tk.TkVersion}),app.destroy()))
        app.mainloop()
    except Exception:
        import traceback
        (ROOT/'reviewer-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
