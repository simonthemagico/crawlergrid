#!/usr/bin/env python3
"""
Indeed Scraper - Standalone Edition (2026)
==========================================
Scrape Indeed job listings using curl_cffi for TLS impersonation.

Dependencies:
    pip install curl_cffi lxml

Usage:
    # Without proxy (will likely get blocked)
    python scrape_indeed.py

    # With proxy (recommended)
    PROXY_URL="http://user:pass@host:port" python scrape_indeed.py

    # Custom search
    python scrape_indeed.py --url "https://fr.indeed.com/jobs?q=python&l=Paris"

    # Limit number of jobs to fetch details for
    python scrape_indeed.py --max 5
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
import os

from curl_cffi import requests
from lxml import html


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_step(msg: str):
    """Major step in the pipeline."""
    print(f"\n{_BOLD}{_CYAN}[{_ts()}]{_RESET} {_BOLD}{msg}{_RESET}")


def log_info(msg: str):
    print(f"  {_DIM}>{_RESET} {msg}")


def log_ok(msg: str):
    print(f"  {_GREEN}OK{_RESET} {msg}")


def log_warn(msg: str):
    print(f"  {_YELLOW}!!{_RESET} {msg}")


def log_err(msg: str):
    print(f"  {_RED}ERR{_RESET} {msg}")


def log_json(label: str, obj, indent: int = 2, max_str: int = 200):
    """Pretty-print a dict/list as JSON under a label."""
    def _truncate(o):
        if isinstance(o, str) and len(o) > max_str:
            return o[:max_str] + f"... ({len(o)} chars)"
        if isinstance(o, dict):
            return {k: _truncate(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_truncate(v) for v in o]
        return o
    print(f"  {_DIM}--- {label} ---{_RESET}")
    print(json.dumps(_truncate(obj), indent=indent, ensure_ascii=False, default=str))


def log_separator():
    print(f"\n{_DIM}{'─' * 70}{_RESET}")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_URL = (
    "https://fr.indeed.com/jobs?q=alternance&l=France&sort=date"
    "&fromage=1&from=searchOnDesktopSerp"
)

# curl_cffi reproduit le handshake TLS de Chrome (JA3 fingerprint identique).
# Sans ca, Cloudflare detecte instantanement que la requete ne vient pas
# d'un vrai navigateur.
IMPERSONATE = "chrome"

# Headers coherents avec un vrai navigateur Firefox sur macOS.
# Indeed verifie notamment les Sec-Fetch-* headers.
LISTING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:148.0) "
        "Gecko/20100101 Firefox/148.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr,fr-FR;q=0.9,en-US;q=0.8,en;q=0.7",
    "Alt-Used": "fr.indeed.com",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Priority": "u=0, i",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

DETAIL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:148.0) "
        "Gecko/20100101 Firefox/148.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr,fr-FR;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ---------------------------------------------------------------------------
# JSON extraction utilities
# ---------------------------------------------------------------------------

def extract_json_object(text: str, start_idx: int) -> str | None:
    """
    Extrait un objet JSON d'une string par comptage d'accolades.
    Gere correctement les strings JSON (guillemets, echappement).
    """
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]
    return None


def extract_mosaic_providers(script_text: str) -> dict[str, dict]:
    """
    Indeed utilise une architecture "Mosaic" (2026+).
    Les donnees sont exposees via des assignations JS :
        window.mosaic.providerData["mosaic-provider-jobcards"] = {...};

    On extrait chaque provider par regex + comptage d'accolades.
    """
    providers: dict[str, dict] = {}
    if not script_text:
        return providers

    pattern = re.compile(
        r"window\.mosaic\.providerData\[(?:\"|')(?P<key>.+?)(?:\"|')\]"
        r"\s*=\s*\{",
        re.S,
    )
    for m in pattern.finditer(script_text):
        key = (m.group("key") or "").strip()
        start = m.end() - 1
        blob = extract_json_object(script_text, start)
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except Exception:
            continue
        if isinstance(data, dict) and key:
            providers[key] = data
    return providers


def html_to_text(raw: str | None) -> str | None:
    """Convertit du HTML en texte brut."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        node = html.fromstring(raw)
        txt = " ".join(t.strip() for t in node.itertext() if t and t.strip())
        return " ".join(txt.split()).strip() or None
    except Exception:
        return " ".join(raw.split()).strip() or None


