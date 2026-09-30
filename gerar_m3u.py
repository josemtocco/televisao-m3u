
#!/usr/bin/env python3
import json, logging, os, re, sys, time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup

BASE="https://televisao.tv/"
HOSTS={"televisao.tv","www.televisao.tv"}
OUT=Path("canais.m3u"); STATUS=Path("status.json")
TIMEOUT=int(os.getenv("HTTP_TIMEOUT","30")); RETRIES=int(os.getenv("HTTP_RETRIES","3"))
USE_BROWSER=os.getenv("USE_BROWSER","auto").lower()
UA=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

CATEGORIES=OrderedDict([
 ("Notícias","/canais-de-noticias"),("Entretenimento","/canais-de-entretenimento"),
 ("Filmes","/canais-de-filmes"),("Música","/canais-de-musica"),
 ("Esporte","/canais-de-esporte"),("Infantil","/canais-infantis"),
 ("Cultura","/canais-de-cultura"),("Regionais","/canais-regionais"),
 ("Canais católicos","/canais-catolicos"),("Evangélica","/canais-evangelicos"),
 ("Portugal","/canais-de-portugal"),("Moçambique","/canais-de-mocambique"),
 ("Angola","/canais-de-angola"),("Desenhos Online","/desenhos-online")
])
BAD=("/sobre","/contato","/copyright","/politica-de-privacidade",
     "/termos-de-uso","/adicionar-tv","/sitemap","/buscar")

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")
log=logging.getLogger("televisao-m3u")
s=requests.Session()
s.headers.update({"User-Agent":UA,"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                  "Accept-Language":"pt-BR,pt;q=0.9,en;q=0.8","Upgrade-Insecure-Requests":"1"})

def clean(x): return re.sub(r"\s+"," ",x or "").strip()
def absurl(x,base=BASE):
    if not x:return ""
    x=str(x).strip().replace("\\/","/")
    if x.startswith("//"):x="https:"+x
    return urldefrag(urljoin(base,x))[0]
def internal(u):
    try:return urlparse(u).netloc.lower().split(":")[0] in HOSTS
    except:return False
def page_url(u):
    p=urlparse(u).path.lower()
    return internal(u) and not p.endswith((".jpg",".jpeg",".png",".gif",".webp",".svg",".ico",
      ".css",".js",".xml",".json",".pdf",".zip",".rar",".mp4",".webm",".m3u",".m3u8",".mpd"))

def http_get(url):
    last=None
    for n in range(1,RETRIES+1):
        try:
            r=s.get(url,timeout=TIMEOUT,allow_redirects=True,headers={"Referer":BASE,"User-Agent":UA})
            if r.status_code in (403,429,500,502,503,504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.text,r.url
        except Exception as e:
            last=e
            if n<RETRIES: time.sleep(1.5*n)
    raise last

_pw=_browser=_page=None
def browser_get(url):
    global _pw,_browser,_page
    from playwright.sync_api import sync_playwright
    if _page is None:
        _pw=sync_playwright().start()
        _browser=_pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage",
          "--disable-blink-features=AutomationControlled"])
        ctx=_browser.new_context(user_agent=UA,locale="pt-BR",viewport={"width":1440,"height":900})
        _page=ctx.new_page()
    _page.goto(url,wait_until="domcontentloaded",timeout=TIMEOUT*1000)
    _page.wait_for_timeout(1500)
    try:_page.wait_for_load_state("networkidle",timeout=5000)
    except:pass
    return _page.content(),_page.url

def get(url):
    try:return http_get(url)
    except Exception as e:
        log.warning("HTTP falhou para %s: %s",url,e)
        if USE_BROWSER=="never":raise
        log.info("Tentando Chromium para: %s",url)
        return browser_get(url)

def extract_links(html,base):
    soup=BeautifulSoup(html,"html.parser"); out=OrderedDict()
    attrs=("href","data-href","data-url","data-link","data-channel-url","data-path","data-target")
    for tag in soup.find_all(True):
        text=clean(tag.get_text(" ",strip=True))
        for attr in attrs:
            v=tag.get(attr)
            if v:
                u=absurl(v,base)
                if page_url(u):out[u]=text
    pattern=r'''(?:href|data-href|data-url|data-link|data-channel-url)\s*=\s*["']([^"']+)["']'''
    for raw in re.findall(pattern,html,re.I):
        u=absurl(raw,base)
        if page_url(u):out.setdefault(u,"")
    return out

def discover_channels(category_url,html):
    links=extract_links(html,category_url); result=OrderedDict()
    category_path=urlparse(category_url).path.rstrip("/").lower()
    category_paths={urlparse(absurl(x)).path.rstrip("/").lower() for x in CATEGORIES.values()}
    for u,text in links.items():
        path=urlparse(u).path.rstrip("/").lower()
        if not path or path==category_path or path in category_paths:continue
        if any(path.startswith(x) for x in BAD):continue
        if any(x in path for x in ("/api/","/assets/","/static/","/wp-","/feed")):continue
        if len([x for x in path.split("/") if x])>2:continue
        name=clean(text)
        if len(name)>120:name=name[:120].rsplit(" ",1)[0]
        result[u]=name
    return result

def channel_title(html,fallback):
    soup=BeautifulSoup(html,"html.parser")
    for tag in soup.find_all(["h1","h2"]):
        t=clean(tag.get_text(" ",strip=True))
        if t:
            t=re.sub(r"\s*[—-]\s*ao vivo.*$","",t,flags=re.I)
            return clean(t)
    t=soup.title.get_text(" ",strip=True) if soup.title else ""
    t=re.sub(r"\s*\|\s*Televisão\.TV.*$","",t,flags=re.I)
    return clean(t) or clean(fallback) or "Canal"

def logo(html,page):
    soup=BeautifulSoup(html,"html.parser")
    for m in soup.find_all("meta"):
        k=(m.get("property") or m.get("name") or "").lower();v=m.get("content")
        if v and k in ("og:image","twitter:image","twitter:image:src"):return absurl(v,page)
    for img in soup.find_all("img"):
        v=img.get("src") or img.get("data-src")
        if v:return absurl(v,page)
    return ""

def stream_candidates(html,page):
    soup=BeautifulSoup(html,"html.parser"); found=[]
    for tag in soup.find_all(True):
        for attr in ("src","href","data-src","data-url","data-file","data-stream","data-video","data-hls","data-m3u8"):
            v=tag.get(attr)
            if not v:continue
            u=absurl(v,page);low=u.lower()
            if any(x in low for x in (".m3u8",".mpd",".m3u")):found.append(u)
            elif tag.name=="iframe":found.append(u)
    blob=html.replace("\\/","/").replace("\\u0026","&")
    for pat in [r'''https?://[^"'<>\s\\]+?\.m3u8(?:\?[^"'<>\s\\]*)?''',
                r'''https?://[^"'<>\s\\]+?\.mpd(?:\?[^"'<>\s\\]*)?''',
                r'''https?://[^"'<>\s\\]+?\.m3u(?:\?[^"'<>\s\\]*)?''']:
        found+=re.findall(pat,blob,re.I)
    return list(dict.fromkeys(x for x in found if x.startswith(("http://","https://"))))

def resolve_player(url,depth=0):
    if depth>3:return ""
    if any(x in url.lower() for x in (".m3u8",".mpd",".m3u")):return url
    try:
        html,final=get(url)
        for u in stream_candidates(html,final):
            if any(x in u.lower() for x in (".m3u8",".mpd",".m3u")):return u
            z=resolve_player(u,depth+1)
            if z:return z
    except Exception as e:log.debug("Player %s: %s",url,e)
    return ""

def find_stream(html,page):
    c=stream_candidates(html,page)
    for u in c:
        if any(x in u.lower() for x in (".m3u8",".mpd",".m3u")):return u
    for u in c:
        z=resolve_player(u)
        if z:return z
    return ""

def alive(url):
    try:
        r=s.get(url,stream=True,timeout=TIMEOUT,allow_redirects=True,
                headers={"User-Agent":UA,"Referer":BASE,"Accept":"*/*"})
        ok=r.status_code in (200,206);r.close();return ok
    except:return False

def save(x):STATUS.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")

def main():
    started=time.time()
    m={"categories":len(CATEGORIES),"category_pages_ok":0,"category_pages_failed":0,
       "channel_links_found":0,"channel_pages_unique":0,"streams_found":0,"streams_active":0}
    log.info("Baixando catálogo: %s",BASE);get(BASE)
    channels=OrderedDict();debug=[]
    category_paths={urlparse(absurl(x)).path.rstrip("/").lower() for x in CATEGORIES.values()}
    for cat,urlpath in CATEGORIES.items():
        url=absurl(urlpath)
        try:
            html,final=get(url);m["category_pages_ok"]+=1
            found=discover_channels(final,html);m["channel_links_found"]+=len(found)
            debug.append({"name":cat,"url":final,"links":len(found)})
            log.info("%s -> %d links de canais",cat,len(found))
            for u,name in found.items():
                rec=channels.setdefault(u,{"url":u,"name":name,"categories":[]})
                if cat not in rec["categories"]:rec["categories"].append(cat)
        except Exception as e:
            m["category_pages_failed"]+=1;debug.append({"name":cat,"url":url,"error":str(e)})
            log.warning("Categoria %s falhou: %s",cat,e)
    m["channel_pages_unique"]=len(channels);log.info("Páginas únicas de canais: %d",len(channels))
    if not channels:
        st={"success":False,"source":BASE,"error_stage":"channel_discovery",
            "error":"Nenhuma página de canal foi identificada.","metrics":m,"categories":debug,
            "preserved_previous_m3u":OUT.exists(),
            "generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
        save(st)
        if OUT.exists():return
        raise RuntimeError(st["error"])
    active=[]
    for i,rec in enumerate(channels.values(),1):
        try:
            html,final=get(rec["url"]);rec["page"]=final;rec["name"]=channel_title(html,rec["name"])
            rec["logo"]=logo(html,final);rec["stream"]=find_stream(html,final)
            if rec["stream"]:
                m["streams_found"]+=1;rec["active"]=alive(rec["stream"])
                if rec["active"]:m["streams_active"]+=1;active.append(rec);state="ATIVO"
                else:state="INATIVO"
            else:rec["active"]=False;state="SEM STREAM"
            log.info("[%d/%d] %s -> %s",i,len(channels),rec["name"],state)
        except Exception as e:rec["active"]=False;rec["error"]=str(e);log.warning("[%d/%d] %s -> ERRO: %s",i,len(channels),rec["name"],e)
    if not active:
        st={"success":False,"source":BASE,"error_stage":"stream_validation",
            "error":"Nenhum stream ativo foi validado.","metrics":m,"categories":debug,
            "preserved_previous_m3u":OUT.exists(),
            "generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
        save(st)
        if OUT.exists():return
        raise RuntimeError(st["error"])
    lines=["#EXTM3U","# Fonte: https://televisao.tv/"]
    for rec in sorted(active,key=lambda x:((x["categories"] or ["Outros"])[0].lower(),x["name"].lower())):
        for cat in rec["categories"] or ["Outros"]:
            n=rec["name"].replace('"',"'");l=rec.get("logo","").replace('"',"'");g=cat.replace('"',"'")
            lines += [f'#EXTINF:-1 tvg-name="{n}" tvg-logo="{l}" group-title="{g}",{n}',rec["stream"]]
    OUT.write_text("\n".join(lines)+"\n",encoding="utf-8")
    save({"success":True,"source":BASE,"generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
          "elapsed_seconds":round(time.time()-started,2),"metrics":m,"categories":debug,
          "active_channels":len(active),"inactive_channels":len(channels)-len(active),
          "channels":[{"name":r["name"],"page":r.get("page",r["url"]),"categories":r["categories"],
                      "logo":r.get("logo",""),"stream":r.get("stream",""),
                      "active":r.get("active",False),"error":r.get("error","")} for r in sorted(channels.values(),key=lambda x:x["name"].lower())]})
    log.info("CONCLUÍDO: %d canais ativos",len(active))

if __name__=="__main__":
    try:main()
    except Exception as e:logging.exception("Falha: %s",e);sys.exit(1)
