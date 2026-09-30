#!/usr/bin/env python3
"""
Coletor Televisao.TV -> M3U para SS IPTV.

Recursos:
- tenta HTTP normal com User-Agent de navegador;
- em 403/429/5xx, tenta novamente;
- se necessário, usa Chromium via Playwright;
- descobre categorias e canais do catálogo;
- extrai m3u8/mpd/m3u de páginas/iframes/scripts;
- valida os streams;
- preserva a última M3U válida se a fonte estiver temporariamente indisponível;
- gera canais.m3u e status.json.
"""

import json
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://televisao.tv/"
OUT = Path("canais.m3u")
STATUS = Path("status.json")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "25"))
WORKERS = int(os.getenv("WORKERS", "8"))
USE_BROWSER = os.getenv("USE_BROWSER", "auto").lower()
MAX_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

CATEGORY_HINTS = [
    "canais-de-noticias",
    "canais-de-entretenimento",
    "canais-de-filmes",
    "canais-de-musica",
    "canais-de-esporte",
    "desenhos-online",
    "canais-de-cultura",
    "canais-regionais",
    "canais-catolicos",
    "canais-evangelica",
    "canais-de-portugal",
    "canais-de-mocambique",
    "canais-de-angola",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("televisao-m3u")

session = requests.Session()
session.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
})


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_url(url):
    if not url:
        return ""
    url = url.strip().replace("\\/", "/")
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE, url)


def get_http(url):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            headers = {
                "Referer": BASE if url != BASE else "https://www.google.com/",
            }
            r = session.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            if r.status_code in (403, 429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    f"HTTP {r.status_code} para {url}", response=r
                )
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            return r.text, r.url
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * attempt)
    raise last_error


_browser = None
_browser_page = None


def get_browser(url):
    global _browser, _browser_page

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright não instalado. Execute: pip install playwright "
            "e playwright install chromium"
        ) from exc

    if _browser_page is None:
        _browser = sync_playwright().start()
        browser = _browser.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        _browser_page = browser.new_page(
            user_agent=BROWSER_UA,
            locale="pt-BR",
            viewport={"width": 1366, "height": 768},
        )

    _browser_page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
    _browser_page.wait_for_timeout(1500)
    return _browser_page.content(), _browser_page.url


def get(url):
    """HTTP primeiro; Chromium como fallback para 403/anti-bot/JS."""
    try:
        return get_http(url)
    except Exception as http_error:
        log.warning("HTTP falhou para %s: %s", url, http_error)

        if USE_BROWSER == "never":
            raise

        try:
            log.info("Tentando Chromium para: %s", url)
            return get_browser(url)
        except Exception as browser_error:
            raise RuntimeError(
                f"HTTP e Chromium falharam para {url}: "
                f"{browser_error}"
            ) from http_error


def discover_categories(html):
    soup = BeautifulSoup(html, "html.parser")
    cats = OrderedDict()

    for a in soup.find_all("a", href=True):
        href = norm_url(a.get("href"))
        path = urlparse(href).path.strip("/")
        text = clean(a.get_text(" ", strip=True))

        if not path:
            continue
        if path in {
            "sobre", "contato", "termos-de-uso",
            "politica-de-privacidade", "copyright",
        }:
            continue

        if (
            ("canais" in path or "desenhos" in path)
            and "/sitemap" not in path
            and "/buscar" not in path
            and text
            and len(text) < 100
        ):
            cats[href] = text

    for slug in CATEGORY_HINTS:
        cats.setdefault(
            urljoin(BASE, slug),
            clean(slug.replace("-", " ").title()),
        )

    return cats


def discover_channels(category_url, html):
    soup = BeautifulSoup(html, "html.parser")
    result = OrderedDict()
    base_host = urlparse(BASE).netloc

    for a in soup.find_all("a", href=True):
        href = norm_url(a.get("href"))
        path = urlparse(href).path.strip("/")
        text = clean(a.get_text(" ", strip=True))

        if not path or not text:
            continue

        if path.startswith((
            "canais-de-",
            "canais-catolicos",
            "canais-evangelica",
            "desenhos-online",
        )):
            continue

        if path in {
            "sobre", "contato", "adicionar-tv",
            "termos-de-uso", "politica-de-privacidade",
            "copyright",
        }:
            continue

        if urlparse(href).netloc != base_host:
            continue

        if len(path.split("/")) == 1:
            result[href] = text

    return result