def epoch_ms_to_iso(value) -> str | None:
    """Convertit un timestamp epoch (ms) en ISO-8601 UTC."""
    if value is None:
        return None
    try:
        ms = int(value)
    except Exception:
        return None
    seconds = ms / 1000.0 if ms > 10**11 else float(ms)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Parsing de la page de listing (SERP)
# ---------------------------------------------------------------------------

def parse_listing_page(text: str) -> tuple[list[dict], int]:
    """
    Parse la page de resultats Indeed.
    Retourne (jobs, total_count).

    Supporte deux formats :
    - Mosaic (2026+) : window.mosaic.providerData[...]
    - Legacy : <script id="comp-initialData">
    """
    doc = html.fromstring(text)

    # 1. Essayer le format legacy
    scripts = doc.xpath(
        '//script[@id="comp-initialData" and @type="application/json"]/text()'
    )
    if scripts:
        log_ok("Format detecte: Legacy (comp-initialData)")
        data = json.loads(scripts[0])
        jobs = data.get("jobList", {}).get("jobs", [])
        total = data.get("jobList", {}).get("filteredJobCount", len(jobs))
        return jobs, int(total)

    # 2. Format Mosaic (2026+)
    mosaic_scripts = doc.xpath('//script[@id="mosaic-data"]/text()')
    if not mosaic_scripts:
        mosaic_scripts = doc.xpath('//script[@id="mosaic-init-data"]/text()')

    # Fallback regex si lxml echoue sur des lignes tres longues
    if not mosaic_scripts:
        match = re.search(
            r'<script[^>]*id="mosaic-data"[^>]*>(.*?)</script>', text, re.DOTALL
        )
        if match:
            mosaic_scripts = [match.group(1).strip()]

    if not mosaic_scripts:
        log_err("Impossible de trouver les donnees Indeed dans la page.")
        log_err("Le site a peut-etre change de structure, ou vous etes bloque.")
        return [], 0

    log_ok("Format detecte: Mosaic (2026+)")
    providers = extract_mosaic_providers(mosaic_scripts[0])
    log_info(f"Providers trouves: {', '.join(providers.keys()) or 'aucun'}")

    # Extraire les offres depuis mosaic-provider-jobcards
    jobcards = providers.get("mosaic-provider-jobcards", {})
    results = (
        (jobcards.get("metaData") or {})
        .get("mosaicProviderJobCardsModel", {})
        .get("results", [])
    )
    if not isinstance(results, list):
        results = []

    # Extraire le nombre total depuis MosaicProviderRichSearchDaemon
    rich = providers.get("MosaicProviderRichSearchDaemon", {})
    total = None
    try:
        total = (rich.get("richSearchComponentModel") or {}).get("totalJobCount")
    except Exception:
        pass
    if total is None:
        total = len(results)

    return results, int(total)


def extract_job_from_listing(job: dict) -> dict:
    """Extrait les champs utiles d'une offre dans le listing."""
    job_key = (
        job.get("jobKey")
        or job.get("jobkey")
        or (job.get("mouseDownHandlerOption") or {}).get("jobKey")
    )

    published = (
        epoch_ms_to_iso(job.get("pubDate"))
        or epoch_ms_to_iso(job.get("createDate"))
        or job.get("formattedRelativeTime")
    )

    salary_snippet = job.get("salarySnippet") or {}

    return {
        "job_key": job_key,
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("formattedLocation") or job.get("jobLocationCity"),
        "salary": salary_snippet.get("text") if isinstance(salary_snippet, dict) else None,
        "published_at": published,
        "url": f"https://fr.indeed.com/viewjob?jk={job_key}" if job_key else None,
    }


