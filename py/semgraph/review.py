"""정답 초안 확인 화면(로컬 HTML 한 장). 질의마다 초안의 core/aux 와 나머지 후보를 보이고, 라디오로 고친다.
체크는 브라우저 localStorage 에 남고, 「gold.json 내려받기」로 파일을 받는다.
"""

import html
import json


def build(units, queries, draft, pool=None):
    U = {u.id: u for u in units}
    data = []
    for q in queries:
        rows = []
        seen = set()
        for g in draft.get(q["id"], []):
            if g["id"] in U and g["id"] not in seen:
                seen.add(g["id"])
                rows.append({"id": g["id"], "label": g["label"], "why": g.get("why", ""), "note": g.get("note", ""),
                             "found": g.get("found", "")})
        for c in (pool or {}).get(q["id"], [])[:15]:
            if c not in seen:
                seen.add(c)
                rows.append({"id": c, "label": "no", "why": "", "note": "", "found": "pool"})
        for r in rows:
            u = U[r["id"]]
            r.update(page=u.page, kind=u.kind, text=u.text[:1500])
        data.append({"id": q["id"], "type": q["type"], "text": q["text"], "self": q.get("self"), "rows": rows})
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload)


TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SG1 시험지 확인</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--core:#e8f3ea;--aux:#f6f1e3;--acc:#2f6f4f}
@media (prefers-color-scheme:dark){:root{--bg:#18181a;--fg:#e8e8e4;--mut:#9a9a94;--line:#333336;--core:#1f3326;--aux:#33301f;--acc:#7cc39c}}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;padding:0 16px 80px}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
h1{font-size:16px;margin:0}button{font:inherit;padding:6px 12px;border:1px solid var(--line);background:transparent;color:var(--fg);border-radius:6px;cursor:pointer}
button.p{background:var(--acc);color:var(--bg);border-color:var(--acc)}
.q{max-width:960px;margin:24px auto;border-top:2px solid var(--fg);padding-top:10px}
.qh{font-weight:600}.qt{margin:4px 0 10px}.mut{color:var(--mut);font-size:13px}
.r{display:grid;grid-template-columns:150px 1fr;gap:10px;border-top:1px solid var(--line);padding:8px 4px}
.r.core{background:var(--core)}.r.aux{background:var(--aux)}
.r label{display:block;font-size:13px;cursor:pointer}.txt{white-space:pre-wrap;font-size:14px;max-height:9em;overflow:auto}
.why{font-size:12px;color:var(--mut)}
@media (max-width:600px){.r{grid-template-columns:1fr}}
</style></head><body>
<header><h1>SG1 시험지 확인</h1><span class="mut" id="cnt"></span>
<span class="mut">초안 = 에이전트가 단 것 · 아래 「후보」는 검색기가 올렸지만 초안에 없는 것</span>
<button class="p" id="dl">gold.json 내려받기</button></header>
<main id="m"></main>
<script>
const D=__DATA__;const KEY="sg1-gold-v1";let st={};try{st=JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){}
const get=(q,r)=>st[q+"|"+r.id]??r.label;
function save(){try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}count()}
function count(){let c=0,a=0;D.forEach(q=>q.rows.forEach(r=>{const v=get(q.id,r);if(v=="core")c++;if(v=="aux")a++}));document.getElementById("cnt").textContent=`core ${c} · aux ${a}`}
const m=document.getElementById("m");
D.forEach(q=>{const s=document.createElement("section");s.className="q";
s.innerHTML=`<div class="qh">${q.id} · ${q.type=="drafting"?"원고작성 목차":"그린워싱 주장"}${q.self?` <span class="mut">(자기 블록 ${q.self} 뺌)</span>`:""}</div><div class="qt"></div>`;
s.querySelector(".qt").textContent=q.text;
q.rows.forEach(r=>{const d=document.createElement("div");const v=get(q.id,r);d.className="r "+v;
const n=q.id+"-"+r.id;
d.innerHTML=`<div><div><b>${r.id}</b> <span class="mut">p${r.page} ${r.kind}</span></div>
${["core","aux","no"].map(x=>`<label><input type="radio" name="${n}" value="${x}" ${v==x?"checked":""}> ${x=="core"?"핵심":x=="aux"?"보조":"아님"}</label>`).join("")}
<div class="why"></div></div><div class="txt"></div>`;
d.querySelector(".txt").textContent=r.text;d.querySelector(".why").textContent=(r.found=="read"?"[직접 찾음] ":r.label=="no"?"[후보] ":"")+r.why+(r.note?" — "+r.note:"");
d.querySelectorAll("input").forEach(i=>i.onchange=()=>{st[q.id+"|"+r.id]=i.value;d.className="r "+i.value;save()});
s.appendChild(d)});m.appendChild(s)});
count();
document.getElementById("dl").onclick=()=>{const out={};D.forEach(q=>{out[q.id]=q.rows.filter(r=>get(q.id,r)!="no").map(r=>({id:r.id,label:get(q.id,r)}))});
const b=new Blob([JSON.stringify(out,null,1)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="gold.json";a.click()};
</script></body></html>"""
