# Indeed Scraper - Standalone Edition (2026)

Scrape les offres d'emploi Indeed.fr sans navigateur headless, en une seule dépendance HTTP : **curl_cffi**.

Le script contourne le TLS fingerprinting de Cloudflare en reproduisant le handshake TLS de Chrome, puis extrait les données structurées (format Mosaic 2026 + fallback legacy) et enrichit chaque offre via les pages de détail embedded.

## Fonctionnalités

- **Impersonation TLS Chrome** via [curl_cffi](https://github.com/lexiforest/curl_cffi) -- fingerprint JA3/JA4 identique à un vrai navigateur
- **Headers Sec-Fetch-\*** cohérents avec une navigation réelle
- **Parsing multi-format** : Mosaic providers (2026) + fallback `comp-initialData` (legacy)
- **Enrichissement automatique** via `viewtype=embedded` (JSON pur) + fallback JSON-LD
- **Logs structurés et colorés** avec horodatage, progression `[1/5]`, et JSON pretty-print
- **Export JSON** optionnel (`--json-output`)
- **Support proxy** via variable d'environnement `PROXY_URL`

## Installation

```bash
pip install -r requirements.txt
```

Dépendances : `curl_cffi` (impersonation TLS) et `lxml` (parsing HTML).

## Utilisation

```bash
# Recherche par défaut (alternance en France, triée par date)
python scrape_indeed.py

# Avec proxy (recommandé pour éviter le blocage)
PROXY_URL="http://user:pass@host:port" python scrape_indeed.py

# Recherche custom
python scrape_indeed.py --url "https://fr.indeed.com/jobs?q=python&l=Paris"

# Limiter à 5 offres détaillées
python scrape_indeed.py --max 5

# Listing seulement (pas de chargement des pages de détail)
python scrape_indeed.py --no-detail

# Exporter en JSON
python scrape_indeed.py --json-output
```

### Options

| Option | Description |
|---|---|
| `--url` | URL de recherche Indeed (défaut : alternance en France) |
| `--max N` | Nombre max d'offres à détailler (défaut : 10) |
| `--no-detail` | Ne charge que le listing, pas les pages de détail |
| `--json-output` | Exporte les résultats dans `jobs_output.json` |

### Variable d'environnement

| Variable | Description |
|---|---|
| `PROXY_URL` | URL du proxy HTTP/HTTPS (ex : `http://user:pass@host:port`) |

## Architecture

```
Session curl_cffi (Chrome TLS)
    │
    ▼
GET listing (SERP) ──► parse Mosaic/Legacy ──► JSON offres
    │
    ▼
Pour chaque offre :
    GET viewjob?viewtype=embedded ──► parse JSON/JSON-LD ──► enrichir
    │
    ▼
Affichage structuré + export JSON optionnel
```

## Limitations

- **Pagination bloquée** : depuis février 2026, Indeed exige un compte connecté pour accéder aux pages au-delà de la première (les tokens `pp` sont présents mais non fonctionnels sans authentification). Le script extrait donc la première page (~15 offres).
- **Blocage Cloudflare** : sans proxy résidentiel, le blocage (403 + captcha) peut survenir après quelques requêtes.

## Licence

MIT