# ---------------------------------------------------------------------------
# Parsing de la page de detail (offre)
# ---------------------------------------------------------------------------

def dict_get(d: dict, path: str, default=None):
    """Acces par chemin slash-separe : dict_get(d, 'a/b/c') == d['a']['b']['c']"""
    keys = path.split("/")
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def parse_detail_page(text: str) -> dict:
    """
    Parse la page de detail d'une offre Indeed.
    Le format embedded (viewtype=embedded) retourne du JSON directement.
    Fallback sur HTML + JSON-LD si necessaire.
    """
    # 1. Essayer de parser comme JSON pur (cas le plus courant avec embedded)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return extract_fields_from_json(data)
    except Exception:
        pass

    # 2. Fallback HTML : chercher JSON-LD JobPosting
    doc = html.fromstring(text)
    for raw in doc.xpath('//script[@type="application/ld+json"]/text()'):
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            ld = json.loads(raw)
        except Exception:
            continue

        candidates = []
        if isinstance(ld, list):
            candidates = ld
        elif isinstance(ld, dict) and isinstance(ld.get("@graph"), list):
            candidates = ld["@graph"]
        else:
            candidates = [ld]

        for c in candidates:
            if not isinstance(c, dict):
                continue
            t = c.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return extract_fields_from_jsonld(c)

    return {}


def extract_fields_from_json(data: dict) -> dict:
    """Extrait les champs depuis le JSON Indeed (format embedded)."""

    # Company : plusieurs chemins possibles
    company = (
        dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/jobInfoHeaderModel/companyName")
        or dict_get(data, "body/hostQueryExecutionResult/data/jobData/results/0/job/sourceEmployerName")
    )

    # Location
    location = dict_get(data, "body/jobLocation")

    # Salary : essayer plusieurs sources
    salary = None
    contents = dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/jobDescriptionSectionModel/jobDetailsSection/contents") or {}
    if isinstance(contents, dict):
        for key in ("Salaire", "Remuneration", "Pay", "Salary"):
            vals = contents.get(key)
            if isinstance(vals, list) and vals:
                salary = ", ".join([v for v in vals if v])
                break

    if not salary:
        salary_info = dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/jobDescriptionSectionModel/jobDetailsSection/salaryInfoModel")
        if isinstance(salary_info, dict):
            salary = salary_info.get("salaryText") or salary_info.get("formattedSalary")

    # Contract type
    contract_type = None
    ct_vals = dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/jobDescriptionSectionModel/jobDetailsSection/contents/Type de contrat")
    if isinstance(ct_vals, list) and ct_vals:
        contract_type = ", ".join(ct_vals)
    else:
        job_types = dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/jobDescriptionSectionModel/jobDetailsSection/jobTypes") or []
        if isinstance(job_types, list):
            labels = [jt.get("label") for jt in job_types if isinstance(jt, dict) and jt.get("label")]
            if labels:
                contract_type = ", ".join(labels)

    # Description
    description = None
    raw_desc = dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/sanitizedJobDescription/content")
    if raw_desc:
        description = html_to_text(raw_desc)
    if not description:
        raw_desc2 = dict_get(data, "body/jobInfoWrapperModel/jobInfoModel/sanitizedJobDescription")
        if isinstance(raw_desc2, str):
            description = html_to_text(raw_desc2)

    # Published at
    published_at = None
    host_job = dict_get(data, "body/hostQueryExecutionResult/data/jobData/results/0/job") or {}
    if isinstance(host_job, dict):
        published_at = (
            epoch_ms_to_iso(host_job.get("datePublished"))
            or epoch_ms_to_iso(host_job.get("dateOnIndeed"))
        )

    return {
        "company": company,
        "location": location,
        "salary": salary,
        "contract_type": contract_type,
        "description": description,
        "published_at": published_at,
    }


