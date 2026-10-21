import os
import queue
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

BASE = 'https://alfred-dsm-shopify-exports-prod.s3.us-west-2.amazonaws.com/'
GROUPS = {'현악기':['VN','VA','VC','SB'], '목관악기':['P','F','O','C','BC','B'], '금관악기':['H','T','TN','TB'], '타악기':['M','TMP','PC'], '그 외':['K']}
SINGLE = {'SB','BC','TMP','K'}

class App:
    def __init__(self, root):
        self.root=root; root.title('AlfDown 3.0 | 악기별 이미지 검색'); root.geometry('900x750'); root.minsize(780,650)
        self.events=queue.Queue(); self.stop=threading.Event(); self.busy=False; self.results=[]
        self.code=tk.StringVar(value='00-PC-0019403'); self.part=tk.StringVar(value='3'); self.page=tk.StringVar(value='15'); self.workers=tk.StringVar(value='6'); self.folder=tk.StringVar(value=os.path.join(os.path.expanduser('~'),'Downloads','AlfDown'))
        top=ttk.Frame(root,padding=12);top.pack(fill='x')
        ttk.Label(top,text='AlfDown 3.0',font=('Segoe UI',17,'bold')).pack(anchor='w')
        ttk.Label(top,text='공개적으로 접근 가능한 S3 이미지 주소를 파일명 패턴으로 검사합니다.').pack(anchor='w')
        line=ttk.Frame(top);line.pack(fill='x',pady=8)
        ttk.Label(line,text='상품 코드').pack(side='left');ttk.Entry(line,textvariable=self.code,width=36).pack(side='left',padx=8)
        ttk.Label(line,text='파트 최대').pack(side='left');ttk.Entry(line,textvariable=self.part,width=5).pack(side='left',padx=6)
        ttk.Label(line,text='페이지 최대').pack(side='left');ttk.Entry(line,textvariable=self.page,width=5).pack(side='left',padx=6)
        ttk.Label(line,text='동시 요청').pack(side='left');ttk.Entry(line,textvariable=self.workers,width=4).pack(side='left',padx=6)
        self.flags={c:tk.BooleanVar(value=(c in ['VN','VA','VC','SB'])) for codes in GROUPS.values() for c in codes}
        for group,codes in GROUPS.items():
            frame=ttk.LabelFrame(root,text=group,padding=(12,6));frame.pack(fill='x',padx=12,pady=3)
            for c in codes: ttk.Checkbutton(frame,text=c,variable=self.flags[c]).pack(side='left',padx=12)
            ttk.Button(frame,text='그룹 선택/해제',command=lambda cc=codes:self.toggle(cc)).pack(side='right')
        opts=ttk.Frame(root,padding=(12,7));opts.pack(fill='x')
        ttk.Button(opts,text='전체 선택',command=lambda:self.select_all(True)).pack(side='left')
        ttk.Button(opts,text='전체 해제',command=lambda:self.select_all(False)).pack(side='left',padx=6)
        self.exts={e:tk.BooleanVar(value=e=='png') for e in ['png','jpg','jpeg','webp']}
        ttk.Label(opts,text='확장자:').pack(side='left',padx=(22,4))
        for e,v in self.exts.items():ttk.Checkbutton(opts,text=e,variable=v).pack(side='left',padx=4)
        pathrow=ttk.Frame(root,padding=(12,5));pathrow.pack(fill='x')
        ttk.Label(pathrow,text='저장 폴더').pack(side='left');ttk.Entry(pathrow,textvariable=self.folder).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(pathrow,text='찾아보기',command=self.browse).pack(side='left')
        controls=ttk.Frame(root,padding=(12,8));controls.pack(fill='x')
        self.startbtn=ttk.Button(controls,text='검색 시작',command=self.start);self.startbtn.pack(side='left')
        self.stopbtn=ttk.Button(controls,text='검색 중지',command=self.cancel,state='disabled');self.stopbtn.pack(side='left',padx=7)
        self.dlbtn=ttk.Button(controls,text='발견 파일 다운로드',command=self.download,state='disabled');self.dlbtn.pack(side='left')
        self.status=tk.StringVar(value='대기 중');ttk.Label(root,textvariable=self.status,padding=(12,2)).pack(anchor='w')
        self.progress=ttk.Progressbar(root,mode='determinate');self.progress.pack(fill='x',padx=12,pady=4)
        resultframe=ttk.Frame(root,padding=(12,5));resultframe.pack(fill='both',expand=True)
        self.listbox=tk.Listbox(resultframe,selectmode='extended',font=('Consolas',10));self.listbox.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(resultframe,orient='vertical',command=self.listbox.yview);scroll.pack(side='right',fill='y');self.listbox.configure(yscrollcommand=scroll.set)
        ttk.Label(root,text='참고: 목록 조회 권한이 없는 경우 선택한 패턴만 검사하며, 누락된 파일명은 발견할 수 없습니다.',padding=10).pack(anchor='w')
        root.after(80,self.pump)
    def toggle(self,codes):
        val=not all(self.flags[c].get() for c in codes)
        for c in codes:self.flags[c].set(val)
    def select_all(self,v):
        for f in self.flags.values():f.set(v)
    def browse(self):
        p=filedialog.askdirectory(initialdir=os.path.expanduser('~'))
        if p:self.folder.set(p)
    def candidates(self,product,parts,pages,codes,exts):
        for c in codes:
            for part in ([None] if c in SINGLE else range(1,parts+1)):
                for page in range(1,pages+1):
                    for ext in exts:
                        yield f'{product}_{c}{"" if part is None else part}-{page}.{ext}'
    def validate(self):
        product=self.code.get().strip().rstrip('_')
        if not product or '/' in product or '\\' in product or '..' in product:raise ValueError('올바른 상품 코드를 입력하세요.')
        parts=int(self.part.get());pages=int(self.page.get());workers=int(self.workers.get())
        if not (1<=parts<=100 and 1<=pages<=500 and 1<=workers<=12):raise ValueError('파트 1~100, 페이지 1~500, 동시 요청 1~12만 가능합니다.')
        codes=[c for c,v in self.flags.items() if v.get()];exts=[e for e,v in self.exts.items() if v.get()]
        if not codes or not exts:raise ValueError('악기 코드와 확장자를 선택하세요.')
        return list(self.candidates(product,parts,pages,codes,exts)),workers
    def start(self):
        if self.busy:return
        try:names,workers=self.validate()
        except (ValueError,TypeError) as e:messagebox.showerror('입력 확인',str(e));return
        self.results=[];self.listbox.delete(0,'end');self.stop.clear();self.busy=True
        self.startbtn.config(state='disabled');self.stopbtn.config(state='normal');self.dlbtn.config(state='disabled')
        self.progress.config(maximum=len(names),value=0);self.status.set(f'검색 시작: {len(names):,}개 후보')
        threading.Thread(target=self.search_worker,args=(names,workers),daemon=True).start()
    def cancel(self):
        self.stop.set();self.status.set('중지 요청됨 — 진행 중인 요청 종료를 기다리는 중...');self.stopbtn.config(state='disabled')
    def check(self,name):
        if self.stop.is_set():return name,None
        url=BASE+name
        try:
            req=urllib.request.Request(url,method='HEAD',headers={'User-Agent':'AlfDown/3.0'})
            with urllib.request.urlopen(req,timeout=5) as response:
                if response.status==200:return name,'found'
        except urllib.error.HTTPError as e:
            if e.code in (404,403):return name,'missing' if e.code==404 else 'forbidden'
            return name,'error'
        except Exception:return name,'error'
        return name,'missing'
    def search_worker(self,names,workers):
        done=0;errors=0;forbidden=0;found=0
        iterator=iter(names)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending=set()
            for _ in range(min(workers*2,len(names))):
                try:pending.add(pool.submit(self.check,next(iterator)))
                except StopIteration:break
            while pending:
                completed,pending=wait(pending,timeout=0.2,return_when=FIRST_COMPLETED)
                for f in completed:
                    name,status=f.result();done+=1
                    if status=='found':found+=1;self.events.put(('found',name))
                    elif status=='forbidden':forbidden+=1
                    elif status=='error':errors+=1
                    if not self.stop.is_set():
                        try:pending.add(pool.submit(self.check,next(iterator)))
                        except StopIteration:pass
                self.events.put(('progress',(done,len(names),found,errors,forbidden)))
        self.events.put(('done',(self.stop.is_set(),done,found,errors,forbidden)))
    def download(self):
        if self.busy or not self.results:return
        folder=self.folder.get().strip()
        if not folder:messagebox.showerror('저장 폴더','저장 폴더를 선택하세요.');return
        try:os.makedirs(folder,exist_ok=True)
        except OSError as e:messagebox.showerror('폴더 오류',str(e));return
        self.busy=True;self.stop.clear();self.startbtn.config(state='disabled');self.stopbtn.config(state='normal');self.dlbtn.config(state='disabled')
        self.progress.config(maximum=len(self.results),value=0)
        threading.Thread(target=self.download_worker,args=(list(self.results),folder),daemon=True).start()
    def download_worker(self,names,folder):
        success=0;fail=0
        for i,name in enumerate(names,1):
            if self.stop.is_set():break
            dest=os.path.join(folder,name)
            try:
                if not os.path.isfile(dest):
                    req=urllib.request.Request(BASE+name,headers={'User-Agent':'AlfDown/3.0'})
                    with urllib.request.urlopen(req,timeout=15) as src,open(dest+'.part','wb') as out:
                        while True:
                            if self.stop.is_set():raise InterruptedError('중지')
                            chunk=src.read(65536)
                            if not chunk:break
                            out.write(chunk)
                    os.replace(dest+'.part',dest)
                success+=1
            except Exception:
                fail+=1
                try:os.remove(dest+'.part')
                except OSError:pass
            self.events.put(('download_progress',(i,len(names),success,fail)))
        self.events.put(('download_done',(success,fail,self.stop.is_set())))
    def pump(self):
        try:
            for _ in range(200):
                kind,value=self.events.get_nowait()
                if kind=='found':self.results.append(value);self.listbox.insert('end',value);self.listbox.see('end')
                elif kind=='progress':
                    done,total,found,err,forbid=value;self.progress['value']=done;self.status.set(f'검사 {done:,}/{total:,} | 발견 {found} | 오류 {err} | 접근거부 {forbid}')
                elif kind=='done':
                    stopped,done,found,err,forbid=value;self.busy=False;self.startbtn.config(state='normal');self.stopbtn.config(state='disabled');self.dlbtn.config(state='normal' if self.results else 'disabled');self.status.set(f'{"중지됨" if stopped else "검색 완료"} | 검사 {done:,} | 발견 {found} | 오류 {err} | 접근거부 {forbid}')
                elif kind=='download_progress':
                    done,total,success,fail=value;self.progress['value']=done;self.status.set(f'다운로드 {done}/{total} | 성공 {success} | 실패 {fail}')
                elif kind=='download_done':
                    success,fail,stopped=value;self.busy=False;self.startbtn.config(state='normal');self.stopbtn.config(state='disabled');self.dlbtn.config(state='normal' if self.results else 'disabled');self.status.set(f'다운로드 {"중지" if stopped else "완료"} | 성공 {success} | 실패 {fail}')
        except queue.Empty:pass
        self.root.after(80,self.pump)

if __name__=='__main__':
    root=tk.Tk();App(root);root.mainloop()
