#!/usr/bin/env python3
import json, logging, re, sys, time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://televisao.tv/"
OUT = Path("canais.m3u")
STATUS = Path("status.json")
TIMEOUT = int(__import__('os').getenv("HTTP_TIMEOUT", "20"))
WORKERS = int(__import__('os').getenv("WORKERS", "12"))
USER_AGENT = "Mozilla/5.0 (compatible; TelevisaoTV-M3U-Bot/1.0; +https://github.com/)"

# Slugs conhecidos do menu do Televisao.TV. O scraper também tenta descobrir
# categorias automaticamente pela navegação do site.
CATEGORY_HINTS = [
    "canais-de-noticias", "canais-de-entretenimento", "canais-de-filmes",
    "canais-de-musica", "canais-de-esporte", "desenhos-online",
    "canais-de-cultura", "canais-regionais", "canais-catolicos",
    "canais-evangelica", "canais-de-portugal", "canais-de-mocambique",
    "canais-de-angola", "canais-religiosos"
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("televisao-m3u")

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"})


def get(url):
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    if not r.encoding:
        r.encoding = "utf-8"
    return r.text, r.url


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE, url)


def slug_to_category(text, href=""):
    t = clean(text)
    if t and t.lower() not in {"ver canais", "mostrar descrição"}:
        return t
    p = urlparse(href).path.strip("/")
    return clean(p.replace("-", " ").title()) if p else "Outros"


def discover_categories(html):
    soup = BeautifulSoup(html, "html.parser")
    cats = OrderedDict()
    # Links de navegação que apontam para páginas de categoria.
    for a in soup.find_all("a", href=True):
        href = norm_url(a.get("href"))
        path = urlparse(href).path.strip("/")
        text = clean(a.get_text(" ", strip=True))
        if not path or path in {"sobre", "contato", "termos-de-uso", "politica-de-privacidade"}:
            continue
        if ("canais" in path or "desenhos" in path) and not any(x in path for x in ["/sitemap", "/buscar"]):
            if text and len(text) < 80:
                cats[href] = text
    # Fallback para categorias conhecidas.
    for slug in CATEGORY_HINTS:
        url = urljoin(BASE, slug)
        cats.setdefault(url, slug_to_category("", url))
    return cats


def discover_channels(category_url, html):
    soup = BeautifulSoup(html, "html.parser")
    result = OrderedDict()
    # Na categoria, os links dos cards apontam para /slug-do-canal.
    for a in soup.find_all("a", href=True):
        href = norm_url(a.get("href"))
        path = urlparse(href).path.strip("/")
        text = clean(a.get_text(" ", strip=True))
        if not path or not text:
            continue
        if path.startswith(("canais-de-", "canais-catolicos", "canais-evangelica", "desenhos-online")):
            continue
        if path in {"", "sobre", "contato", "adicionar-tv", "termos-de-uso", "politica-de-privacidade", "copyright"}:
            continue
        # Cards normalmente possuem nome curto + nome do canal.
        # Evita links de navegação externos.
        if urlparse(href).netloc and urlparse(href).netloc != urlparse(BASE).netloc:
            continue
        # Descartar âncoras e links de busca.
        if href.startswith(BASE) and len(path.split("/")) == 1:
            result[href] = text
    return result


def extract_logo(soup):
    for meta in soup.find_all("meta"):
        if meta.get("property") in {"og:image", "twitter:image"} and meta.get("content"):
            return norm_url(meta["content"])
    for img in soup.find_all("img", src=True):
        src = norm_url(img.get("src"))
        alt = clean(img.get("alt"))
        if src and (alt or "logo" in src.lower()):
            return src
    return ""


def extract_stream(soup, page_url):
    candidates = []
    attrs = ("src", "data-src", "data-url", "data-stream", "data-video", "href")
    for tag in soup.find_all(True):
        for attr in attrs:
            value = tag.get(attr)
            if not value:
                continue
            v = norm_url(value)
            lv = v.lower()
            if any(x in lv for x in [".m3u8", ".mpd", ".m3u", ".mp4", "youtube.com/embed", "youtube-nocookie.com/embed", "player.vimeo.com"]):
                candidates.append(v)
    # Scripts e JSON inline: procura URLs de manifest/player.
    raw = "\n".join(x for x in soup.stripped_strings)
    scripts = "\n".join((s.string or s.get_text()) for s in soup.find_all("script"))
    blob = raw + "\n" + scripts
    patterns = [
        r'https?[^\s\"\'<>\\]+?\.m3u8(?:\?[^\s\"\'<>\\]*)?',
        r'https?[^\s\"\'<>\\]+?\.mpd(?:\?[^\s\"\'<>\\]*)?',
        r'https?[^\s\"\'<>\\]+?\.m3u(?:\?[^\s\"\'<>\\]*)?',
    ]
    for pat in patterns:
        candidates.extend(re.findall(pat, blob, flags=re.I))
    # Remove escaped characters.
    fixed = []
    for c in candidates:
        c = c.replace('\\/', '/').replace('\\u0026', '&').rstrip('\\')
        if c.startswith("http"):
            fixed.append(c)
    # Prefer HLS/DASH direct manifests; then iframe players.
    fixed = list(dict.fromkeys(fixed))
    direct = [u for u in fixed if ".m3u8" in u.lower() or ".mpd" in u.lower() or ".m3u" in u.lower()]
    return (direct + fixed)[0] if (direct or fixed) else ""