def extract_fields_from_jsonld(jp: dict) -> dict:
    """Extrait les champs depuis un JSON-LD JobPosting."""
    org = jp.get("hiringOrganization") or {}

    # Location
    location = None
    jl = jp.get("jobLocation")
    if isinstance(jl, list) and jl:
        jl = jl[0]
    if isinstance(jl, dict):
        addr = jl.get("address") or {}
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            location = " ".join([p for p in parts if p]) or None

    # Salary
    salary = None
    base = jp.get("baseSalary") or jp.get("estimatedSalary")
    if isinstance(base, dict):
        val = base.get("value", base)
        currency = base.get("currency", "")
        if isinstance(val, dict):
            minv = val.get("minValue")
            maxv = val.get("maxValue")
            v = val.get("value")
            unit = val.get("unitText", "")
            if v is not None:
                salary = f"{v} {currency} / {unit}".strip()
            elif minv is not None and maxv is not None:
                salary = f"{minv} - {maxv} {currency} / {unit}".strip()

    # Contract type
    et = jp.get("employmentType")
    if isinstance(et, list):
        contract_type = ", ".join(et)
    elif isinstance(et, str):
        contract_type = et
    else:
        contract_type = None

    return {
        "company": org.get("name") if isinstance(org, dict) else None,
        "location": location,
        "salary": salary,
        "contract_type": contract_type,
        "description": html_to_text(jp.get("description")),
        "published_at": jp.get("datePosted"),
    }


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------

def create_session(proxy_url: str | None = None) -> requests.Session:
    """
    Cree une session curl_cffi avec impersonation Chrome.
    C'est la piece maitresse : le handshake TLS sera identique
    a celui d'un vrai Chrome.
    """
    log_step("Creation de la session curl_cffi")
    log_info(f"TLS impersonation: {IMPERSONATE}")

    session = requests.Session(impersonate=IMPERSONATE)

    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
        parsed = urlparse(proxy_url)
        safe = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        log_ok(f"Proxy: {safe}")
    else:
        log_warn("Aucun proxy configure -- risque eleve de blocage")

    return session


def scrape_listing(session: requests.Session, url: str) -> tuple[list[dict], int]:
    """
    Charge la page de resultats et parse les offres.
    Retourne (jobs, total_count).
    """
    log_step("Chargement de la page de resultats (SERP)")
    log_info(f"URL: {url}")

    headers = dict(LISTING_HEADERS)
    parsed = urlparse(url)
    headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/jobs"

    response = session.get(url, headers=headers, timeout=30)
    log_info(f"HTTP {response.status_code} -- {len(response.text):,} bytes")

    if response.status_code != 200:
        log_err(f"Status inattendu: {response.status_code}")
        if "secure.indeed.com/auth" in str(response.url):
            log_err("Bot detecte: redirection vers secure.indeed.com/auth")
            log_err("Essayez avec un proxy different (PROXY_URL=...)")
        return [], 0

    log_ok("Page recue, parsing en cours...")
    jobs_raw, total = parse_listing_page(response.text)
    jobs = [extract_job_from_listing(j) for j in jobs_raw]

    log_ok(f"{len(jobs)} offres extraites sur cette page (total Indeed: {total})")

    # Afficher le JSON extrait de chaque offre du listing
    if jobs:
        log_step(f"JSON extrait du listing ({len(jobs)} offres)")
        for i, job in enumerate(jobs, 1):
            log_separator()
            log_json(f"Offre #{i} -- {job.get('title', '?')}", job)

    return jobs, total


