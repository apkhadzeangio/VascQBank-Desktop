import json, collections, re, csv, os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
d=json.load(open('counts.json',encoding='utf-8'))
cq,ce,docs,suf=(collections.Counter(d[k]) for k in ('q','e','docs','suf'))
allw=cq+ce
OUT='/home/user/VascQBank-Desktop/word_bank'; os.makedirs(OUT,exist_ok=True)
import unicodedata
geo=lambda w: not any(unicodedata.category(c).startswith('L') and not ('\u10d0'<=c<='\u10ff') for c in w)
def akey(w): return (0 if 'ა'<=w[0]<='ჿ' else 1, w.casefold())
alpha=sorted(allw,key=akey)
freq=sorted(allw,key=lambda w:(-allw[w],akey(w)))
H=['№','სიტყვა','სულ','კითხვებში (პირობა + პასუხები)','განმარტებებში','რამდენ ტესტშია (10 000-დან)']
row=lambda i,w:[i,w,allw[w],cq[w],ce[w],docs[w]]
wb=Workbook()
hdr=Font(bold=True,color='FFFFFF'); fill=PatternFill('solid',fgColor='1F4E79')
def sheet(ws,head,rows,widths):
    ws.append(head)
    for c in ws[1]: c.font=hdr; c.fill=fill; c.alignment=Alignment(wrap_text=True,vertical='center')
    for r in rows: ws.append(r)
    ws.freeze_panes='C2' if len(head)>2 else 'A2'; ws.auto_filter.ref=ws.dimensions
    for col,wd in zip('ABCDEFG',widths): ws.column_dimensions[col].width=wd
    ws.row_dimensions[1].height=32
s=wb.active; s.title='შეჯამება'
nG=sum(1 for w in allw if geo(w)); nL=len(allw)-nG
tot=sum(allw.values())
info=[('VascQBank-10K — სიტყვათა ბანკი (განმარტებების გარეშე)',''),('',''),
('წყარო','VascQBank-10K, MASTER/VascQBank_10K_ALL_QUESTIONS.txt და ..._ALL_EXPLANATIONS.txt (10 000 კითხვა, 10 000 განმარტება)'),
('',''),
('უნიკალური სიტყვაფორმა — სულ',len(allw)),
('  მათ შორის ქართული',nG),
('  მათ შორის ლათინური/ბერძნული შემცველი (აბრევიატურები, ტერმინები)',nL),
('სიტყვების საერთო რაოდენობა (ტოკენები)',tot),
('',''),
('კითხვებში (პირობა + 5 პასუხი): უნიკალური',len(cq)),
('კითხვებში: სულ ტოკენი',sum(cq.values())),
('განმარტებებში: უნიკალური',len(ce)),
('განმარტებებში: სულ ტოკენი',sum(ce.values())),
('მხოლოდ კითხვებში გვხვდება',sum(1 for w in allw if ce[w]==0)),
('მხოლოდ განმარტებებში გვხვდება',sum(1 for w in allw if cq[w]==0)),
('მხოლოდ ერთხელ გვხვდება (hapax)',sum(1 for w in allw if allw[w]==1)),
('',''),
('მეთოდი',''),
('•','სიტყვა = ასოების უწყვეტი მიმდევრობა; დეფისით შეერთებული ნაწილები ერთ სიტყვად ითვლება (გოჯ-მხრის, ABI-ის); სიტყვის შიგნით ციფრები ნარჩუნდება (HbA1c, P2Y12, მე-3).'),
('•','რიცხვები, პუნქტუაცია, კითხვის ნომრები და პასუხის ასოები (ა) … ე), *) არ ითვლება.'),
('•','ქართული გრამატიკული ფორმები ცალ-ცალკეა (არტერია / არტერიის / არტერიით) — ეს სიტყვაფორმების ლექსიკონია, არა ლემების.'),
('•','ლათინური ასოები რეგისტრს ინარჩუნებს (ABI ≠ abi).'),
('•','რიცხვზე მიერთებული ფორმები (24-საათიანი, 1,54-ია, 30%-ით) ცალკე ფურცელზეა.'),
('•','„რამდენ ტესტშია“ = რამდენ ერთეულში (კითხვა+განმარტება) გვხვდება ერთხელ მაინც.'),
]
for a,b in info: s.append([a,b])
s['A1'].font=Font(bold=True,size=14); s.column_dimensions['A'].width=62; s.column_dimensions['B'].width=110
for r in (18,): s.cell(r,1).font=Font(bold=True)
W=[9,38,10,16,14,16]
sheet(wb.create_sheet('ანბანით'),H,[row(i,w) for i,w in enumerate(alpha,1)],W)
sheet(wb.create_sheet('სიხშირით'),H,[row(i,w) for i,w in enumerate(freq,1)],W)
# Latin/Greek
lat=[w for w in allw if not geo(w)]
def base(w):
    segs=w.split('-'); b=[]
    for sg in segs:
        if b and all('ა'<=c<='ჿ' for c in sg): break
        b.append(sg)
    return '-'.join(b)
bc=collections.Counter(); bforms=collections.defaultdict(int)
for w in lat: bc[base(w)]+=allw[w]; bforms[base(w)]+=1
sheet(wb.create_sheet('აბრევიატურები (ფუძით)'),['№','აბრევიატურა / ლათინური ფუძე','სულ (ყველა ფორმით)','ფორმების რაოდენობა'],
      [[i,b,bc[b],bforms[b]] for i,b in enumerate(sorted(bc,key=lambda b:(-bc[b],b.casefold())),1)],[9,34,18,18])
sheet(wb.create_sheet('ლათინური ფორმები'),H,[row(i,w) for i,w in enumerate(sorted(lat,key=lambda w:(-allw[w],w.casefold())),1)],W)
sheet(wb.create_sheet('რიცხვზე მიერთებული'),['№','ფორმა (რიცხვის შემდეგ)','სულ'],
      [[i,'-'+w,suf[w]] for i,w in enumerate(sorted(suf,key=lambda w:(-suf[w],w)),1)],[9,30,10])
wb.save(f'{OUT}/VascQBank_10K_სიტყვათა_ბანკი.xlsx')
with open(f'{OUT}/VascQBank_10K_word_bank.csv','w',newline='',encoding='utf-8-sig') as f:
    c=csv.writer(f); c.writerow(H[1:])
    for w in alpha: c.writerow(row(0,w)[1:])
with open(f'{OUT}/VascQBank_10K_word_bank.txt','w',encoding='utf-8') as f:
    for w in alpha: f.write(f'{w}\t{allw[w]}\n')
print(nG,nL,len(bc))
#print([ (b,bc[b]) for b in sorted(bc,key=lambda b:-bc[b])[:25]])