def resolve_stream(url, depth=0):
    """Resolve uma URL de player/iframe até um manifesto de vídeo quando possível."""
    if not url or depth > 2:
        return ""
    low = url.lower()
    if any(ext in low for ext in (".m3u8", ".mpd", ".m3u")):
        return url
    try:
        html, final = get(url)
        soup = BeautifulSoup(html, "html.parser")
        found = extract_stream(soup, final)
        if found and found != url:
            return resolve_stream(found, depth + 1) or found
    except requests.RequestException:
        pass
    return ""


def stream_alive(url):
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        r = session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
        ctype = (r.headers.get("content-type") or "").lower()
        status = r.status_code
        r.close()
        # HLS/DASH may return 200, 206 or occasionally 3xx after redirect.
        return status in (200, 206) and ("text" in ctype or "mpegurl" in ctype or "octet-stream" in ctype or "video" in ctype or not ctype)
    except requests.RequestException:
        return False


def esc(s):
    return clean(s).replace('"', "'")


def main():
    started = time.time()
    log.info("Baixando catálogo: %s", BASE)
    home, _ = get(BASE)
    categories = discover_categories(home)
    log.info("Categorias descobertas: %d", len(categories))

    # channel_url -> record; categorias é uma lista porque um canal pode estar em várias categorias.
    channels = OrderedDict()
    for cat_url, cat_name in categories.items():
        try:
            html, final = get(cat_url)
        except Exception as e:
            log.warning("Categoria indisponível: %s (%s)", cat_url, e)
            continue
        found = discover_channels(final, html)
        log.info("%s -> %d links", cat_name, len(found))
        for url, name in found.items():
            rec = channels.setdefault(url, {"url": url, "name": name, "categories": []})
            if cat_name not in rec["categories"]:
                rec["categories"].append(cat_name)

    log.info("Canais únicos encontrados: %d", len(channels))
    if not channels:
        raise RuntimeError("Nenhum canal foi encontrado. O site pode ter alterado sua estrutura.")

    # Busca páginas dos canais e extrai stream/logo.
    alive = []
    for i, (url, rec) in enumerate(channels.items(), 1):
        try:
            html, final = get(url)
            soup = BeautifulSoup(html, "html.parser")
            stream = extract_stream(soup, final)
            if stream and not any(ext in stream.lower() for ext in (".m3u8", ".mpd", ".m3u")):
                stream = resolve_stream(stream)
            rec["logo"] = extract_logo(soup)
            rec["stream"] = stream
            rec["page"] = final
            if stream and stream_alive(stream):
                alive.append(rec)
                rec["active"] = True
            else:
                rec["active"] = False
            log.info("[%d/%d] %-35s %s", i, len(channels), rec["name"], "ATIVO" if rec["active"] else "INATIVO")
        except Exception as e:
            rec["active"] = False
            rec["error"] = str(e)
            log.warning("[%d/%d] %s: %s", i, len(channels), rec["name"], e)

    # Ordenação por categoria e nome, preservando múltiplas categorias.
    lines = ["#EXTM3U", "# Generated automatically from https://televisao.tv/", f"# Channels active: {len(alive)}"]
    for rec in sorted(alive, key=lambda x: ((x["categories"] or ["Outros"])[0].lower(), x["name"].lower())):
        categories_sorted = rec["categories"] or ["Outros"]
        for cat in categories_sorted:
            lines.append(
                '#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}'.format(
                    name=esc(rec["name"]), logo=esc(rec.get("logo", "")), group=esc(cat)
                )
            )
            lines.append(rec["stream"])
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status = {
        "source": BASE,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "catalog_channels": len(channels),
        "active_channels": len(alive),
        "inactive_channels": len(channels) - len(alive),
        "categories": len(categories),
        "elapsed_seconds": round(time.time() - started, 2),
        "channels": [
            {k: rec.get(k) for k in ["name", "page", "stream", "logo", "categories", "active"]}
            for rec in sorted(channels.values(), key=lambda x: x["name"].lower())
        ],
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Gerado %s com %d entradas ativas", OUT, len(alive) * 1)
    log.info("Relatório: %s", STATUS)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.exception("Falha: %s", exc)
        sys.exit(1)
