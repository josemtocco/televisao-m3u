#!/usr/bin/env python3
"""
Televisao.TV -> M3U para SS IPTV
v3

A estratégia desta versão é:
1. abrir a página inicial;
2. descobrir categorias pelo texto/links da própria página;
3. abrir cada categoria;
4. extrair TODOS os links internos do domínio;
5. distinguir páginas de canais de navegação/arquivos;
6. abrir cada página de canal;
7. procurar iframe/player e URLs de stream;
8. validar os streams;
9. gerar canais.m3u e status.json.

O HTTP é tentado primeiro. Em 403/429/5xx ou conteúdo insuficiente,
usa Chromium/Playwright.
"""

import json
import logging
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

BASE = "https://televisao.tv/"
HOST = "televisao.tv"
OUT = Path("canais.m3u")
STATUS = Path("status.json")

TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
USE_BROWSER = os.getenv("USE_BROWSER", "auto").lower()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("televisao-m3u")

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
})


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def absolute(url, base=BASE):
    if not url:
        return ""
    url = str(url).strip().replace("\\/", "/")
    if url.startswith("//"):
        url = "https:" + url
    return urldefrag(urljoin(base, url))[0]


def same_host(url):
    try:
        return urlparse(url).netloc.lower().split(":")[0] in {
            HOST, "www." + HOST
        }
    except Exception:
        return False


def is_html_url(url):
    p = urlparse(url).path.lower()
    return not p.endswith((
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
        ".css", ".js", ".xml", ".json", ".pdf", ".zip", ".rar",
        ".mp4", ".webm", ".m3u", ".m3u8", ".mpd",
    ))


def http_get(url):
    last = None

    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(
                url,
                timeout=TIMEOUT,
                allow_redirects=True,
                headers={
                    "Referer": BASE,
                    "User-Agent": UA,
                },
            )

            if r.status_code in {403, 429, 500, 502, 503, 504}:
                raise requests.HTTPError(
                    f"HTTP {r.status_code}", response=r
                )

            r.raise_for_status()

            ctype = (r.headers.get("content-type") or "").lower()
            if "html" not in ctype and "<html" not in r.text[:1000].lower():
                raise RuntimeError(
                    f"Resposta não parece HTML: {ctype}"
                )

            r.encoding = r.encoding or "utf-8"
            return r.text, r.url

        except Exception as exc:
            last = exc
            if attempt < RETRIES:
                time.sleep(attempt * 1.5)

    raise last


_playwright = None
_browser = None
_page = None


def browser_get(url):
    global _playwright, _browser, _page

    from playwright.sync_api import sync_playwright

    if _page is None:
        _playwright = sync_playwright().start()

        _browser = _playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = _browser.new_context(
            user_agent=UA,
            locale="pt-BR",
            viewport={"width": 1440, "height": 900},
        )

        _page = context.new_page()

    _page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=TIMEOUT * 1000,
    )

    _page.wait_for_timeout(1200)

    # Permite que scripts que montam o player tenham tempo para executar.
    try:
        _page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    return _page.content(), _page.url


def get(url, force_browser=False):
    if not force_browser:
        try:
            return http_get(url)
        except Exception as exc:
            log.warning("HTTP falhou para %s: %s", url, exc)

            if USE_BROWSER == "never":
                raise

    log.info("Tentando Chromium para: %s", url)
    return browser_get(url)


