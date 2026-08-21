import re, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from guide_new import (EMBEDDINGS, PART_MODERN, BENCHMARKS, OBSERVABILITY,
                       NEUROSYMBOLIC, AP_ECONOMICS, APPENDIX_RUN)

HERE = pathlib.Path(__file__).parent
p1 = (HERE / "guide-1.html").read_text(encoding="utf-8")
p2 = (HERE / "guide-2.html").read_text(encoding="utf-8")
p3 = (HERE / "guide-3.html").read_text(encoding="utf-8")
p1 = p1.replace("</article></div></div>\n", "").rstrip()
p1 = p1.replace("--rule:#e969-placeholder;\n  ", "")
doc = "\n".join([p1, p2, p3])

# ---- 1. renumber chapters and parts ---------------------------------------
CH = {1:1,2:2,3:3,4:4,5:5,6:7,7:8,8:9,9:10,10:11,11:12,12:13,13:14,14:15,15:16,
      16:23,17:25,18:26,19:27,20:28,21:30,22:31,23:32,24:33,25:34,26:35,27:36,
      28:38,29:39,30:40,31:41,32:42,33:43,34:44,35:45,36:46,37:47,38:48,39:49,
      40:50,41:51,42:52}
PT = {1:1,2:2,3:3,4:5,5:6,6:7,7:8,8:9,9:10,10:11}

def sub_tokens(text):
    text = re.sub(r'id="c(\d+)"', lambda m: f'id="\x00C{CH[int(m.group(1))]}\x00"', text)
    text = re.sub(r'href="#c(\d+)"', lambda m: f'href="#\x00C{CH[int(m.group(1))]}\x00"', text)
    text = re.sub(r'id="p(\d+)"', lambda m: f'id="\x00P{PT[int(m.group(1))]}\x00"', text)
    text = re.sub(r'href="#p(\d+)"', lambda m: f'href="#\x00P{PT[int(m.group(1))]}\x00"', text)
    text = re.sub(r'<span class="chapnum">Chapter (\d+)</span>',
                  lambda m: f'<span class="chapnum">Chapter \x00N{CH[int(m.group(1))]}\x00</span>', text)
    # prose cross-references
    text = re.sub(r'\bChapter (\d+)\b(?!</span>)',
                  lambda m: f'Chapter \x00N{CH[int(m.group(1))]}\x00'
                  if int(m.group(1)) in CH else m.group(0), text)
    return text

doc = sub_tokens(doc)
doc = re.sub(r'\x00C(\d+)\x00', lambda m: f'c{m.group(1)}', doc)
doc = re.sub(r'\x00P(\d+)\x00', lambda m: f'p{m.group(1)}', doc)
doc = re.sub(r'\x00N(\d+)\x00', lambda m: m.group(1), doc)

# part heading text
ROMAN = {1:"I",2:"II",3:"III",4:"IV",5:"V",6:"VI",7:"VII",8:"VIII",9:"IX",10:"X",11:"XI"}
TITLES = {1:"Machine learning from zero",2:"Language models",3:"From model to agent",
          5:"Evaluation and observability",6:"The industry, and who is actually in it",
          7:"Accounts payable, explained for non-accountants",8:"Plumbline, the system",
          9:"The experiment",10:"Epistemics, or: every bug and what it taught",
          11:"Explaining it"}
for n, t in TITLES.items():
    doc = re.sub(rf'(<h2 id="p{n}">)Part [IVX]+ · [^<]+(</h2>)',
                 rf'\1Part {ROMAN[n]} · {t}\2', doc)

# ---- 2. insert new chapters ------------------------------------------------
def before(marker, block):
    global doc
    i = doc.index(marker)
    doc = doc[:i] + block.strip() + "\n\n" + doc[i:]

