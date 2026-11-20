import re, collections, json, sys
SRC='/home/user/apkhadzeangio/vascqbank-10k/MASTER/'
L=r'[^\W\d_]'
WORD=re.compile(r'[^\W_]+(?:-[^\W_]+)*')
def items(path):
    t=open(path,encoding='utf-8').read()
    parts=re.split(r'(?m)^(?=\d+\. )',t)
    out={}
    for p in parts:
        m=re.match(r'(\d+)\. ',p)
        if not m: continue
        n=int(m.group(1)); body=p[m.end():]
        body=re.sub(r'(?m)^\*?[ა-ე]\) ','',body)   # option letters / answer key
        out[n]=body
    return out
import unicodedata
def hasletter(x): return any(unicodedata.category(c).startswith('L') for c in x)
def words(text):
    for m in WORD.finditer(text):
        w=m.group(); s=m.start()
        segs=w.split('-'); dropped=False
        while segs and not hasletter(segs[0]): segs.pop(0); dropped=True
        if not segs: continue
        w='-'.join(segs)
        if dropped or (s>=2 and text[s-1]=='-' and not re.match(r'[^\W_]',text[s-2])):
            yield ('SUFFIX',w)   # e.g. 1,54-ია , 24-საათიანი
        else:
            yield ('W',w)
Q=items(SRC+'VascQBank_10K_ALL_QUESTIONS.txt'); E=items(SRC+'VascQBank_10K_ALL_EXPLANATIONS.txt')
assert len(Q)==len(E)==10000
cq=collections.Counter(); ce=collections.Counter(); docs=collections.Counter(); suf=collections.Counter()
for n in range(1,10001):
    seen=set()
    for src,c in ((Q[n],cq),(E[n],ce)):
        for k,w in words(src):
            if k=='SUFFIX': suf[w]+=1; continue
            c[w]+=1; seen.add(w)
    for w in seen: docs[w]+=1
json.dump({'q':cq,'e':ce,'docs':docs,'suf':suf},open('counts.json','w',encoding='utf-8'),ensure_ascii=False)
allw=cq+ce
print('unique',len(allw),'tokens',sum(allw.values()),'q-unique',len(cq),'q-tokens',sum(cq.values()),'e-unique',len(ce),'e-tokens',sum(ce.values()))
print(allw.most_common(40))
odd=[w for w in allw if re.search(r'[^ა-ჿA-Za-z\-]',w)]
print('odd',odd[:80])
print(suf.most_common(30))
print('single',sum(1 for w,c in allw.items() if c==1))