def extract_logo(soup):
    for meta in soup.find_all("meta"):
        if meta.get("property") in {"og:image", "twitter:image"}:
            if meta.get("content"):
                return norm_url(meta["content"])

    for img in soup.find_all("img", src=True):
        src = norm_url(img.get("src"))
        alt = clean(img.get("alt"))
        if src and (alt or "logo" in src.lower()):
            return src

    return ""


def extract_stream(soup, page_url):
    candidates = []

    attrs = (
        "src", "data-src", "data-url", "data-stream",
        "data-video", "data-file", "href",
    )

    for tag in soup.find_all(True):
        for attr in attrs:
            value = tag.get(attr)
            if not value:
                continue

            v = norm_url(value)
            lv = v.lower()

            if any(
                x in lv for x in (
                    ".m3u8", ".mpd", ".m3u", ".mp4",
                    "youtube.com/embed",
                    "youtube-nocookie.com/embed",
                    "player.vimeo.com",
                )
            ):
                candidates.append(v)

    scripts = "\n".join(
        (s.string or s.get_text() or "")
        for s in soup.find_all("script")
    )

    blob = scripts + "\n" + str(soup)

    patterns = [
        r'https?://[^"\'>\s\\]+?\.m3u8(?:\?[^"\'>\s\\]*)?',
        r'https?://[^"\'>\s\\]+?\.mpd(?:\?[^"\'>\s\\]*)?',
        r'https?://[^"\'>\s\\]+?\.m3u(?:\?[^"\'>\s\\]*)?',
    ]

    for pattern in patterns:
        candidates.extend(re.findall(pattern, blob, re.I))

    fixed = []
    for candidate in candidates:
        candidate = (
            candidate
            .replace("\\/", "/")
            .replace("\\u0026", "&")
            .rstrip("\\")
        )
        if candidate.startswith(("http://", "https://")):
            fixed.append(candidate)

    fixed = list(dict.fromkeys(fixed))

    manifests = [
        u for u in fixed
        if any(x in u.lower() for x in (".m3u8", ".mpd", ".m3u"))
    ]

    return (manifests + fixed)[0] if (manifests or fixed) else ""


def resolve_stream(url, depth=0):
    if not url or depth > 3:
        return ""

    low = url.lower()

    if any(x in low for x in (".m3u8", ".mpd", ".m3u")):
        return url

    try:
        html, final = get(url)
        soup = BeautifulSoup(html, "html.parser")
        found = extract_stream(soup, final)

        if found and found != url:
            return resolve_stream(found, depth + 1) or found

    except Exception as exc:
        log.debug("Não foi possível resolver %s: %s", url, exc)

    return ""


def stream_alive(url):
    if not url.startswith(("http://", "https://")):
        return False

    try:
        r = session.get(
            url,
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=True,
            headers={
                "User-Agent": BROWSER_UA,
                "Referer": BASE,
                "Accept": "*/*",
            },
        )

        status = r.status_code
        ctype = (r.headers.get("content-type") or "").lower()

        # Alguns servidores HLS/DASH não enviam content-type perfeito.
        ok_type = (
            not ctype
            or "text" in ctype
            or "mpegurl" in ctype
            or "octet-stream" in ctype
            or "video" in ctype
            or "application/vnd.apple" in ctype
        )

        r.close()
        return status in (200, 206) and ok_type

    except requests.RequestException:
        return False


def esc(s):
    return clean(s).replace('"', "'")


