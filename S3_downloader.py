import os, re, queue, threading, urllib.request, urllib.error, sys, sqlite3, subprocess, io, time, json, webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import defaultdict
from PIL import Image, ImageOps, UnidentifiedImageError

BASE = 'https://alfred-dsm-shopify-exports-prod.s3.us-west-2.amazonaws.com/'
GROUPS = [
 ('현악기',[('VN','바이올린'),('VA','비올라'),('VC','첼로'),('SB','스트링베이스')]),
 ('솔로현악기',[('SVN','솔로바이올린'),('SVA','솔로비올라'),('SVC','솔로첼로')]),
 ('목관악기',[('P','피콜로'),('F','플루트'),('O','오보에'),('C','클라리넷'),('AC','알토클라리넷'),('BC','베이스클라리넷'),('B','바순'),('AX','알토색소폰'),('TX','테너색소폰'),('BTX','바리톤색소폰')]),
 ('금관악기',[('H','호른'),('T','트럼펫'),('TN','트롬본'),('TN3TB','트롬본3튜바'),('BTN','베이스트롬본'),('BBC','유포늄 B.C.'),('BTC','유포늄 T.C.'),('TB','튜바')]),
 ('타악기 외',[('M','말렛퍼커션'),('TMP','팀파니'),('PC','퍼커션'),('D','세트드럼'),('SD','스네어드럼'),('DS','드럼세트'),('K','피아노'),('H','하프')]),
]
SINGLE = {'SB','BC','BTX','TMP','K','D','SVN','SVA','SVC','TN3TB','BTN','SD','DS'}
EXTS = ['png']

def natural_key(s):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)',s)]

def make_names(product, selections, parts, pages, extensions, score=False):
    mapping=defaultdict(set)
    if score:
        for page in range(1,pages+1):
            for ext in extensions:
                mapping[f'{product}-{page}.{ext}'].add('스코어')
    else:
        for group,code in selections:
            for part in ([None] if code in SINGLE or (group=='타악기 외' and code=='H') else range(1,parts+1)):
                for page in range(1,pages+1):
                    for ext in extensions:
                        name=f'{product}_{code}{"" if part is None else part}-{page}.{ext}'
                        mapping[name].add(group)
    return mapping

