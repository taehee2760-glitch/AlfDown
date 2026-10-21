import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
import threading, os, re
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

HOST='https://alfred-dsm-shopify-exports-prod.s3.us-west-2.amazonaws.com/'
DEFAULT='00-PC-0019403_'
EXTS=['png','jpg','jpeg','webp']
class App:
 def __init__(self, root):
  self.root=root; root.title('S3 이미지 일괄 다운로더'); root.geometry('790x580'); root.configure(bg='#f3f6fa')
  self.prefix=tk.StringVar(value=DEFAULT); self.folder=tk.StringVar(value=os.path.join(os.path.expanduser('~'),'Downloads','S3_Images'))
  self.mode=tk.StringVar(value='list'); self.maxn=tk.IntVar(value=30); self.maxgroup=tk.IntVar(value=10)
  self.items=[]; self.busy=False; self.cancel=threading.Event(); self.seen=set()
  f=ttk.Frame(root,padding=18);f.pack(fill='both',expand=True)
  ttk.Label(f,text='S3 이미지 일괄 다운로더',font=('맑은 고딕',17,'bold')).pack(anchor='w',pady=(0,12))
  ttk.Label(f,text='검색할 파일명 앞부분 또는 전체 이미지 URL (언제든 변경 가능)').pack(anchor='w')
  ttk.Entry(f,textvariable=self.prefix).pack(fill='x',pady=(4,12))
  ttk.Label(f,text='저장 폴더').pack(anchor='w')
  rr=ttk.Frame(f);rr.pack(fill='x',pady=(4,12));ttk.Entry(rr,textvariable=self.folder).pack(side='left',fill='x',expand=True);ttk.Button(rr,text='폴더 선택',command=self.choose).pack(side='left',padx=(8,0))
  ttk.Radiobutton(f,text='S3 목록 조회 (해당 이름으로 시작하는 모든 공개 이미지)',variable=self.mode,value='list').pack(anchor='w')
  ttk.Radiobutton(f,text='입력한 파일 하나만 다운로드 (전체 URL 또는 정확한 파일명)',variable=self.mode,value='exact').pack(anchor='w')
  ttk.Radiobutton(f,text='파일명 패턴 추측 검색 (전체 파일 보장 안 됨)',variable=self.mode,value='guess').pack(anchor='w',pady=(3,0))
  r=ttk.Frame(f);r.pack(fill='x',pady=(5,10));ttk.Label(r,text='VN 번호 1 ~').pack(side='left');ttk.Spinbox(r,from_=1,to=1000,textvariable=self.maxn,width=6).pack(side='left',padx=5);ttk.Label(r,text='뒤 번호 1 ~').pack(side='left',padx=(15,0));ttk.Spinbox(r,from_=1,to=100,textvariable=self.maxgroup,width=6).pack(side='left',padx=5)
  r=ttk.Frame(f);r.pack(fill='x');self.scan=ttk.Button(r,text='파일 검색',command=self.start_scan);self.scan.pack(side='left');self.stop=ttk.Button(r,text='검색 중지',command=self.stop_scan,state='disabled');self.stop.pack(side='left',padx=8);self.download=ttk.Button(r,text='검색 결과 다운로드',command=self.start_download);self.download.pack(side='left',padx=8)
  self.status=tk.StringVar(value='준비 완료');ttk.Label(f,textvariable=self.status).pack(anchor='w',pady=(12,4))
  self.bar=ttk.Progressbar(f,mode='determinate');self.bar.pack(fill='x',pady=(0,10))
  box=ttk.Frame(f);box.pack(fill='both',expand=True);self.list=tk.Listbox(box,selectmode='extended',font=('Consolas',10));self.list.pack(side='left',fill='both',expand=True);sc=ttk.Scrollbar(box,command=self.list.yview);sc.pack(side='right',fill='y');self.list.configure(yscrollcommand=sc.set)
  ttk.Label(f,text='주의: 목록 조회가 차단되면 패턴 검색은 지정된 이름만 검사합니다. 권한이 있는 공개 파일에만 사용하세요.',wraplength=730).pack(anchor='w',pady=(10,0))
 def choose(self):
  p=filedialog.askdirectory();
  if p:self.folder.set(p)
 def getprefix(self):
  s=self.prefix.get().strip()
  if s.startswith(('http://','https://')):
   u=urllib.parse.urlparse(s)
   if u.netloc != urllib.parse.urlparse(HOST).netloc:raise ValueError('지정된 S3 서버의 URL만 지원합니다.')
   s=urllib.parse.unquote(u.path.lstrip('/'))
  if not s:raise ValueError('파일명 또는 URL을 입력하세요.')
  if '/' in s or '..' in s:raise ValueError('파일명만 입력하거나 이 서버의 전체 URL을 입력하세요.')
  return s
 def ui(self,fn):self.root.after(0,fn)
 def setstatus(self,s):self.ui(lambda:self.status.set(s))
 def request(self,url,method='GET'):
  req=urllib.request.Request(url,method=method,headers={'User-Agent':'S3ImageDownloader/1.0'})
  return urllib.request.urlopen(req,timeout=15)
 def stop_scan(self):
  self.cancel.set();self.status.set("중지 요청 중... 진행 중인 요청을 마치면 멈춥니다.")
 def add_found(self,k):
  if k not in self.seen:
   self.seen.add(k);self.items.append(k);self.ui(lambda k=k:self.list.insert("end",k))
 def start_scan(self):
  if self.busy:return
  try:p=self.getprefix(); n=int(self.maxn.get());g=int(self.maxgroup.get());assert 1<=n<=1000 and 1<=g<=100
  except Exception as e:messagebox.showerror('입력 오류',str(e));return
  self.busy=True;self.cancel.clear();self.seen=set();self.items=[];self.list.delete(0,'end');self.bar['value']=0;self.stop.configure(state='normal');self.scan.configure(state='disabled');self.status.set('검색 중...')
  threading.Thread(target=self.scan_worker,args=(p,n,g,self.mode.get()),daemon=True).start()
 def scan_worker(self,p,n,g,mode):
  try:
   found=[]
   if mode=='exact':
    with self.request(HOST+urllib.parse.quote(p),method='HEAD') as resp:
     if resp.status==200:self.add_found(p)
   elif mode=='list':
    token=None
    while not self.cancel.is_set():
     params={'list-type':'2','prefix':p,'max-keys':'1000'}
     if token:params['continuation-token']=token
     url=HOST+'?'+urllib.parse.urlencode(params)
     with self.request(url) as resp:xml=resp.read()
     tree=ET.fromstring(xml);ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}
     for e in tree.findall('s:Contents',ns):
      key=e.findtext('s:Key',default='',namespaces=ns)
      if key.startswith(p) and key.lower().endswith(tuple('.'+x for x in EXTS)):self.add_found(key)
     self.setstatus(f'목록 검색 중: {len(self.items)}개 발견')
     if tree.findtext('s:IsTruncated',default='false',namespaces=ns)!='true':break
     token=tree.findtext('s:NextContinuationToken',namespaces=ns)
     if not token:break
   else:
    candidates=[f'{p}VN{i}-{j}.{ext}' for i in range(1,n+1) for j in range(1,g+1) for ext in EXTS]
    total=len(candidates);done=0
    def check(k):
     try:
      with self.request(HOST+urllib.parse.quote(k),method='HEAD') as r:return k if r.status==200 else None
     except urllib.error.HTTPError as e:
      if e.code in (403,405):
       try:
        req=urllib.request.Request(HOST+urllib.parse.quote(k),headers={'Range':'bytes=0-0','User-Agent':'S3ImageDownloader/1.0'})
        with urllib.request.urlopen(req,timeout=12) as r:return k if r.status in (200,206) else None
       except Exception:return None
      return None
     except Exception:return None
    with ThreadPoolExecutor(max_workers=8) as pool:
     iterator=iter(candidates);pending=set()
     def submit_next():
      if self.cancel.is_set():return
      try:pending.add(pool.submit(check,next(iterator)))
      except StopIteration:pass
     for _ in range(min(8,total)):submit_next()
     while pending and not self.cancel.is_set():
      completed,pending=wait(pending,timeout=0.3,return_when=FIRST_COMPLETED)
      for future in completed:
       k=future.result();done+=1
       if k:self.add_found(k)
       submit_next()
      self.ui(lambda d=done,t=total:self.bar.configure(value=d*100/t))
      self.setstatus(f'패턴 검색 {done}/{total} · 발견 {len(self.items)}개')
     if self.cancel.is_set():
      for future in pending:future.cancel()
   self.setstatus(f"검색 {'중지' if self.cancel.is_set() else '완료'}: {len(self.items)}개 발견")
  except urllib.error.HTTPError as e:self.setstatus(f'검색 실패: HTTP {e.code} — S3 목록 조회 권한이 없을 수 있습니다. 패턴 검색을 선택하세요.')
  except Exception as e:self.setstatus(f'검색 실패: {e}')
  finally:
   self.busy=False;self.ui(lambda:(self.stop.configure(state='disabled'),self.scan.configure(state='normal')))
 def populate(self):
  for k in self.items:self.list.insert('end',k)
  self.status.set(f'검색 완료: {len(self.items)}개 발견')
 def start_download(self):
  if self.busy:return
  if not self.items:messagebox.showinfo('안내','먼저 파일을 검색하세요.');return
  self.busy=True;threading.Thread(target=self.download_worker,daemon=True).start()
 def download_worker(self):
  ok=skip=fail=0;total=len(self.items)
  try:
   folder=self.folder.get().strip();os.makedirs(folder,exist_ok=True)
   for i,k in enumerate(self.items,1):
    name=os.path.basename(k);dest=os.path.join(folder,name)
    try:
     if os.path.exists(dest) and os.path.getsize(dest)>0:skip+=1
     else:
      tmp=dest+'.part'
      try:
       with self.request(HOST+urllib.parse.quote(k)) as src,open(tmp,'wb') as out:
        while True:
         chunk=src.read(1024*256)
         if not chunk:break
         out.write(chunk)
       os.replace(tmp,dest);ok+=1
      finally:
       if os.path.exists(tmp):os.remove(tmp)
    except Exception:fail+=1
    self.ui(lambda i=i:self.bar.configure(value=i*100/total));self.setstatus(f'다운로드 {i}/{total} · 성공 {ok} · 기존 {skip} · 실패 {fail}')
   self.ui(lambda:messagebox.showinfo('완료',f'다운로드 성공: {ok}\n기존 파일 건너뜀: {skip}\n실패: {fail}\n저장 폴더: {folder}'))
  except Exception as e:self.setstatus(f'저장 실패: {e}')
  finally:self.busy=False
if __name__=='__main__':
 root=tk.Tk();App(root);root.mainloop()