def write_status(data):
    STATUS.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    started = time.time()

    log.info("Baixando catálogo: %s", BASE)

    try:
        home, _ = get(BASE)
    except Exception as exc:
        # Não destrói uma M3U válida só porque a fonte está temporariamente
        # bloqueando o GitHub Actions.
        log.error("Não foi possível acessar a fonte: %s", exc)

        status = {
            "source": BASE,
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            "source_error": str(exc),
            "preserved_previous_m3u": OUT.exists(),
            "success": False,
        }
        write_status(status)

        if OUT.exists():
            log.warning(
                "Mantendo a última canais.m3u válida. "
                "A execução será considerada parcialmente concluída."
            )
            return

        raise

    categories = discover_categories(home)
    log.info("Categorias descobertas: %d", len(categories))

    channels = OrderedDict()

    for cat_url, cat_name in categories.items():
        try:
            html, final = get(cat_url)
        except Exception as exc:
            log.warning(
                "Categoria indisponível: %s (%s)",
                cat_url,
                exc,
            )
            continue

        found = discover_channels(final, html)
        log.info("%s -> %d links", cat_name, len(found))

        for url, name in found.items():
            rec = channels.setdefault(
                url,
                {
                    "url": url,
                    "name": name,
                    "categories": [],
                },
            )

            if cat_name not in rec["categories"]:
                rec["categories"].append(cat_name)

    log.info("Canais únicos encontrados: %d", len(channels))

    if not channels:
        raise RuntimeError(
            "Nenhum canal foi encontrado. "
            "A estrutura do site pode ter mudado."
        )

    alive = []

    for i, (url, rec) in enumerate(channels.items(), 1):
        try:
            html, final = get(url)
            soup = BeautifulSoup(html, "html.parser")

            stream = extract_stream(soup, final)

            if stream and not any(
                ext in stream.lower()
                for ext in (".m3u8", ".mpd", ".m3u")
            ):
                stream = resolve_stream(stream)

            rec["logo"] = extract_logo(soup)
            rec["stream"] = stream
            rec["page"] = final

            if stream and stream_alive(stream):
                rec["active"] = True
                alive.append(rec)
                state = "ATIVO"
            else:
                rec["active"] = False
                state = "INATIVO"

            log.info(
                "[%d/%d] %-40s %s",
                i,
                len(channels),
                rec["name"][:40],
                state,
            )

        except Exception as exc:
            rec["active"] = False
            rec["error"] = str(exc)

            log.warning(
                "[%d/%d] %s: %s",
                i,
                len(channels),
                rec["name"],
                exc,
            )

    # Se houve uma falha geral de acesso aos canais e nenhum stream foi
    # encontrado, não substitui uma lista anterior válida.
    if not alive and OUT.exists():
        log.error(
            "Nenhum canal ativo foi validado. "
            "Mantendo a última M3U válida."
        )

        write_status({
            "source": BASE,
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            "catalog_channels": len(channels),
            "active_channels": 0,
            "success": False,
            "preserved_previous_m3u": True,
            "reason": "Nenhum stream ativo foi validado.",
        })
        return

    lines = [
        "#EXTM3U",
        "# Generated automatically from https://televisao.tv/",
        f"# Channels active: {len(alive)}",
    ]

    for rec in sorted(
        alive,
        key=lambda x: (
            (x["categories"] or ["Outros"])[0].lower(),
            x["name"].lower(),
        ),
    ):
        for cat in rec["categories"] or ["Outros"]:
            lines.append(
                '#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" '
                'group-title="{group}",{name}'.format(
                    name=esc(rec["name"]),
                    logo=esc(rec.get("logo", "")),
                    group=esc(cat),
                )
            )
            lines.append(rec["stream"])

    OUT.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    status = {
        "source": BASE,
        "generated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
        "catalog_channels": len(channels),
        "active_channels": len(alive),
        "inactive_channels": len(channels) - len(alive),
        "categories": len(categories),
        "elapsed_seconds": round(time.time() - started, 2),
        "success": True,
        "channels": [
            {
                k: rec.get(k)
                for k in (
                    "name",
                    "page",
                    "stream",
                    "logo",
                    "categories",
                    "active",
                )
            }
            for rec in sorted(
                channels.values(),
                key=lambda x: x["name"].lower(),
            )
        ],
    }

    write_status(status)

    log.info(
        "Gerado %s com %d canais ativos.",
        OUT,
        len(alive),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.exception("Falha: %s", exc)
        sys.exit(1)