def links_from_html(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    links = OrderedDict()

    for a in soup.find_all("a", href=True):
        href = absolute(a.get("href"), page_url)
        text = clean(a.get_text(" ", strip=True))

        if not href or not same_host(href):
            continue
        if not is_html_url(href):
            continue

        links[href] = text

    return links


def classify_category_links(home_html, home_url):
    """
    A categoria é identificada principalmente pela navegação da própria
    página. Não depende de uma lista fixa de slugs.
    """
    soup = BeautifulSoup(home_html, "html.parser")
    result = OrderedDict()

    # Primeiro procura elementos de navegação/menu.
    candidates = []

    for a in soup.find_all("a", href=True):
        href = absolute(a.get("href"), home_url)
        text = clean(a.get_text(" ", strip=True))

        if not href or not same_host(href) or not text:
            continue
        if not is_html_url(href):
            continue

        blob = f"{text} {href}".lower()

        # Indicadores típicos das categorias do catálogo.
        if any(word in blob for word in (
            "canal", "notícia", "entreten", "filme", "música",
            "esporte", "infantil", "desenho", "cultura", "regional",
            "católic", "evangé", "evangel", "portugal",
            "moçambique", "mocambique", "angola",
        )):
            candidates.append((href, text))

    # Preserva a ordem visual.
    for href, text in candidates:
        if href == absolute(BASE):
            continue
        result[href] = text

    return result


def channel_score(url, text, category_url):
    """
    Pontuação heurística para diferenciar uma página de canal de:
    login, contato, termos, categorias, busca etc.

    Não exige que o slug tenha a palavra 'tv' ou 'canal'.
    """
    path = urlparse(url).path.strip("/").lower()
    lowtext = text.lower()

    if not path:
        return -100

    forbidden = (
        "sobre", "contato", "copyright", "privacidade",
        "termos", "login", "cadastro", "buscar", "pesquisa",
        "sitemap", "anuncie", "adicionar",
    )

    if any(x in path for x in forbidden):
        return -100

    if url == category_url:
        return -100

    score = 0

    # Páginas simples de um slug tendem a ser páginas de canais.
    if len(path.split("/")) == 1:
        score += 4

    # Texto visível é uma forte indicação.
    if 2 <= len(text) <= 80:
        score += 2

    # Nomes/termos comuns em canais.
    if any(x in lowtext for x in (
        "tv", "sbt", "record", "globo", "band", "cnn",
        "news", "rede", "canal", "radio", "rádio",
    )):
        score += 1

    # Não tratar a própria categoria como canal.
    if any(x in path for x in (
        "canais-", "desenhos-online", "desenhos-animados",
    )):
        score -= 8

    return score


def discover_categories(home_html, home_url):
    categories = classify_category_links(home_html, home_url)

    # Algumas categorias podem aparecer somente no menu renderizado.
    # Se o HTML inicial trouxe poucos resultados, coletamos links gerais.
    if len(categories) < 5:
        for url, text in links_from_html(home_html, home_url).items():
            blob = f"{text} {url}".lower()
            if any(x in blob for x in (
                "notícia", "entreten", "filme", "música", "esporte",
                "infantil", "desenho", "cultura", "regional",
                "católic", "evangé", "portugal", "moçambique", "angola",
            )):
                if text:
                    categories.setdefault(url, text)

    return categories


def discover_channels(category_url, html):
    links = links_from_html(html, category_url)
    channels = OrderedDict()

    for url, text in links.items():
        score = channel_score(url, text, category_url)

        if score >= 4:
            channels[url] = text

    return channels


def extract_logo(soup, page_url):
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content")

        if content and prop in {
            "og:image", "twitter:image", "twitter:image:src"
        }:
            return absolute(content, page_url)

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        alt = clean(img.get("alt", ""))

        if src:
            u = absolute(src, page_url)
            if "logo" in u.lower() or alt:
                return u

    return ""


def extract_streams(html, soup, page_url):
    found = []

    # Atributos HTML.
    for tag in soup.find_all(True):
        for attr in (
            "src", "href", "data-src", "data-url",
            "data-file", "data-stream", "data-video",
            "data-hls", "data-m3u8",
        ):
            value = tag.get(attr)

            if not value:
                continue

            u = absolute(value, page_url)
            low = u.lower()

            if any(x in low for x in (
                ".m3u8", ".mpd", ".m3u",
            )):
                found.append(u)

            # Iframes/players serão resolvidos separadamente.
            elif tag.name == "iframe":
                found.append(u)

    # Scripts e HTML bruto.
    blob = html.replace("\\/", "/").replace("\\u0026", "&")

    patterns = [
        r'https?://[^"\'<>\s\\]+?\.m3u8(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+?\.mpd(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+?\.m3u(?:\?[^"\'<>\s\\]*)?',
    ]

    for pattern in patterns:
        found.extend(re.findall(pattern, blob, flags=re.I))

    return list(dict.fromkeys(
        x for x in found
        if x.startswith(("http://", "https://"))
    ))


def resolve_stream(url, depth=0):
    if depth > 3 or not url:
        return ""

    low = url.lower()

    if any(x in low for x in (".m3u8", ".mpd", ".m3u")):
        return url

    try:
        html, final = get(url)
        soup = BeautifulSoup(html, "html.parser")

        streams = extract_streams(html, soup, final)

        for candidate in streams:
            if any(x in candidate.lower() for x in (
                ".m3u8", ".mpd", ".m3u"
            )):
                return candidate

            resolved = resolve_stream(candidate, depth + 1)
            if resolved:
                return resolved

    except Exception as exc:
        log.debug("Falha ao resolver player %s: %s", url, exc)

    return ""


def get_channel_stream(page_url, html):
    soup = BeautifulSoup(html, "html.parser")

    streams = extract_streams(html, soup, page_url)

    # Primeiro os manifests diretos.
    manifests = [
        x for x in streams
        if any(ext in x.lower() for ext in (".m3u8", ".mpd", ".m3u"))
    ]

    if manifests:
        return manifests[0]

    # Depois os iframes/players.
    for candidate in streams:
        resolved = resolve_stream(candidate)
        if resolved:
            return resolved

    # Se a página contém URL de player em scripts mas não foi capturada,
    # tenta regex de iframe.
    iframe_urls = re.findall(
        r'<iframe[^>]+(?:src|data-src)=["\']([^"\']+)',
        html,
        flags=re.I,
    )

    for iframe in iframe_urls:
        iframe = absolute(iframe, page_url)
        resolved = resolve_stream(iframe)
        if resolved:
            return resolved

    return ""


def stream_alive(url):
    try:
        r = session.get(
            url,
            stream=True,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers={
                "User-Agent": UA,
                "Referer": BASE,
                "Accept": "*/*",
            },
        )

        status = r.status_code
        ctype = (r.headers.get("content-type") or "").lower()

        r.close()

        if status not in (200, 206):
            return False

        # HLS/DASH costuma retornar estes tipos; alguns hosts retornam
        # octet-stream ou até deixam o tipo ausente.
        return (
            not ctype
            or "mpegurl" in ctype
            or "dash" in ctype
            or "video" in ctype
            or "octet-stream" in ctype
            or "text" in ctype
            or "application" in ctype
        )

    except Exception:
        return False


def extinf(name, logo, group):
    name = clean(name).replace('"', "'")
    logo = clean(logo).replace('"', "'")
    group = clean(group).replace('"', "'")

    return (
        f'#EXTINF:-1 tvg-name="{name}" '
        f'tvg-logo="{logo}" group-title="{group}",{name}'
    )


def save_status(status):
    STATUS.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    started = time.time()

    metrics = {
        "categories": 0,
        "category_pages_ok": 0,
        "category_pages_failed": 0,
        "channel_pages_discovered": 0,
        "channel_pages_unique": 0,
        "streams_found": 0,
        "streams_active": 0,
    }

    log.info("Baixando catálogo: %s", BASE)

    try:
        home_html, home_url = get(BASE)
    except Exception as exc:
        log.error("Não foi possível acessar a página inicial: %s", exc)

        save_status({
            "success": False,
            "source": BASE,
            "error_stage": "home",
            "error": str(exc),
            "preserved_previous_m3u": OUT.exists(),
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        })

        if OUT.exists():
            log.warning("Mantendo M3U anterior.")
            return

        raise

    categories = discover_categories(home_html, home_url)
    metrics["categories"] = len(categories)

    log.info("Categorias descobertas: %d", len(categories))

    channels = OrderedDict()

    for category_url, category_name in categories.items():
        try:
            html, final = get(category_url)
            metrics["category_pages_ok"] += 1
        except Exception as exc:
            metrics["category_pages_failed"] += 1
            log.warning(
                "Categoria indisponível %s: %s",
                category_url,
                exc,
            )
            continue

        found = discover_channels(final, html)
        metrics["channel_pages_discovered"] += len(found)

        log.info(
            "%s -> %d páginas de canais",
            category_name,
            len(found),
        )

        for channel_url, channel_name in found.items():
            rec = channels.setdefault(
                channel_url,
                {
                    "url": channel_url,
                    "name": channel_name,
                    "categories": [],
                },
            )

            # Caso a página tenha um texto vazio ou pouco útil em uma
            # categoria, mantém o primeiro nome encontrado.
            if not rec["name"] and channel_name:
                rec["name"] = channel_name

            if category_name not in rec["categories"]:
                rec["categories"].append(category_name)

    metrics["channel_pages_unique"] = len(channels)

    log.info(
        "Páginas de canais únicas encontradas: %d",
        len(channels),
    )

    if not channels:
        # Salva diagnóstico detalhado.
        save_status({
            "success": False,
            "source": BASE,
            "error_stage": "channel_discovery",
            "error": "Nenhuma página de canal foi identificada.",
            "metrics": metrics,
            "categories_found": [
                {"name": name, "url": url}
                for url, name in categories.items()
            ],
            "preserved_previous_m3u": OUT.exists(),
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        })

        if OUT.exists():
            log.warning("Mantendo M3U anterior.")
            return

        raise RuntimeError(
            "Nenhuma página de canal encontrada."
        )

    active = []

    for index, rec in enumerate(channels.values(), 1):
        try:
            html, final = get(rec["url"])
            soup = BeautifulSoup(html, "html.parser")

            rec["page"] = final
            rec["logo"] = extract_logo(soup, final)

            stream = get_channel_stream(final, html)

            if stream:
                metrics["streams_found"] += 1
                rec["stream"] = stream

                if stream_alive(stream):
                    rec["active"] = True
                    active.append(rec)
                    metrics["streams_active"] += 1
                    state = "ATIVO"
                else:
                    rec["active"] = False
                    state = "STREAM INATIVO"
            else:
                rec["active"] = False
                rec["stream"] = ""
                state = "SEM STREAM"

            log.info(
                "[%d/%d] %s -> %s",
                index,
                len(channels),
                rec["name"],
                state,
            )

        except Exception as exc:
            rec["active"] = False
            rec["error"] = str(exc)

            log.warning(
                "[%d/%d] %s -> ERRO: %s",
                index,
                len(channels),
                rec["name"],
                exc,
            )

    # Não substituir uma lista existente por uma lista vazia causada por
    # bloqueio/alteração temporária da fonte.
    if not active:
        save_status({
            "success": False,
            "source": BASE,
            "error_stage": "stream_validation",
            "error": "Nenhum stream ativo foi validado.",
            "metrics": metrics,
            "preserved_previous_m3u": OUT.exists(),
            "generated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        })

        if OUT.exists():
            log.warning("Mantendo M3U anterior.")
            return

        raise RuntimeError("Nenhum stream ativo foi validado.")

    lines = [
        "#EXTM3U",
        "# Fonte: https://televisao.tv/",
        f"# Canais ativos: {len(active)}",
    ]

    # Um canal pode pertencer a várias categorias.
    # Para SS IPTV, cada associação vira uma entrada na M3U.
    for rec in sorted(
        active,
        key=lambda x: (
            (x["categories"] or ["Outros"])[0].lower(),
            x["name"].lower(),
        ),
    ):
        cats = rec["categories"] or ["Outros"]

        for category in cats:
            lines.append(
                extinf(
                    rec["name"],
                    rec.get("logo", ""),
                    category,
                )
            )
            lines.append(rec["stream"])

    OUT.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    channel_report = []

    for rec in sorted(
        channels.values(),
        key=lambda x: x["name"].lower(),
    ):
        channel_report.append({
            "name": rec["name"],
            "page": rec.get("page", rec["url"]),
            "categories": rec["categories"],
            "logo": rec.get("logo", ""),
            "stream": rec.get("stream", ""),
            "active": rec.get("active", False),
            "error": rec.get("error", ""),
        })

    status = {
        "success": True,
        "source": BASE,
        "generated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        ),
        "elapsed_seconds": round(time.time() - started, 2),
        "metrics": metrics,
        "active_channels": len(active),
        "inactive_channels": len(channels) - len(active),
        "categories_found": [
            {"name": name, "url": url}
            for url, name in categories.items()
        ],
        "channels": channel_report,
    }

    save_status(status)

    log.info(
        "CONCLUÍDO: %d canais ativos | %d streams encontrados | "
        "%d páginas de canais",
        len(active),
        metrics["streams_found"],
        len(channels),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.exception("Falha: %s", exc)
        sys.exit(1)