class App:
    def __init__(self,root):
        self.root=root;root.title('AlfDown 6.0 | Simsangso Personal Edition');root.geometry('1260x920');root.minsize(1080,780)
        try:root.iconbitmap(os.path.join(getattr(sys,'_MEIPASS',os.path.dirname(__file__)),'treble.ico'))
        except Exception:pass
        self.configure_theme()
        self.events=queue.Queue();self.stop=threading.Event();self.busy=False
        self.results={};self.downloaded={};self.visible_names=[];self.preview_photo=None;self.custom_order=None
        self.db_path=os.path.join(os.path.expanduser('~'),'.alfdown6_history.sqlite3');self.init_db()
        self.code=tk.StringVar(value='00-PC-0019403');self.part=tk.StringVar(value='4');self.page=tk.StringVar(value='15');self.workers=tk.StringVar(value='6')
        self.settings_path=os.path.join(os.path.expanduser('~'),'.alfdown6_settings.json')
        self.folder=tk.StringVar(value=self.load_last_folder())
        self.folder.trace_add('write',self.on_folder_changed)
        self._folder_save_job=None
        self.root.protocol('WM_DELETE_WINDOW',self.on_close)
        self.score=tk.BooleanVar(value=False);self.auto=tk.BooleanVar(value=True);self.make_parts=tk.BooleanVar(value=True);self.make_combined=tk.BooleanVar(value=True)
        # Main page scrolls independently of the results list.
        self.main_canvas=tk.Canvas(root,bg='#f7f0e5',highlightthickness=0,borderwidth=0)
        self.main_scroll=ttk.Scrollbar(root,orient='vertical',command=self.main_canvas.yview)
        self.main_canvas.configure(yscrollcommand=self.main_scroll.set)
        self.main_scroll.pack(side='right',fill='y')
        self.main_canvas.pack(side='left',fill='both',expand=True)
        outer=ttk.Frame(self.main_canvas,padding=20)
        self.main_window=self.main_canvas.create_window((0,0),window=outer,anchor='nw')
        outer.bind('<Configure>',lambda e:self.main_canvas.configure(scrollregion=self.main_canvas.bbox('all')))
        self.main_canvas.bind('<Configure>',lambda e:self.main_canvas.itemconfigure(self.main_window,width=e.width))
        self.main_canvas.bind_all('<MouseWheel>',self.on_mousewheel,add='+')
        self.main_canvas.bind_all('<Button-4>',self.on_mousewheel,add='+')
        self.main_canvas.bind_all('<Button-5>',self.on_mousewheel,add='+')
        ttk.Label(outer,text='𝄞  AlfDown',style='Hero.TLabel').pack(anchor='w')
        ttk.Label(outer,text='SIMSANGSO EDITION  ·  악기별 악보 검색 / 다운로드 / 맞춤 PDF',style='Subtitle.TLabel').pack(anchor='w')
        row=ttk.Frame(outer);row.pack(fill='x',pady=9)
        for label,var,width in [('상품번호',self.code,28),('파트 최대',self.part,5),('페이지 최대',self.page,5),('동시 요청',self.workers,5)]:
            ttk.Label(row,text=label).pack(side='left',padx=(0,3));ttk.Entry(row,textvariable=var,width=width).pack(side='left',padx=(0,6 if label=='상품번호' else 12))
            if label=='상품번호':
                ttk.Button(row,text='Alfred.com 열기',command=lambda:webbrowser.open('https://www.alfred.com/')).pack(side='left',padx=(0,12))
        ttk.Checkbutton(outer,text='스마트 탐색 (범위 내 존재하는 파트/페이지 자동 확인)',variable=self.auto).pack(anchor='w')
        self.flags={}
        for group,items in GROUPS:
            frame=ttk.LabelFrame(outer,text=group,padding=(12,9));frame.pack(fill='x',pady=3)
            group_grid=ttk.Frame(frame);group_grid.pack(side='left',fill='x',expand=True)
            for index,(code,label) in enumerate(items):
                key=(group,code);var=tk.BooleanVar(value=(group=='현악기'))
                self.flags[key]=var
                ttk.Checkbutton(group_grid,text=f'{code} ({label})',variable=var).grid(row=index//5,column=index%5,sticky='w',padx=(3,14),pady=3)
            ttk.Button(frame,text='그룹 선택/해제',command=lambda g=group:self.toggle_group(g)).pack(side='right')
        controls=ttk.Frame(outer);controls.pack(fill='x',pady=8)
        ttk.Button(controls,text='전체 선택',command=lambda:self.select_all(True)).pack(side='left')
        ttk.Button(controls,text='전체 해제',command=lambda:self.select_all(False)).pack(side='left',padx=5)
        # Only PNG exists on the source; no extension selector is needed.
        self.exts={'png':tk.BooleanVar(value=True)}
        scorebox=ttk.LabelFrame(outer,text='스코어 다운로드 (악기 검색 대신 상품번호-페이지번호 형식)',padding=10);scorebox.pack(fill='x',pady=5)
        ttk.Checkbutton(scorebox,text='스코어 다운 전용',variable=self.score).pack(side='left')
        ttk.Label(scorebox,text='예: 00-PC-0019403-1.png  →  00-PC-0019403-2.png',style='Subtitle.TLabel').pack(side='left',padx=18)
        path=ttk.Frame(outer);path.pack(fill='x',pady=7)
        ttk.Label(path,text='저장 폴더').pack(side='left');ttk.Entry(path,textvariable=self.folder).pack(side='left',fill='x',expand=True,padx=7)
        ttk.Button(path,text='찾아보기',command=self.browse).pack(side='left')
        ttk.Button(path,text='📂 저장 폴더 열기',command=self.open_save_folder).pack(side='left',padx=(8,0))
        actions=ttk.Frame(outer);actions.pack(fill='x',pady=7)
        self.startbtn=ttk.Button(actions,text='검색 시작',command=self.start,style='Accent.TButton');self.startbtn.pack(side='left')
        self.stopbtn=ttk.Button(actions,text='검색 중지',command=self.cancel,state='disabled');self.stopbtn.pack(side='left',padx=7)
        self.dlbtn=ttk.Button(actions,text='발견 파일 다운로드',command=self.download,state='disabled',style='Accent.TButton');self.dlbtn.pack(side='left')
        self.pdfbtn=ttk.Button(actions,text='PDF 만들기',command=self.export_pdf,state='normal');self.pdfbtn.pack(side='left',padx=7)
        ttk.Button(actions,text='미리보기',command=self.preview).pack(side='left',padx=5)
        ttk.Button(actions,text='선택 제거',command=self.remove_selected).pack(side='left',padx=5)
        ttk.Button(actions,text='누락 검사',command=self.report_missing).pack(side='left',padx=5)
        ttk.Button(actions,text='다운로드 기록',command=self.show_history).pack(side='left',padx=5)
        ttk.Button(actions,text='PDF 순서',command=self.configure_order).pack(side='left',padx=5)
        ttk.Checkbutton(outer,text='악기군별 PDF',variable=self.make_parts).pack(anchor='w')
        ttk.Checkbutton(outer,text='전체 합본 PDF',variable=self.make_combined).pack(anchor='w')
        self.status=tk.StringVar(value='대기 중');ttk.Label(outer,textvariable=self.status).pack(anchor='w')
        self.progress=ttk.Progressbar(outer,mode='determinate');self.progress.pack(fill='x',pady=5)
        listing=ttk.Frame(outer);listing.pack(fill='both',expand=True)
        self.listbox=tk.Listbox(listing,font=('Consolas',10),bg='#fffaf2',fg='#514236',selectbackground='#d9b99c',selectforeground='#392c23',relief='flat',borderwidth=0,highlightthickness=0);self.listbox.pack(side='left',fill='both',expand=True);self.listbox.bind('<Double-Button-1>',lambda e:self.preview())
        scrollbar=ttk.Scrollbar(listing,orient='vertical',command=self.listbox.yview);scrollbar.pack(side='right',fill='y');self.listbox.configure(yscrollcommand=scrollbar.set)
        ttk.Label(outer,text='검색은 선택한 패턴의 공개 파일만 확인합니다. 403 접근거부는 파일 없음과 구분합니다.',style='Subtitle.TLabel').pack(anchor='w',pady=7)
        root.after(80,self.pump)
    def on_mousewheel(self,event):
        # Let scrollable popup widgets handle their own wheel events.
        if event.widget.winfo_toplevel() is not self.root:return
        widget=event.widget
        while widget is not None:
            if widget is self.listbox:
                if getattr(event,'num',None)==4:delta=-3
                elif getattr(event,'num',None)==5:delta=3
                else:delta=-int(event.delta/120)*3 if event.delta else 0
                self.listbox.yview_scroll(delta,'units')
                return 'break'
            widget=getattr(widget,'master',None)
        if getattr(event,'num',None)==4:delta=-3
        elif getattr(event,'num',None)==5:delta=3
        else:delta=-int(event.delta/120)*3 if event.delta else 0
        self.main_canvas.yview_scroll(delta,'units')
        return 'break'

    def toggle_group(self,group):
        keys=[k for k in self.flags if k[0]==group];value=not all(self.flags[k].get() for k in keys)
        for k in keys:self.flags[k].set(value)
    def select_all(self,value):
        for var in self.flags.values():var.set(value)
    def load_last_folder(self):
        default=os.path.join(os.path.expanduser('~'),'Downloads','AlfDown')
        try:
            with open(self.settings_path,'r',encoding='utf-8') as f:
                value=json.load(f).get('last_folder','')
            if isinstance(value,str) and value.strip():
                return value
        except (OSError,ValueError,TypeError):
            pass
        return default
    def save_last_folder(self):
        self._folder_save_job=None
        folder=self.folder.get().strip()
        if not folder:return
        tmp=self.settings_path+'.tmp'
        try:
            with open(tmp,'w',encoding='utf-8') as f:
                json.dump({'last_folder':folder},f,ensure_ascii=False,indent=2)
            os.replace(tmp,self.settings_path)
        except OSError:
            try:os.remove(tmp)
            except OSError:pass
    def on_folder_changed(self,*args):
        if self._folder_save_job is not None:
            self.root.after_cancel(self._folder_save_job)
        self._folder_save_job=self.root.after(600,self.save_last_folder)
    def on_close(self):
        if self._folder_save_job is not None:
            self.root.after_cancel(self._folder_save_job)
        self.save_last_folder()
        self.root.destroy()
    def browse(self):
        current=self.folder.get().strip()
        initial=current if os.path.isdir(current) else os.path.expanduser('~')
        p=filedialog.askdirectory(initialdir=initial)
        if p:self.folder.set(p)
    def open_save_folder(self):
        folder=os.path.abspath(os.path.expanduser(self.folder.get().strip())) if self.folder.get().strip() else ''
        if not folder:
            messagebox.showwarning('저장 폴더', '저장 폴더를 지정해주세요.')
            return
        try:
            os.makedirs(folder,exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(folder)
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open',folder])
            else:
                import subprocess
                subprocess.Popen(['xdg-open',folder])
        except Exception as exc:
            messagebox.showerror('폴더 열기 실패', f'저장 폴더를 열 수 없습니다.\n{exc}')

    def validate(self):
        product=self.code.get().strip().rstrip('_')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',product):raise ValueError('상품번호에는 영문, 숫자, -, _만 사용하세요.')
        parts=int(self.part.get());pages=int(self.page.get());workers=int(self.workers.get())
        if not (1<=parts<=100 and 1<=pages<=500 and 1<=workers<=12):raise ValueError('파트 1~100, 페이지 1~500, 동시 요청 1~12만 가능합니다.')
        extensions=[ext for ext,var in self.exts.items() if var.get()]
        if not extensions:raise ValueError('확장자를 하나 이상 선택하세요.')
        selections=[k for k,var in self.flags.items() if var.get()]
        if not self.score.get() and not selections:raise ValueError('악기를 하나 이상 선택하세요.')
        mapping=make_names(product,selections,parts,pages,extensions,self.score.get())
        if len(mapping)>250000:raise ValueError('검색 후보가 25만 개를 넘습니다. 범위를 줄여주세요.')
        return mapping,workers
    def set_busy(self,active):
        self.busy=active;self.startbtn.config(state='disabled' if active else 'normal')
        self.stopbtn.config(state='normal' if active else 'disabled')
        self.dlbtn.config(state='disabled' if active or not self.results else 'normal')
        self.pdfbtn.config(state='disabled' if active else 'normal')
    def start(self):
        if self.busy:return
        try:mapping,workers=self.validate()
        except (ValueError,TypeError) as e:messagebox.showerror('입력 확인',str(e));return
        self.results={};self.downloaded={};self.visible_names=[];self.listbox.delete(0,'end');self.stop.clear();self.set_busy(True)
        self.progress.config(maximum=max(1,len(mapping)),value=0);self.status.set(f'검색 시작: {len(mapping):,}개 후보')
        threading.Thread(target=self.search_worker,args=(mapping,workers),daemon=True).start()
    def cancel(self):
        self.stop.set();self.stopbtn.config(state='disabled');self.status.set('중지 요청됨 — 진행 중인 요청 마무리 중...')
    def check(self,name):
        if self.stop.is_set():return name,'cancelled'
        try:
            req=urllib.request.Request(BASE+name,method='HEAD',headers={'User-Agent':'AlfDown/6.0'})
            with urllib.request.urlopen(req,timeout=6) as response:return name,'found' if response.status==200 else 'error'
        except urllib.error.HTTPError as e:
            return name,('missing' if e.code==404 else 'forbidden' if e.code==403 else 'error')
        except Exception:return name,'error'
    def search_worker(self,mapping,workers):
        done=found=errors=forbidden=0;names=iter(mapping);pending=set()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in range(min(workers*2,len(mapping))):
                try:pending.add(pool.submit(self.check,next(names)))
                except StopIteration:break
            while pending:
                completed,pending=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
                for future in completed:
                    name,status=future.result();done+=1
                    if status=='found':found+=1;self.events.put(('found',(name,mapping[name])))
                    elif status=='error':errors+=1
                    elif status=='forbidden':forbidden+=1
                    if not self.stop.is_set():
                        try:pending.add(pool.submit(self.check,next(names)))
                        except StopIteration:pass
                self.events.put(('progress',(done,len(mapping),found,errors,forbidden)))
        self.events.put(('search_done',(self.stop.is_set(),done,found,errors,forbidden)))
    def download(self):
        if self.busy or not self.results:return
        folder=self.folder.get().strip()
        if not folder:messagebox.showerror('저장 폴더','폴더를 선택하세요.');return
        try:os.makedirs(folder,exist_ok=True)
        except OSError as e:messagebox.showerror('폴더 오류',str(e));return
        self.stop.clear();self.set_busy(True);self.progress.config(maximum=len(self.results),value=0)
        threading.Thread(target=self.download_worker,args=({k:v for k,v in self.results.items() if k in self.visible_names},folder),daemon=True).start()
    def download_worker(self,items,folder):
        success=fail=0
        for i,(name,groups) in enumerate(items.items(),1):
            if self.stop.is_set():break
            dest=os.path.join(folder,name);temp=dest+'.part'
            try:
                if not os.path.isfile(dest) or os.path.getsize(dest)==0:
                    req=urllib.request.Request(BASE+name,headers={'User-Agent':'AlfDown/6.0'})
                    with urllib.request.urlopen(req,timeout=20) as src,open(temp,'wb') as out:
                        while True:
                            if self.stop.is_set():raise InterruptedError('중지')
                            chunk=src.read(65536)
                            if not chunk:break
                            out.write(chunk)
                    os.replace(temp,dest)
                success+=1;self.record_file(name,dest);self.events.put(('downloaded',(name,groups)))
            except Exception:
                fail+=1
                try:os.remove(temp)
                except OSError:pass
            self.events.put(('download_progress',(i,len(items),success,fail)))
        self.events.put(('download_done',(success,fail,self.stop.is_set())))
    def export_pdf(self):
        if self.busy:return
        folder=self.folder.get().strip()
        if not os.path.isdir(folder):messagebox.showwarning('폴더 확인','다운로드 폴더가 없습니다. 먼저 이미지를 다운로드하세요.');return
        # Only files discovered/downloaded by this session; avoid mixing unrelated files.
        if not self.downloaded:
            self.downloaded={name:groups for name,groups in self.results.items() if os.path.isfile(os.path.join(folder,name))}
        if not self.downloaded:
            messagebox.showwarning('PDF 생성','저장된 이미지가 없습니다. 먼저 다운로드하세요.');return
        self.stop.clear();self.set_busy(True)
        threading.Thread(target=self.pdf_worker,args=(dict(self.downloaded),folder,self.make_parts.get(),self.make_combined.get()),daemon=True).start()
    @staticmethod
    def pdf_sort_key(group,name):
        # Order by the user's instrument order, then part number, then page number.
        # Match long prefixes exactly (TN before T, BC before B).
        match=re.search(r'_([A-Z]+)(\d+)?-(\d+)\.[^.]+$',name,re.I)
        if group=='스코어':
            m=re.search(r'-(\d+)\.[^.]+$',name)
            return (0,0,int(m.group(1)) if m else 999999,natural_key(name))
        if not match:return (999,999,999,natural_key(name))
        code,part,page=match.group(1).upper(),match.group(2),match.group(3)
        ordered=[c for g,items in GROUPS if g==group for c,_ in items]
        try:rank=ordered.index(code)
        except ValueError:rank=999
        return (rank,int(part) if part else 0,int(page),natural_key(name))

    def configure_theme(self):
        style=ttk.Style(self.root)
        style.theme_use('clam')
        bg='#f7f0e5';surface='#fffaf2';fg='#514236';muted='#8f7c6d';accent='#aa8064'
        border='#dfcdbb';hover='#ead9c6'
        self.root.configure(bg=bg)
        style.configure('.',background=bg,foreground=fg,font=('Segoe UI',10))
        style.configure('TFrame',background=bg)
        style.configure('TLabel',background=bg,foreground=fg)
        style.configure('Hero.TLabel',background=bg,foreground='#77543c',font=('Segoe UI',25,'bold'))
        style.configure('Subtitle.TLabel',background=bg,foreground=muted,font=('Segoe UI',9))
        style.configure('TLabelframe',background=bg,foreground='#886b55',bordercolor=border,relief='solid')
        style.configure('TLabelframe.Label',background=bg,foreground='#886b55',font=('Segoe UI',10,'bold'))
        style.configure('TCheckbutton',background=bg,foreground=fg)
        style.map('TCheckbutton',background=[('active',bg)],foreground=[('disabled',muted)])
        style.configure('TEntry',fieldbackground=surface,foreground=fg,insertcolor=fg,bordercolor=border,padding=5)
        style.configure('TButton',background=surface,foreground=fg,bordercolor=border,padding=(13,8))
        style.map('TButton',background=[('active',hover),('disabled','#eee6dc')],foreground=[('disabled','#b3a394')])
        style.configure('Accent.TButton',background=accent,foreground='white',bordercolor=accent)
        style.map('Accent.TButton',background=[('active','#92694e'),('disabled','#d9c8b8')],foreground=[('disabled','#fffaf2')])
        style.configure('TProgressbar',troughcolor=surface,background=accent,bordercolor=border)
        style.configure('TScrollbar',background=hover,troughcolor=bg)

    def init_db(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS downloads (name TEXT PRIMARY KEY, path TEXT NOT NULL, saved_at TEXT DEFAULT CURRENT_TIMESTAMP)')

    def record_file(self,name,path):
        with sqlite3.connect(self.db_path,timeout=20) as db:
            db.execute('INSERT OR REPLACE INTO downloads(name,path,saved_at) VALUES(?,?,CURRENT_TIMESTAMP)',(name,os.path.abspath(path)))

    def show_history(self):
        w=tk.Toplevel(self.root);w.title('다운로드 기록 · 상품별');w.geometry('820x480')
        ttk.Label(w,text='상품번호별 다운로드 기록 (중복 없이 표시)').pack(anchor='w',padx=12,pady=(12,0))
        lb=tk.Listbox(w,font=('Consolas',11));lb.pack(fill='both',expand=True,padx=12,pady=12)
        with sqlite3.connect(self.db_path) as db:
            rows=db.execute('SELECT name,path,saved_at FROM downloads ORDER BY saved_at DESC').fetchall()
        products={}
        for name,path,date in rows:
            # The prefix before _PART or -PAGE is the product identifier.
            product=name.split('_',1)[0] if '_' in name else re.sub(r'-\d+\.png$','',name,flags=re.I)
            if product not in products:products[product]={'folder':os.path.dirname(path),'count':0,'date':date}
            products[product]['count']+=1
            if os.path.isdir(os.path.dirname(path)) and not os.path.isdir(products[product]['folder']):
                products[product]['folder']=os.path.dirname(path)
        product_ids=list(products)
        for product in product_ids:
            item=products[product]
            lb.insert('end',f'{product}   ·   {item["count"]}개 파일   ·   {item["date"]}')

    @staticmethod
    def open_path(path):
        if sys.platform=='win32':os.startfile(path)
        elif sys.platform=='darwin':subprocess.Popen(['open',path])
        else:subprocess.Popen(['xdg-open',path])

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            name=self.visible_names.pop(i);self.results.pop(name,None);self.listbox.delete(i)
        self.set_busy(False)

    def preview(self):
        if not self.listbox.curselection():messagebox.showinfo('미리보기','목록에서 파일을 선택하세요.');return
        name=self.visible_names[self.listbox.curselection()[0]]
        w=tk.Toplevel(self.root);w.title('미리보기 · '+name);w.geometry('750x850')
        lbl=ttk.Label(w,text='이미지를 불러오는 중...',anchor='center');lbl.pack(fill='both',expand=True)
        def worker():
            try:
                path=os.path.join(self.folder.get(),name)
                if os.path.isfile(path):im=Image.open(path)
                else:
                    req=urllib.request.Request(BASE+name,headers={'User-Agent':'AlfDown/6.0'})
                    with urllib.request.urlopen(req,timeout=25) as response:
                        data=response.read(25*1024*1024+1)
                    if len(data)>25*1024*1024:raise ValueError('미리보기 최대 크기 25MB 초과')
                    im=Image.open(io.BytesIO(data))
                im.thumbnail((700,760));photo=__import__('PIL.ImageTk',fromlist=['PhotoImage']).PhotoImage(im)
                self.events.put(('preview_ready',(w,lbl,photo)))
            except Exception as e:self.events.put(('preview_error',(w,lbl,str(e))))
        threading.Thread(target=worker,daemon=True).start()

    def report_missing(self):
        groups=defaultdict(set)
        for name in self.results:
            m=re.search(r'^(.*?)_([A-Z]+)(\d*)-(\d+)\.[^.]+$',name,re.I)
            if m:groups[(m.group(2).upper(),m.group(3) or '0')].add(int(m.group(4)))
            else:
                m=re.search(r'-(\d+)\.[^.]+$',name)
                if m:groups[('스코어','0')].add(int(m.group(1)))
        issues=[]
        for (code,part),pages in sorted(groups.items()):
            gaps=sorted(set(range(1,max(pages)+1))-pages)
            if gaps:issues.append(f'{code}{"" if part=="0" else part}: {gaps}')
        messagebox.showinfo('페이지 누락 의심', '\n'.join(issues[:100]) if issues else '발견된 각 파트의 마지막 페이지 이전에는 누락이 보이지 않습니다.\n마지막 페이지 이후의 누락은 확인할 수 없습니다.')

    def configure_order(self):
        w=tk.Toplevel(self.root);w.title('PDF 파트 순서');w.geometry('390x540')
        ttk.Label(w,text='위/아래 버튼으로 PDF 순서를 조정하세요').pack(pady=8)
        lb=tk.Listbox(w);lb.pack(fill='both',expand=True,padx=12)
        codes=self.custom_order or [code for group,items in GROUPS for code,_ in items]
        for code in codes:lb.insert('end',code)
        def move(d):
            if not lb.curselection():return
            i=lb.curselection()[0];j=i+d
            if j<0 or j>=lb.size():return
            val=lb.get(i);lb.delete(i);lb.insert(j,val);lb.selection_set(j)
        ttk.Button(w,text='위로',command=lambda:move(-1)).pack()
        ttk.Button(w,text='아래로',command=lambda:move(1)).pack()
        def save():self.custom_order=list(lb.get(0,'end'));w.destroy()
        ttk.Button(w,text='순서 저장',command=save).pack(pady=8)

    def pdf_worker(self,items,folder,make_parts,make_combined):
        # One PDF per instrument family; within each family, instrument/part/page order.
        bygroup=defaultdict(list)
        for name,groups in items.items():
            path=os.path.join(folder,name)
            if not os.path.isfile(path):continue
            group=next(iter(groups),None)
            if group not in [g for g,_ in GROUPS]+['스코어']:
                group='스코어' if re.search(r'-\d+\.png$',name,re.I) and '_' not in name else None
            if group:bygroup[group].append((name,path))
        codes=self.custom_order or [code for group,entries in GROUPS for code,_ in entries]
        def order_key(name):
            m=re.search(r'_([A-Z]+?)(\d*)-(\d+)\.png$',name,re.I)
            if not m:
                n=re.search(r'-(\d+)\.png$',name,re.I)
                return (-1,0,int(n.group(1)) if n else 0)
            code,part,page=m.group(1).upper(),int(m.group(2) or 0),int(m.group(3))
            return (codes.index(code) if code in codes else 999,part,page)
        family_order=['스코어']+[g for g,_ in GROUPS]
        created=[];skipped=0;combined=[]
        folder_label=os.path.basename(os.path.normpath(os.path.abspath(folder))).rstrip(' .') or 'AlfDown'
        try:
            for group in family_order:
                if self.stop.is_set():break
                images=[]
                for name,path in sorted(bygroup.get(group,[]),key=lambda item:order_key(item[0])):
                    if self.stop.is_set():break
                    try:
                        with Image.open(path) as source:
                            im=ImageOps.exif_transpose(source)
                            if im.mode=='RGBA':
                                bg=Image.new('RGB',im.size,'white');bg.paste(im,mask=im.getchannel('A'));im=bg
                            else:im=im.convert('RGB')
                            images.append(im)
                    except Exception:skipped+=1
                if not images:continue
                if make_parts:
                    out=os.path.join(folder,f'{folder_label}_{group}.pdf')
                    images[0].save(out,'PDF',save_all=True,append_images=images[1:],resolution=150)
                    created.append(out)
                if make_combined:combined.extend(images)
                else:
                    for im in images:im.close()
            if combined and not self.stop.is_set():
                out=os.path.join(folder,f'{folder_label}_전체합본.pdf')
                combined[0].save(out,'PDF',save_all=True,append_images=combined[1:],resolution=150)
                created.append(out)
        except Exception as e:self.events.put(('pdf_error',str(e)))
        finally:
            for im in combined:im.close()
        self.events.put(('pdf_done',(created,skipped,self.stop.is_set())))

    def pump(self):
        try:
            for _ in range(200):
                kind,value=self.events.get_nowait()
                if kind=='found':
                    name,groups=value;self.results[name]=set(groups);self.visible_names.append(name);self.listbox.insert('end',f'[{", ".join(sorted(groups))}] {name}');self.listbox.see('end')
                elif kind=='progress':
                    done,total,found,err,forbidden=value;self.progress['value']=done
                    self.status.set(f'검사 {done:,}/{total:,} | 발견 {found} | 오류 {err} | 접근거부 {forbidden}')
                elif kind=='search_done':
                    stopped,done,found,err,forbidden=value;self.set_busy(False)
                    self.status.set(f'{"중지됨" if stopped else "검색 완료"} | 검사 {done:,} | 발견 {found} | 오류 {err} | 접근거부 {forbidden}')
                elif kind=='downloaded':
                    name,groups=value;self.downloaded[name]=set(groups)
                elif kind=='download_progress':
                    done,total,success,fail=value;self.progress['value']=done
                    self.status.set(f'다운로드 {done}/{total} | 성공 {success} | 실패 {fail}')
                elif kind=='download_done':
                    success,fail,stopped=value;self.set_busy(False)
                    self.status.set(f'다운로드 {"중지" if stopped else "완료"} | 성공 {success} | 실패 {fail}')
                elif kind=='preview_ready':
                    w,lbl,photo=value
                    if w.winfo_exists():lbl.config(image=photo,text='');lbl.image=photo
                elif kind=='preview_error':
                    w,lbl,error=value
                    if w.winfo_exists():lbl.config(text='미리보기 실패: '+error)
                elif kind=='pdf_error':messagebox.showerror('PDF 생성 오류',value)
                elif kind=='pdf_done':
                    created,skipped,stopped=value;self.set_busy(False)
                    self.status.set(f'PDF {len(created)}개 생성 | 이미지 오류 {skipped} | {"중지됨" if stopped else "완료"}')
                    messagebox.showinfo('PDF 생성 결과',f'PDF {len(created)}개 생성\n이미지 읽기 실패 {skipped}개\n'+ '\n'.join(os.path.basename(p) for p in created))
        except queue.Empty:pass
        self.root.after(80,self.pump)

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()