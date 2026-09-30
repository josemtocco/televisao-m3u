import asyncio
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

from playwright.async_api import async_playwright

BASE = "https://televisao.tv/"
OUTPUT = Path("canais.m3u")
STATUS = Path("status.json")

CATEGORIES = [
    ("Notícias", "/canais-de-noticias"),
    ("Entretenimento", "/canais-de-entretenimento"),
    ("Filmes", "/canais-de-filmes"),
    ("Música", "/canais-de-musica"),
    ("Esporte", "/canais-de-esporte"),
    ("Infantil", "/canais-infantis"),
    ("Cultura", "/canais-de-cultura"),
    ("Regionais", "/canais-regionais"),
    ("Canais católicos", "/canais-catolicos"),
    ("Evangélica", "/canais-evangelicos"),
    ("Portugal", "/canais-de-portugal"),
    ("Moçambique", "/canais-de-mocambique"),
    ("Angola", "/canais-de-angola"),
    ("Desenhos Online", "/desenhos-online"),
]

BLOCKED = {
    "", "sobre", "adicionar-tv", "contato", "politica-de-privacidade",
    "termos-de-uso", "dmca", "sitemap", "login", "register", "buscar",
    "favoritos", "canais", "categoria", "categorias", "api"
}
MEDIA_RE = re.compile(r"\.(?:m3u8|mpd|m3u)(?:$|[?#])", re.I)
URL_RE = re.compile(
    r"""https?://[^"'\\<>\s]+?\.(?:m3u8|mpd|m3u)(?:\?[^"'\\<>\s]*)?""",
    re.I
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("televisao")

def clean(s):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    s = re.sub(r"\s*(?:—|-)?\s*ao vivo\s*$", "", s, flags=re.I)
    return s.strip(" -|")

def normalize(url, base=BASE):
    if not url or url.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
        return None
    u = urljoin(base, url)
    p = urlparse(u)
    if p.scheme not in ("http", "https") or p.netloc.lower() != "televisao.tv":
        return None
    path = re.sub(r"/+", "/", p.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return "https://televisao.tv" + path

def is_channel(url):
    p = urlparse(url)
    parts = [unquote(x).strip() for x in p.path.split("/") if x.strip()]
    if len(parts) != 1:
        return False
    slug = parts[0].lower()
    return slug not in BLOCKED and "." not in slug and not slug.startswith(("canais-", "desenhos-"))

def streams_from_text(text):
    out = []
    for u in URL_RE.findall(text or ""):
        u = u.replace("\\/", "/").replace("&amp;", "&").rstrip("),;")
        if u not in out:
            out.append(u)
    return out

async def category_links(page, category_url):
    await page.goto(category_url, wait_until="domcontentloaded", timeout=45000)
    try:
        await page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass

    # IMPORTANTE: somente links VISÍVEIS. O site mantém um catálogo global
    # de ~370 canais no DOM de todas as categorias; ele não representa a categoria atual.
    rows = await page.locator("a:visible").evaluate_all(
        """els => els.map(a => ({
            href: a.href || "",
            text: (a.innerText || a.textContent || "").trim(),
            aria: a.getAttribute("aria-label") || ""
        })).filter(x => x.href)"""
    )

    found = {}
    for row in rows:
        u = normalize(row["href"], category_url)
        if u and is_channel(u):
            found[u] = clean(row["text"] or row["aria"])
    return found

async def collect_channel(context, url, categories, suggested_name):
    page = await context.new_page()
    result = {
        "url": url, "name": "", "logo": "", "categories": categories,
        "streams": [], "page_ok": False, "error": ""
    }
    network = set()

    async def response_handler(resp):
        if MEDIA_RE.search(resp.url):
            network.add(resp.url)

    page.on("response", response_handler)
    try:
        # Não espera networkidle: players de vídeo podem manter conexões abertas indefinidamente.
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)

        for sel in ("h1", "h2", "meta[property='og:title']", "title"):
            try:
                loc = page.locator(sel).first
                value = await loc.get_attribute("content") if sel.startswith("meta") else await loc.inner_text()
                if value:
                    result["name"] = clean(value)
                    if result["name"]:
                        break
            except Exception:
                pass
        result["name"] = result["name"] or suggested_name or clean(urlparse(url).path[1:].replace("-", " ").title())

        for sel, attr in [
            ("meta[property='og:image']", "content"),
            ("meta[name='twitter:image']", "content"),
        ]:
            try:
                value = await page.locator(sel).first.get_attribute(attr)
                if value:
                    result["logo"] = urljoin(url, value)
                    break
            except Exception:
                pass

        # Dá alguns segundos para o player emitir a requisição do stream.
        await page.wait_for_timeout(2500)

        html = await page.content()
        candidates = set(streams_from_text(html))
        candidates.update(network)

        # URLs de vídeo/iframe presentes no DOM.
        attrs = await page.locator("video,source,iframe,embed,object").evaluate_all(
            """els => els.flatMap(e => [
                e.src, e.currentSrc, e.getAttribute("src"),
                e.getAttribute("data-src"), e.getAttribute("data-url"),
                e.getAttribute("data-stream")
            ]).filter(Boolean)"""
        )

        # Examina iframes/player pages rapidamente.
        for raw in attrs:
            if MEDIA_RE.search(raw):
                candidates.add(raw)
            elif raw.startswith("http") and raw != url:
                child = await context.new_page()
                child_network = set()
                async def child_response(resp):
                    if MEDIA_RE.search(resp.url):
                        child_network.add(resp.url)
                child.on("response", child_response)
                try:
                    await child.goto(raw, wait_until="domcontentloaded", timeout=10000)
                    await child.wait_for_timeout(1200)
                    candidates.update(child_network)
                    candidates.update(streams_from_text(await child.content()))
                    for x in await child.locator("video,source").evaluate_all(
                        """els => els.flatMap(e => [e.src,e.currentSrc,e.getAttribute("src")]).filter(Boolean)"""
                    ):
                        if MEDIA_RE.search(x):
                            candidates.add(x)
                except Exception:
                    pass
                finally:
                    await child.close()

        for script in await page.locator("script").all_text_contents():
            candidates.update(streams_from_text(script))

        result["streams"] = list(candidates)[:20]
        result["page_ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        page.remove_listener("response", response_handler)
        await page.close()
    return result

def make_m3u(channels):
    lines = ["#EXTM3U"]
    seen = set()
    for c in sorted(channels, key=lambda x: ((x["categories"] or [""])[0].lower(), x["name"].lower())):
        for category in c["categories"] or ["Televisão.TV"]:
            name = c["name"].replace('"', "'")
            logo = (c["logo"] or "").replace('"', "'")
            group = category.replace('"', "'")
            for stream in c["streams"]:
                key = (name, group, stream)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}')
                lines.append(stream)
    return "\n".join(lines) + "\n"

async def main():
    metrics = {
        "categories": len(CATEGORIES),
        "category_pages_ok": 0,
        "category_pages_failed": 0,
        "channel_links_found": 0,
        "channel_pages_unique": 0,
        "streams_found": 0,
        "streams_active": 0
    }
    category_stats = []
    channels = defaultdict(lambda: {"name": "", "categories": []})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()

        for category, path in CATEGORIES:
            url = urljoin(BASE, path.lstrip("/"))
            try:
                links = await category_links(page, url)
                metrics["category_pages_ok"] += 1
                metrics["channel_links_found"] += len(links)
                examples = list(links.items())[:5]
                log.info("%s -> %d links | exemplos: %s", category, len(links), examples)
                for u, name in links.items():
                    if name and not channels[u]["name"]:
                        channels[u]["name"] = name
                    if category not in channels[u]["categories"]:
                        channels[u]["categories"].append(category)
                category_stats.append({
                    "name": category, "url": url, "links": len(links),
                    "examples": [u for u, _ in examples]
                })
            except Exception as exc:
                metrics["category_pages_failed"] += 1
                log.exception("Falha em %s: %s", category, exc)
                category_stats.append({"name": category, "url": url, "links": 0, "error": str(exc)})

        metrics["channel_pages_unique"] = len(channels)
        log.info("Páginas únicas de canais: %d", len(channels))

        if not channels:
            status = {
                "success": False, "source": BASE, "error_stage": "channel_discovery",
                "error": "Nenhuma página de canal foi identificada.",
                "metrics": metrics, "categories": category_stats
            }
            STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            await browser.close()
            return 1

        # Muito mais rápido: 20 canais simultâneos e sem HEAD/GET de 15 segundos.
        sem = asyncio.Semaphore(20)

        async def run_one(u, info):
            async with sem:
                return await collect_channel(context, u, info["categories"], info["name"])

        tasks = [asyncio.create_task(run_one(u, info)) for u, info in channels.items()]
        results = []
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            result = await task
            results.append(result)
            metrics["streams_found"] += len(result["streams"])
            if result["streams"]:
                metrics["streams_active"] += 1
            if i % 25 == 0 or i == len(tasks):
                log.info("Processados %d/%d | canais com stream: %d", i, len(tasks), metrics["streams_active"])

        active = [x for x in results if x["streams"]]
        status = {
            "success": bool(active),
            "source": BASE,
            "error_stage": None if active else "stream_discovery",
            "error": None if active else "Nenhum stream encontrado nas páginas/players.",
            "metrics": metrics,
            "categories": category_stats,
            "channels": results,
        }
        STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

        if active:
            playlist = make_m3u(active)
            OUTPUT.write_text(playlist, encoding="utf-8")
            log.info("M3U gerada com %d entradas.", playlist.count("#EXTINF:"))
        else:
            log.error(status["error"])

        await browser.close()
        return 0 if active else 1

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