before('<h3 id="c7">', EMBEDDINGS)                 # embeddings before prediction task
before('<h2 id="p5">', PART_MODERN)                # new Part IV before evaluation
before('<h3 id="c25">', BENCHMARKS)                # benchmarks before the eval gap
before('<h2 id="p6">', OBSERVABILITY)              # observability closes evaluation
before('<h3 id="c36">', AP_ECONOMICS)              # economics closes the AP intro
before('<h2 id="p8">', NEUROSYMBOLIC)              # neurosymbolic closes AP part
before('<h2 id="gloss">', APPENDIX_RUN)            # appendix before glossary
doc = doc.replace('<h2 id="gloss">Glossary</h2>', '<h2 id="gloss">Appendix B · Glossary</h2>')

# ---- 3. rebuild navigation -------------------------------------------------
nav = ['<p class="navtitle">The Plumbline Companion</p>', '<a href="#how">How to use this</a>']
for m in re.finditer(r'<h([23]) id="(p\d+|c\d+|appa|gloss)">(?:<span class="chapnum">'
                     r'(Chapter \d+)</span>)?([^<]*)', doc):
    lvl, ident, chap, title = m.groups()
    title = title.strip()
    if lvl == "2":
        if ident.startswith("p"):
            label = title.split(" · ")[0] + " · " + title.split(" · ")[1][:28]
        else:
            label = title.split(" · ")[0] if " · " in title else title
        nav.append(f'<a class="part" href="#{ident}">{label}</a>')
    else:
        num = chap.replace("Chapter ", "") if chap else ""
        short = title if len(title) <= 34 else title[:32].rsplit(" ", 1)[0] + "…"
        nav.append(f'<a href="#{ident}">{num}. {short}</a>' if num
                   else f'<a href="#{ident}">{short}</a>')
doc = re.sub(r'(<nav aria-label="Contents">).*?(</nav>)',
             lambda m: m.group(1) + "\n" + "\n".join(nav) + "\n" + m.group(2),
             doc, flags=re.S)

# ---- 4. print styles for the PDF ------------------------------------------
PRINT = """
@media print {
  .bk{background:#fff!important; color:#000!important; font-size:10.5pt; line-height:1.5}
  .bk nav{display:none!important}
  .bk .shell{display:block!important; max-width:none; padding:0; margin:0}
  .bk article{max-width:none}
  .bk h1{font-size:26pt; margin-top:0}
  .bk h2{font-size:11pt; break-before:page; break-after:avoid; padding-top:0;
    border-top:1.5pt solid #999; margin-top:0}
  .bk h1 + .deck + h2{break-before:auto}
  .bk h3{font-size:14pt; break-after:avoid; margin-top:1.6em}
  .bk h4{break-after:avoid}
  .bk p,.bk li{orphans:3; widows:3}
  .bk .def,.bk .caution,.bk .plain,.bk pre,.bk table{break-inside:avoid}
  .bk pre{font-size:8pt; background:#fafafa}
  .bk table{font-size:8.5pt; min-width:0}
  .bk .caution{background:#fdf6ec}
  .bk .plain,.bk .sunk{background:#f4f4f2}
  .bk footer{break-before:page}
  .bk a{color:#000; text-decoration:none}
}
@page { size:A4; margin:18mm 16mm 20mm; }
"""
doc = doc.replace("</style>", PRINT + "</style>")

out = HERE / "plumbline-companion.html"
out.write_text(doc, encoding="utf-8")

# ---- 5. verify -------------------------------------------------------------
ids = set(re.findall(r'id="([^"]+)"', doc))
links = set(re.findall(r'href="#([^"]+)"', doc))
chaps = [int(x) for x in re.findall(r'<span class="chapnum">Chapter (\d+)</span>', doc)]
words = len(re.sub(r"<[^>]+>", " ", doc).split())
print(f"bytes            {len(doc):,}")
print(f"approx words     {words:,}")
print(f"chapters         {len(chaps)}  sequential: {chaps == list(range(1, len(chaps)+1))}")
print(f"parts            {len(re.findall(r'<h2 id=', doc))}")
print(f"nav entries      {len(nav)-1}")
print(f"broken links     {sorted(links - ids) or 'none'}")
print(f"tables {doc.count('<table')}  code {doc.count('<pre')}  boxes "
      f"{doc.count('class=\"def\"')+doc.count('class=\"caution\"')+doc.count('class=\"plain\"')}")
