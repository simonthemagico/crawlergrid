import re
import textwrap
from datetime import datetime, timezone
from html import unescape

from curl_cffi import requests

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_WHITE = "\033[97m"
_BG_RED = "\033[41m"
_RESET = "\033[0m"

# ---------------------------------------------------------------------------
# Financial keywords — posts matching these are flagged as market-moving
# ---------------------------------------------------------------------------
FINANCIAL_KEYWORDS = [
    r"tariff", r"trade", r"china", r"eu\b", r"european union",
    r"stock", r"market", r"dow", r"nasdaq", r"s&p", r"economy",
    r"inflation", r"interest rate", r"fed\b", r"federal reserve",
    r"oil", r"gas", r"energy", r"opec", r"bitcoin", r"crypto",
    r"tax", r"deficit", r"debt", r"gdp", r"recession",
    r"sanction", r"embargo", r"import", r"export", r"currency",
    r"dollar", r"treasury", r"bond", r"bank", r"shutdown",
    r"regulation", r"deregulat", r"antitrust", r"merger",
    r"apple", r"google", r"meta", r"amazon", r"tesla", r"nvidia",
    r"microsoft", r"tiktok", r"boeing", r"lockheed",
]
_FIN_PATTERN = re.compile("|".join(FINANCIAL_KEYWORDS), re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
IMPERSONATE = "chrome"
ACCOUNT_ID = "107780257626128497"
API_URL = f"https://truthsocial.com/api/v1/accounts/{ACCOUNT_ID}/statuses"

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    'cache-control': 'no-cache',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://truthsocial.com/@realDonaldTrump/',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
}

params = {
    'exclude_replies': 'true',
    'only_replies': 'false',
    'with_muted': 'true',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strip_html(raw: str) -> str:
    text = raw.replace("<br/>", "\n").replace("<br>", "\n").replace("</p><p>", "\n\n")
    text = _HTML_TAG.sub("", text)
    text = unescape(text)
    return text.strip()


def format_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = now - dt
    mins = int(delta.total_seconds() / 60)
    if mins < 60:
        ago = f"{mins}m ago"
    elif mins < 1440:
        ago = f"{mins // 60}h{mins % 60:02d}m ago"
    else:
        ago = f"{mins // 1440}d ago"
    return f"{dt.strftime('%Y-%m-%d %H:%M UTC')} ({ago})"


def detect_financial(text: str) -> list[str]:
    return list({m.group().upper() for m in _FIN_PATTERN.finditer(text)})


def format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_header(count: int):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{_BOLD}{_CYAN}{'=' * 74}")
    print(f"  TRUTH SOCIAL FEED  |  @realDonaldTrump  |  {now}")
    print(f"  {count} posts fetched  |  curl_cffi + Chrome TLS impersonation")
    print(f"{'=' * 74}{_RESET}\n")


def print_post(post: dict, index: int):
    is_reblog = post.get("reblog") is not None
    source = post["reblog"] if is_reblog else post

    content_html = source.get("content", "")
    text = strip_html(content_html)
    created = post.get("created_at", "")
    url = post.get("url", "")

    replies = source.get("replies_count", 0)
    reblogs = source.get("reblogs_count", 0)
    favorites = source.get("favourites_count", 0)

    keywords = detect_financial(text)
    is_financial = len(keywords) > 0

    # --- separator ---
    if is_financial:
        print(f"{_BOLD}{_RED}{'!' * 74}{_RESET}")
    else:
        print(f"{_DIM}{'─' * 74}{_RESET}")

    # --- header line ---
    tag = f" {_YELLOW}[RT]{_RESET}" if is_reblog else ""
    fin_tag = f" {_BG_RED}{_WHITE}{_BOLD} FINANCIAL {_RESET}" if is_financial else ""
    print(f"{_BOLD}{_CYAN}  #{index}{_RESET}{tag}{fin_tag}")

    # --- time ---
    print(f"  {_DIM}Time:{_RESET}  {format_time(created)}")

    # --- financial keywords ---
    if keywords:
        kw_str = ", ".join(f"{_BOLD}{_YELLOW}{k}{_RESET}" for k in sorted(keywords))
        print(f"  {_DIM}Tags:{_RESET}  {kw_str}")

    # --- content ---
    print(f"  {_DIM}{'- ' * 35}{_RESET}")
    wrapped = textwrap.fill(text, width=70, initial_indent="  ", subsequent_indent="  ")
    if is_financial:
        print(f"{_WHITE}{_BOLD}{wrapped}{_RESET}")
    else:
        print(wrapped)

    # --- engagement ---
    print(f"\n  {_GREEN}Likes {format_number(favorites)}{_RESET}"
          f"  {_CYAN}RTs {format_number(reblogs)}{_RESET}"
          f"  {_MAGENTA}Replies {format_number(replies)}{_RESET}"
          f"  {_DIM}|{_RESET}  {_DIM}{url}{_RESET}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

session = requests.Session(impersonate=IMPERSONATE)

response = session.get(
    API_URL,
    params=params,
    headers=headers,
    timeout=30,
)

if response.status_code != 200:
    print(f"{_RED}ERR{_RESET} HTTP {response.status_code}")
    raise SystemExit(1)

posts = response.json()
print_header(len(posts))

financial_count = 0
for i, post in enumerate(posts, 1):
    source = post["reblog"] if post.get("reblog") else post
    text = strip_html(source.get("content", ""))
    if detect_financial(text):
        financial_count += 1
    print_post(post, i)

# --- summary ---
print(f"{_BOLD}{_CYAN}{'=' * 74}")
print(f"  SUMMARY: {len(posts)} posts  |  "
      f"{_YELLOW}{financial_count} market-relevant{_CYAN}  |  "
      f"{len(posts) - financial_count} other")
print(f"{'=' * 74}{_RESET}\n")