def scrape_job_detail(session: requests.Session, job: dict, index: int = 0, total: int = 0) -> dict:
    """
    Charge la page de detail d'une offre via l'URL embedded.
    Indeed retourne du JSON pur avec viewtype=embedded.
    """
    jk = job.get("job_key")
    if not jk:
        log_warn(f"Pas de job_key pour: {job.get('title', '?')}")
        return job

    progress = f"[{index}/{total}]" if total else ""
    log_info(f"{progress} Detail de: {job.get('title', '?')} (jk={jk})")

    detail_url = (
        f"https://fr.indeed.com/viewjob?viewtype=embedded"
        f"&jk={jk}&from=shareddesktop_copy&adid=0&spa=1&hidecmpheader=1"
    )

    headers = dict(DETAIL_HEADERS)
    headers["Referer"] = job.get("url", "https://fr.indeed.com/jobs")

    response = session.get(detail_url, headers=headers, timeout=30)

    if response.status_code != 200:
        log_warn(f"HTTP {response.status_code} pour jk={jk}")
        return job

    detail = parse_detail_page(response.text)

    # Fusionner : le detail enrichit les donnees du listing
    new_fields = []
    for key, value in detail.items():
        if value is not None:
            job[key] = value
            new_fields.append(key)

    log_ok(f"Champs enrichis: {', '.join(new_fields) if new_fields else 'aucun nouveau'}")

    return job


def print_job_summary(job: dict, index: int):
    """Affiche un resume compact d'une offre."""
    title = job.get("title", "?")
    company = job.get("company", "?")
    location = job.get("location", "?")
    salary = job.get("salary")
    salary_str = f" | {salary}" if salary else ""
    print(f"  {_BOLD}#{index}{_RESET} {title} @ {company} -- {location}{salary_str}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Indeed avec curl_cffi (TLS impersonation)"
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help="URL de recherche Indeed"
    )
    parser.add_argument(
        "--max", type=int, default=10,
        help="Nombre max d'offres a detailler (defaut: 10)"
    )
    parser.add_argument(
        "--no-detail", action="store_true",
        help="Ne pas charger les pages de detail (listing seulement)"
    )
    parser.add_argument(
        "--json-output", action="store_true",
        help="Ecrire le JSON final dans un fichier jobs_output.json"
    )
    args = parser.parse_args()

    proxy_url = os.environ.get("PROXY_URL")

    print(f"\n{_BOLD}{'=' * 70}")
    print(f"  Indeed Scraper -- curl_cffi + Chrome TLS Impersonation")
    print(f"{'=' * 70}{_RESET}")

    # --- Phase 1 : Session ---
    session = create_session(proxy_url)

    # --- Phase 2 : Listing ---
    jobs, total = scrape_listing(session, args.url)

    if not jobs:
        log_err("Aucune offre trouvee. Verifiez l'URL ou le proxy.")
        sys.exit(1)

    # --- Phase 3 : Details ---
    if not args.no_detail:
        limit = min(len(jobs), args.max)
        log_step(f"Chargement des details ({limit} offres)")

        for i in range(limit):
            jobs[i] = scrape_job_detail(session, jobs[i], index=i + 1, total=limit)
            if i < limit - 1:
                delay = random.uniform(2, 4)
                log_info(f"Pause {delay:.1f}s...")
                time.sleep(delay)

    # --- Phase 4 : Resultats finaux ---
    display_jobs = jobs[: args.max]

    log_step(f"Resultats finaux ({len(display_jobs)} offres)")
    print()
    for i, job in enumerate(display_jobs, 1):
        print_job_summary(job, i)

    # JSON complet de chaque offre enrichie
    log_step("JSON complet des offres enrichies")
    for i, job in enumerate(display_jobs, 1):
        log_separator()
        log_json(f"Offre #{i} -- {job.get('title', '?')}", job)

    # Export fichier optionnel
    if args.json_output:
        out_path = os.path.join(os.path.dirname(__file__) or ".", "jobs_output.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(display_jobs, f, indent=2, ensure_ascii=False, default=str)
        log_ok(f"JSON ecrit dans {out_path}")

    print(f"\n{_BOLD}{'=' * 70}")
    print(f"  {len(display_jobs)} offres affichees sur {total} au total")
    print(f"{'=' * 70}{_RESET}\n")


if __name__ == "__main__":
    main()
