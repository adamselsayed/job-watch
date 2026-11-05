#!/usr/bin/env python3
"""
job-watch fetcher.

Reads companies.yml, pulls each company's public job board, keeps only the
roles that match the watch criteria, merges with the previous snapshot in
data/jobs.json (so we know what's new and what closed), and writes the result.

Standard library only -- runs anywhere Python 3.9+ is installed.

Usage:
    python3 scripts/fetch_jobs.py            # fetch all companies
    python3 scripts/fetch_jobs.py Samsara    # fetch one company (others kept as-is)
"""

import html
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANIES_FILE = os.path.join(ROOT, "companies.yml")
DATA_FILE = os.path.join(ROOT, "data", "jobs.json")
CHANGES_FILE = os.path.join(ROOT, "data", "changes.log")

# ---------------------------------------------------------------- criteria --

TITLE_KEYWORDS = ["product"]          # title must contain one of these (case-insensitive)
TITLE_EXCLUDE_WORDS = ["design", "designer", "marketing", "engineer", "engineers", "engineering"]      # ...and must not contain any of these as a standalone word
KEEP_CLOSED_DAYS = 30                 # how long closed roles stay visible on the dashboard

# ------------------------------------------------------------- helpers -----

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg):
    print(msg, file=sys.stderr)


def read_companies(path=COMPANIES_FILE):
    """Tiny parser for the flat list-of-blocks format used in companies.yml.
    Avoids a PyYAML dependency so the script runs on a bare Python install."""
    companies, cur = [], None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            m = re.match(r"^-\s+(\w+):\s*(.*)$", line)
            if m:
                cur = {m.group(1): m.group(2).strip().strip('"\'')}
                companies.append(cur)
                continue
            m = re.match(r"^\s+(\w+):\s*(.*)$", line)
            if m and cur is not None:
                cur[m.group(1)] = m.group(2).strip().strip('"\'')
    for c in companies:
        for k in ("name", "ats", "board"):
            if k not in c:
                raise SystemExit(f"companies.yml: entry {c} is missing '{k}'")
        c["ats"] = c["ats"].lower()
    return companies


_AIA_CTX = {}


def _aia_context(host):
    """Some career sites (e.g. apply.ford.com) send their certificate without the intermediate.
    Browsers fill the gap from the certificate's "CA Issuers" link; Python doesn't. Do the same
    here: download that intermediate and add it to an otherwise normal, fully-verifying context."""
    if host in _AIA_CTX:
        return _AIA_CTX[host]
    import ssl, tempfile
    leaf = ssl.get_server_certificate((host, 443))
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(leaf)
    info = ssl._ssl._test_decode_cert(f.name)          # stdlib helper; exposes the CA Issuers URL
    os.unlink(f.name)
    ctx = ssl.create_default_context()
    for issuer_url in info.get("caIssuers") or ():
        with urllib.request.urlopen(issuer_url, timeout=20) as r:
            der = r.read()
        pem = der.decode() if der.startswith(b"-----") else ssl.DER_cert_to_PEM_cert(der)
        ctx.load_verify_locations(cadata=pem)
    _AIA_CTX[host] = ctx
    return ctx


_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def fetch_text(url, timeout=30, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "job-watch/1.0 (+github)", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), r
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e) or "local issuer" not in str(e):
            raise
        host = urllib.parse.urlsplit(url).hostname
        with urllib.request.urlopen(req, timeout=timeout, context=_aia_context(host)) as r:
            return r.read().decode("utf-8", "replace"), r


def fetch_json(url, timeout=30, headers=None):
    body, r = fetch_text(url, timeout, headers)
    try:
        return json.loads(body)
    except ValueError:
        raise ValueError(f"not JSON (HTTP {r.status}, {r.headers.get('Content-Type')}): {body[:300]!r}") from None


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", s)


# ------------------------------------------------------ classification -----

US_STATES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut","delaware",
    "florida","georgia","hawaii","idaho","illinois","indiana","iowa","kansas","kentucky",
    "louisiana","maine","maryland","massachusetts","michigan","minnesota","mississippi",
    "missouri","montana","nebraska","nevada","new hampshire","new jersey","new mexico",
    "new york","north carolina","north dakota","ohio","oklahoma","oregon","pennsylvania",
    "rhode island","south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming","district of columbia",
}
US_STATE_ABBR = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
    "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
}
US_CITIES = {
    "san francisco","new york","nyc","los angeles","san diego","seattle","austin","denver",
    "chicago","boston","atlanta","phoenix","dallas","houston","miami","portland","nashville",
    "charlotte","washington, d.c.","washington dc","salt lake city","minneapolis","detroit",
    "philadelphia","raleigh","durham","pittsburgh","las vegas","san jose","palo alto",
    "mountain view","menlo park","sunnyvale","santa clara","oakland","irvine","orange county",
    "sacramento","boulder","kansas city","st. louis","columbus","indianapolis","tampa",
    "orlando","baltimore","richmond","buffalo","rochester","madison","milwaukee","omaha",
    "tempe","scottsdale","bay area","sf bay area","sf","silicon valley",
}
NON_US_MARKERS = {
    "canada","toronto","vancouver","montreal","calgary","ottawa","mexico","mexico city",
    "guadalajara","monterrey","united kingdom","uk","england","london","ireland","dublin",
    "germany","berlin","munich","france","paris","netherlands","amsterdam","spain","madrid",
    "barcelona","italy","milan","poland","warsaw","krakow","portugal","lisbon","sweden",
    "stockholm","denmark","copenhagen","norway","finland","switzerland","zurich","austria",
    "belgium","brussels","czech","prague","romania","bucharest","hungary","budapest",
    "israel","tel aviv","uae","dubai","abu dhabi","saudi","riyadh","qatar","egypt","cairo",
    "india","bangalore","bengaluru","hyderabad","pune","mumbai","delhi","gurgaon","chennai",
    "pakistan","islamabad","lahore","karachi","singapore","japan","tokyo","china","shanghai",
    "beijing","hong kong","taiwan","taipei","korea","seoul","australia","sydney","melbourne",
    "new zealand","brazil","sao paulo","são paulo","argentina","buenos aires","colombia",
    "bogota","chile","santiago","peru","lima","south africa","nigeria","kenya","philippines",
    "manila","vietnam","indonesia","malaysia","thailand","bangkok","emea","apac","latam",
    "dach","benelux","nordics","europe","bulgaria","sofia","costa rica","turkey","istanbul","ukraine","kyiv",
    "greece","athens","morocco","bangladesh","dhaka","sri lanka","colombo","south korea","czech republic",
    "united arab emirates","saudi arabia","hong kong","north america", "canada","ontario","quebec","british columbia","alberta","manitoba","saskatchewan","nova scotia","new brunswick",
}


def _tokens(s):
    return [t.strip() for t in re.split(r"[,;\-/|()]+|\s{2,}", s) if t.strip()]


def classify_us(location_text):
    """Return True (US), False (not US) or None (can't tell)."""
    low = location_text.lower()
    if re.search(r"\b(united states|usa|u\.s\.a?\.?|us only|us-based)\b", low):
        return True
    if re.search(r"(^|[\s\-,(])us([\s\-,)]|$)", low):            # "Remote - US", "US Remote"
        return True
    if any(re.search(r"\b" + re.escape(m) + r"\b", low) for m in NON_US_MARKERS):
        return False
    toks = [t.lower() for t in _tokens(location_text)]
    if any(t in US_STATES or t in US_CITIES for t in toks):
        return True
    if any(t.strip().upper() in US_STATE_ABBR for t in _tokens(location_text) if len(t.strip()) == 2):
        return True
    if re.search(r"\bremote\b", low) and not toks[1:]:
        return None                                              # bare "Remote" -- can't tell
    return None


def classify_remote(location_text, title, metadata_text="", content_text=""):
    """Return (value, confidence). value in remote|hybrid|onsite|unknown."""
    loc = f"{location_text} {title}".lower()
    if "hybrid" in loc:
        return "hybrid", "high"
    if re.search(r"\bremote\b", loc):
        return "remote", "high"
    if re.search(r"\b(on-?site|in[- ]office)\b", loc):
        return "onsite", "high"
    meta = metadata_text.lower()
    tag = re.search(r"#li-(remote|hybrid|onsite)", content_text.lower())      # LinkedIn workplace tags are reliable
    if tag:
        return tag.group(1), "medium"
    if "hybrid" in meta:
        return "hybrid", "medium"
    if re.search(r"\bremote\b", meta):
        return "remote", "medium"
    if re.search(r"\b(on-?site|in[- ]office)\b", meta):
        return "onsite", "medium"
    body = content_text.lower()
    if re.search(r"\b(fully remote|remote-first|remote first|this (role|position) is remote|work remotely|remote (role|position|opportunity))\b", body):
        return "remote", "low"
    if re.search(r"\bhybrid\b", body):
        return "hybrid", "low"
    if re.search(r"\b(on-?site|in[- ]office|in the office)\b", body) and re.search(r"\b(days? (a|per) week|5 days)\b", body):
        return "onsite", "low"
    return "unknown", "none"


SALARY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])\s*(?:-|–|—|to)\s*\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])"
)


def _money(s):
    s = s.replace(",", "").replace(" ", "")
    if s[-1] in "kK":
        return int(float(s[:-1]) * 1000)
    return int(s)


SALARY_USD_RE = re.compile(r"(?:USD\s*\$?\s*)?(\d{2,3},\d{3})\s*(?:USD)?\s*(?:-|–|—|to)\s*\$?\s*(\d{2,3},\d{3})\s*USD\b")


def salary_from_text(text):
    for m in SALARY_USD_RE.finditer(text):
        lo, hi = _money(m.group(1)), _money(m.group(2))
        if 40_000 <= lo <= hi <= 2_000_000:
            return {"min": lo, "max": hi, "currency": "USD", "source": "description"}
    for m in SALARY_RE.finditer(text):
        lo, hi = _money(m.group(1)), _money(m.group(2))
        if 40_000 <= lo <= hi <= 2_000_000:
            return {"min": lo, "max": hi, "currency": "USD", "source": "description"}
    return None


# --------------------------------------------------------------- adapters --
# Each adapter returns a list of normalized job dicts for a company.

def fetch_greenhouse(company):
    board = company["board"]
    data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "") or ""
        if title_matches(title):
            # The list endpoint omits pay ranges; the per-job endpoint has them.
            try:
                detail = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{j['id']}?pay_transparency=true")
                for k in ("content", "pay_input_ranges", "metadata", "offices", "location"):
                    if detail.get(k) is not None:
                        j[k] = detail[k]
            except Exception as e:          # noqa: BLE001 - enrichment is best-effort
                log(f"[{company['name']}] detail fetch failed for {j.get('id')}: {e}")
        loc = (j.get("location") or {}).get("name", "") or ""
        offices = [o.get("name", "") for o in (j.get("offices") or []) if o.get("name")]
        office_locs = [o.get("location", "") for o in (j.get("offices") or []) if o.get("location")]
        meta_text = " ".join(f"{m.get('name','')}: {m.get('value','')}" for m in (j.get("metadata") or []) if m.get("value"))
        content = strip_html(j.get("content", ""))
        salary = None
        ranges = j.get("pay_input_ranges") or j.get("pay_ranges") or []
        if ranges:
            r = ranges[0]
            lo = r.get("min_cents"); hi = r.get("max_cents")
            if lo is not None and hi is not None:
                salary = {"min": lo // 100, "max": hi // 100,
                          "currency": r.get("currency_type", "USD"), "source": "posting"}
        if salary is None:
            salary = salary_from_text(content)
        out.append({
            "job_id": str(j.get("id")),
            "req_id": j.get("requisition_id") or None,
            "title": title,
            "location": loc,
            "location_detail": " | ".join(x for x in office_locs or offices if x),
            "department": ", ".join(d.get("name", "") for d in (j.get("departments") or []) if d.get("name")),
            "published_at": j.get("first_published"),
            "updated_at": j.get("updated_at"),
            "url": j.get("absolute_url"),
            "salary": salary,
            "_meta_text": meta_text,
            "_content": content,
        })
    return out


def fetch_lever(company):
    board = company["board"]
    data = fetch_json(f"https://api.lever.co/v0/postings/{board}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ""
        all_locs = j.get("allLocations") or []
        content = strip_html(j.get("descriptionPlain") or j.get("description") or "")
        created = j.get("createdAt")
        published = datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat() if created else None
        salary = None
        sr = j.get("salaryRange") or {}
        if sr.get("min") and sr.get("max"):
            salary = {"min": int(sr["min"]), "max": int(sr["max"]), "currency": sr.get("currency", "USD"), "source": "posting"}
        if salary is None:
            salary = salary_from_text(content)
        out.append({
            "job_id": j.get("id"),
            "req_id": None,
            "title": j.get("text", ""),
            "location": loc,
            "location_detail": " | ".join(all_locs),
            "department": " / ".join(x for x in (cats.get("team"), cats.get("department")) if x),
            "published_at": published,
            "updated_at": None,
            "url": j.get("hostedUrl"),
            "salary": salary,
            "_meta_text": f"workplaceType: {j.get('workplaceType','')}",
            "_content": content,
        })
    return out


def fetch_ashby(company):
    board = company["board"]
    data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "") or ""
        secondary = [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        content = strip_html(j.get("descriptionHtml") or j.get("descriptionPlain") or "")
        salary = None
        comp = j.get("compensation") or {}
        tiers = comp.get("compensationTiers") or []
        for t in tiers:
            for c in t.get("components") or []:
                if c.get("compensationType") == "Salary" and c.get("minValue") and c.get("maxValue"):
                    salary = {"min": int(c["minValue"]), "max": int(c["maxValue"]),
                              "currency": c.get("currencyCode", "USD"), "source": "posting"}
                    break
            if salary:
                break
        if salary is None and comp.get("compensationTierSummary"):
            salary = salary_from_text(comp["compensationTierSummary"])
        if salary is None:
            salary = salary_from_text(content)
        wt = j.get("workplaceType") or ""
        meta = f"workplaceType: {wt}" + (" remote" if j.get("isRemote") else "")
        out.append({
            "job_id": j.get("id"),
            "req_id": None,
            "title": j.get("title", ""),
            "location": loc,
            "location_detail": " | ".join(x for x in secondary if x),
            "department": " / ".join(x for x in (j.get("department"), j.get("team")) if x),
            "published_at": j.get("publishedAt"),
            "updated_at": None,
            "url": j.get("jobUrl") or j.get("applyUrl"),
            "salary": salary,
            "_meta_text": meta,
            "_content": content,
        })
    return out


COUNTRY_NAMES = {'US': 'United States', 'CA': 'Canada', 'MX': 'Mexico', 'BR': 'Brazil', 'AR': 'Argentina', 'CL': 'Chile', 'CO': 'Colombia', 'PE': 'Peru', 'CR': 'Costa Rica', 'GB': 'United Kingdom', 'UK': 'United Kingdom', 'IE': 'Ireland', 'DE': 'Germany', 'FR': 'France', 'ES': 'Spain', 'PT': 'Portugal', 'IT': 'Italy', 'NL': 'Netherlands', 'BE': 'Belgium', 'CH': 'Switzerland', 'AT': 'Austria', 'SE': 'Sweden', 'NO': 'Norway', 'DK': 'Denmark', 'FI': 'Finland', 'PL': 'Poland', 'CZ': 'Czech Republic', 'RO': 'Romania', 'BG': 'Bulgaria', 'HU': 'Hungary', 'GR': 'Greece', 'TR': 'Turkey', 'UA': 'Ukraine', 'IL': 'Israel', 'AE': 'United Arab Emirates', 'SA': 'Saudi Arabia', 'QA': 'Qatar', 'EG': 'Egypt', 'ZA': 'South Africa', 'NG': 'Nigeria', 'KE': 'Kenya', 'MA': 'Morocco', 'IN': 'India', 'PK': 'Pakistan', 'BD': 'Bangladesh', 'LK': 'Sri Lanka', 'SG': 'Singapore', 'MY': 'Malaysia', 'ID': 'Indonesia', 'TH': 'Thailand', 'VN': 'Vietnam', 'PH': 'Philippines', 'JP': 'Japan', 'KR': 'South Korea', 'CN': 'China', 'HK': 'Hong Kong', 'TW': 'Taiwan', 'AU': 'Australia', 'NZ': 'New Zealand'}


NON_US_MARKERS |= {v.lower() for k, v in COUNTRY_NAMES.items() if k != "US"}


def fetch_smartrecruiters(company):
    """SmartRecruiters public Posting API (no auth). board = the company identifier
    in https://api.smartrecruiters.com/v1/companies/<identifier>/postings"""
    board = company["board"]
    base = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
    items, offset = [], 0
    while True:
        page = fetch_json(f"{base}?limit=100&offset={offset}")
        items.extend(page.get("content", []))
        offset += 100
        if offset >= int(page.get("totalFound", 0)) or not page.get("content"):
            break
    out = []
    for j in items:
        title = j.get("name", "") or ""
        loc = j.get("location") or {}
        code = (loc.get("country") or "").upper()
        country = COUNTRY_NAMES.get(code, f"country {code}" if code else "")
        parts, seen = [], set()
        for x in (loc.get("city"), loc.get("region"), country):
            if x and x.lower() not in seen:
                parts.append(x); seen.add(x.lower())
        loc_name = ", ".join(parts)
        flags = []
        if loc.get("remote"):
            flags.append("Remote")
        if loc.get("hybrid"):
            flags.append("Hybrid")
        if flags:
            loc_name = f"{' / '.join(flags)} - {loc_name}" if loc_name else " / ".join(flags)
        content, url, salary = "", f"https://jobs.smartrecruiters.com/{board}/{j.get('id')}", None
        if title_matches(title):
            try:
                d = fetch_json(f"{base}/{j.get('id')}")
                secs = (d.get("jobAd") or {}).get("sections") or {}
                content = strip_html(" ".join((secs.get(k) or {}).get("text", "") for k in
                                              ("jobDescription", "qualifications", "additionalInformation", "companyDescription")))
                # postingUrl is the job description page; applyUrl skips straight to the form
                url = d.get("postingUrl") or url
                salary = salary_from_text(content)
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {j.get('id')}: {e}")
        meta = " ".join(f"{c.get('fieldLabel','')}: {c.get('valueLabel','')}" for c in (j.get("customField") or []) if c.get("valueLabel"))
        out.append({
            "job_id": str(j.get("id")),
            "req_id": j.get("refNumber") or None,
            "title": title,
            "location": loc_name,
            "location_detail": "",
            "department": (j.get("department") or {}).get("label", "") or "",
            "published_at": j.get("releasedDate"),
            "updated_at": None,
            "url": url,
            "salary": salary,
            "_meta_text": meta + (" remote" if loc.get("remote") else "") + (" hybrid" if loc.get("hybrid") else ""),
            "_content": content,
        })
    return out


import http.cookiejar
_WD_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_WD_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) job-watch/1.0", "Accept": "application/json",
               "Accept-Language": "en-US,en;q=0.9"}


def _retry(fn, tries=4):
    import time
    for i in range(tries):
        try:
            return fn()
        except Exception as e:          # noqa: BLE001 - transient 5xx / timeouts are common on Workday
            if i == tries - 1:
                raise
            log(f"  retry {i + 1} after: {e}")
            time.sleep(2 * (i + 1))


def wd_get(url, timeout=60, as_json=True):
    def go():
        req = urllib.request.Request(url, headers=_WD_HEADERS)
        with _WD_OPENER.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if as_json else raw
    return _retry(go)


def fetch_json_post(url, body, timeout=60):
    def go():
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={**_WD_HEADERS, "Content-Type": "application/json"})
        with _WD_OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    return _retry(go)


DEBUG = os.environ.get("JOBWATCH_DEBUG") == "1"


def debug_dump(company, payload):
    if not DEBUG:
        return
    os.makedirs(os.path.join(ROOT, "data", "debug"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "debug", f"{company['name'].replace(' ', '_')}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False, default=str)


def fetch_workday(company):
    """Workday hosted career sites.  board = "<tenant>.<wd#>/<site>", e.g. "hitachi.wd1/hitachi"
    for https://hitachi.wd1.myworkdayjobs.com/hitachi .  Optional "filter": comma-separated
    substrings; a posting is kept only if one of them appears in its detail (e.g. the hiring
    company name on a shared group-wide board)."""
    tenant_host, site = company["board"].split("/", 1)
    tenant = tenant_host.split(".")[0]
    base = f"https://{tenant_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    filters = [x.strip().lower() for x in company.get("filter", "").split(",") if x.strip()]
    search = "" if company.get("scan", "").lower() == "all" else " ".join(TITLE_KEYWORDS)
    # optional facets, e.g.  facets: jobFamilyGroup=abc123,def456; locationCountry=xyz
    facets = {}
    for part in company.get("facets", "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            facets[k.strip()] = [x.strip() for x in v.split(",") if x.strip()]
    postings, offset, first_page, pages = [], 0, None, []
    try:
        wd_get(f"https://{tenant_host}.myworkdayjobs.com/{site}", as_json=False)     # establishes the session cookie
    except Exception as e:          # noqa: BLE001
        log(f"[{company['name']}] cookie warm-up failed: {e}")
    while True:
        page = fetch_json_post(f"{base}/jobs", {"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": search})
        pages.append((offset, len(page.get("jobPostings") or []), page.get("total")))
        if first_page is None:
            first_page = page
        items = page.get("jobPostings") or []
        postings.extend(items)
        offset += 20
        total = int((first_page or {}).get("total") or 0)          # later pages report total=0
        if not items or offset >= total or offset > 6000:
            break
    out, first_detail, orgs = [], None, {}
    for jp in postings:
        title = jp.get("title", "") or ""
        if not title_matches(title):
            continue
        path = jp.get("externalPath") or ""
        try:
            d = wd_get(f"{base}{path}")
        except Exception as e:          # noqa: BLE001
            log(f"[{company['name']}] detail fetch failed for {path}: {e}")
            continue
        info = d.get("jobPostingInfo") or {}
        if first_detail is None:
            first_detail = d
        content = strip_html(info.get("jobDescription", ""))
        org = (d.get("hiringOrganization") or {}).get("name", "") if isinstance(d.get("hiringOrganization"), dict) else ""
        orgs[org] = orgs.get(org, 0) + 1
        hay = (org + " " + content[:1500]).lower()
        if filters and not any(f in hay for f in filters):
            continue
        locs = [info.get("location", "")] + [x for x in (info.get("additionalLocations") or []) if x]
        country = (info.get("country") or {}).get("descriptor", "") if isinstance(info.get("country"), dict) else ""
        loc_name = "; ".join(x for x in locs if x) or jp.get("locationsText", "") or ""
        if not loc_name:                                   # fall back to the requisition location, then the description's "Location:" line
            rl = info.get("jobRequisitionLocation") or {}
            loc_name = re.sub(r"^\([A-Z]+\)", "", rl.get("descriptor", "") or "").replace("_", " ").strip()
            if not country and isinstance(rl.get("country"), dict):
                country = rl["country"].get("descriptor", "") or COUNTRY_NAMES.get(rl["country"].get("alpha2Code", ""), "")
            m = re.search(r"Location:\s*(.+?)\s*(?:Job ID|Req(?:uisition)? ID|Date Posted|$)", content[:600])
            if m and len(m.group(1)) < 80:
                loc_name = m.group(1).strip(" .,:") or loc_name
        if country and country.lower().split(" of ")[0] not in loc_name.lower():     # "United States of America" vs "United States"
            loc_name = f"{loc_name}, {country}" if loc_name else country
        remote_type = (info.get("remoteType") or "")
        published = info.get("startDate") or None          # YYYY-MM-DD (Workday gives no time of day)
        m = re.search(r"Posted\s+(Today|Yesterday|(\d+)\+?\s+Days?\s+Ago)", jp.get("postedOn", "") or info.get("postedOn", "") or "", re.I)
        if m:                                              # some tenants bump startDate on every refresh; "Posted N Days Ago" is steadier
            days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
            derived = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            if "+" not in m.group(0) and (not published or derived < published):
                published = derived
        out.append({
            "job_id": info.get("jobReqId") or path.rsplit("_", 1)[-1] or path,
            "req_id": info.get("jobReqId") or None,
            "title": title,
            "location": loc_name,
            "location_detail": "",
            "department": org,
            "published_at": published,
            "updated_at": None,
            "url": info.get("externalUrl") or f"https://{tenant_host}.myworkdayjobs.com/{site}{path}",
            "salary": salary_from_text(content),
            "_meta_text": f"remoteType: {remote_type} timeType: {info.get('timeType', '')} postedOn: {jp.get('postedOn', '')} "
                          + ("remote" if re.search(r"Remote:\s*Yes", content[:800]) or remote_type.lower() in ("virtual", "remote") else "")
                          + (" hybrid" if remote_type.lower() == "hybrid" else ""),
            "_content": content,
        })
    debug_dump(company, {"first_page_keys": list((first_page or {}).keys()), "first_postings": postings[:3],
                         "facets": [(f.get("facetParameter"), [(v.get("descriptor"), v.get("id")) for v in (f.get("values") or [])[:15]])
                                    for f in ((first_page or {}).get("facets") or [])],
                         "first_detail": first_detail, "kept": len(out), "scanned": len(postings),
                         "orgs_of_title_matches": orgs, "total_reported": (first_page or {}).get("total"), "pages": pages[:12],
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:500]} for r in out],
                         "pay_snippets": [[r["_content"][max(0, m.start() - 120): m.end() + 60] for m in list(re.finditer(r"USD|\$\s?\d", r["_content"]))[:3]] for r in out[:6]]})
    return out


def fetch_phenom(company):
    """Phenom People career sites (URLs like https://<host>/<country>/<lang>/search-results).
    board = "<host>/<country>/<lang>", e.g. "vgcareers.virgingalactic.com/global/en" """
    host, country, lang = company["board"].split("/", 2)
    base = f"https://{host}"
    body_base = {"lang": f"{lang}_{country}", "deviceType": "desktop", "country": country, "pageName": "search-results",
                 "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "jobs": True, "counts": True,
                 "all_fields": ["category", "country", "state", "city", "type"], "size": 50, "clearAll": False,
                 "jdsource": "facets", "isSliderEnable": False, "siteType": "external", "keywords": "",
                 "global": country == "global", "selected_fields": {}}
    jobs, offset, first = [], 0, None
    while True:
        page = fetch_json_post(f"{base}/widgets", {**body_base, "from": offset})
        rs = (page.get("refineSearch") or {})
        data = rs.get("data") or {}
        items = data.get("jobs") or []
        if first is None:
            first = {"keys": list(page.keys()), "rs_keys": list(rs.keys()), "data_keys": list(data.keys()),
                     "totalHits": rs.get("totalHits") or data.get("totalHits"), "sample": items[:2]}
        jobs.extend(items)
        offset += len(items)
        total = int(rs.get("totalHits") or data.get("totalHits") or 0)
        if not items or offset >= total or offset > 5000:
            break
    out = []
    for j in jobs:
        title = j.get("title", "") or ""
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x) or (j.get("location") or "")
        content, url = "", j.get("applyUrl") or ""
        seq = j.get("jobSeqNo") or j.get("jobId") or ""
        if title_matches(title):
            # job page: the Phenom "jobDetail" widget returns the full description
            try:
                d = fetch_json_post(f"{base}/widgets", {"lang": f"{lang}_{country}", "deviceType": "desktop", "country": country,
                                                        "pageName": "job-details", "ddoKey": "jobDetail", "jobId": seq, "siteType": "external"})
                jd = (d.get("jobDetail") or {}).get("data") or {}
                jo = jd.get("job") or jd
                content = strip_html(jo.get("description") or jo.get("descriptionTeaser") or "")
                url = jo.get("jobUrl") or url
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {seq}: {e}")
                content = strip_html(j.get("descriptionTeaser") or "")
        # Always link to the Phenom job page (applyUrl drops straight into the application form).
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "job"
        url = f"{base}/{country}/{lang}/job/{seq}/{slug}" if seq else url
        posted = j.get("postedDate") or j.get("dateCreated") or None
        if isinstance(posted, (int, float)):
            posted = datetime.fromtimestamp(posted / 1000, tz=timezone.utc).isoformat()
        out.append({
            "job_id": str(seq or j.get("reqId") or title),
            "req_id": j.get("reqId") or None,
            "title": title,
            "location": loc,
            "location_detail": "; ".join(x for x in (j.get("multi_location") or []) if isinstance(x, str)),
            "department": j.get("category") or "",
            "published_at": posted,
            "updated_at": None,
            "url": url,
            "salary": salary_from_text(content) if content else None,
            "_meta_text": f"type: {j.get('type', '')} workplace: {j.get('workplaceType', '') or j.get('remote', '')}",
            "_content": content,
        })
    debug_dump(company, {"first_page": first, "scanned": len(jobs), "kept": len([o for o in out if title_matches(o['title'])]),
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:400]} for r in out if title_matches(r["title"])]})
    return out


def fetch_jobvite(company):
    """Jobvite career pages (https://jobs.jobvite.com/<token>/jobs).  No JSON feed, so this
    parses the HTML job list and each matching job's page (which carries schema.org JSON-LD)."""
    token = company["board"]
    base = f"https://jobs.jobvite.com/{token}"
    html_list = wd_get(f"{base}/jobs", as_json=False)
    out, first = [], None
    # Each row: <a href="/<token>/job/<id>" class="jv-job-list-name">Title</a> ... <div class="jv-job-list-location">Loc</div>
    rows = re.findall(r'href="(/' + re.escape(token) + r'/job/([^"]+))"[^>]*class="jv-job-list-name"[^>]*>(.*?)</a>(.*?)(?=<a [^>]*class="jv-job-list-name"|</table>|$)',
                      html_list, re.S)
    if not rows:   # alternate markup: name inside an <a> that comes after the class attribute
        rows = re.findall(r'<a[^>]*class="jv-job-list-name"[^>]*href="(/' + re.escape(token) + r'/job/([^"]+))"[^>]*>(.*?)</a>(.*?)(?=<a [^>]*class="jv-job-list-name"|</table>|$)',
                          html_list, re.S)
    for href, jid, title_html, rest in rows:
        title = strip_html(title_html).strip()
        locm = re.search(r'jv-job-list-location[^>]*>(.*?)</', rest, re.S)
        loc = strip_html(locm.group(1)).strip() if locm else ""
        content, published, salary, url = "", None, None, f"{base}/job/{jid}"
        if title_matches(title):
            try:
                page = wd_get(url, as_json=False)
                ld = None
                for blob in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
                    try:
                        obj = json.loads(blob.strip())
                        objs = obj if isinstance(obj, list) else [obj]
                        ld = next((o for o in objs if isinstance(o, dict) and o.get("@type") == "JobPosting"), None)
                        if ld:
                            break
                    except Exception:
                        continue
                if first is None:
                    first = {"ld_keys": list(ld.keys()) if ld else None, "page_head": re.sub(r"\s+", " ", strip_html(page))[:600]}
                if ld:
                    content = strip_html(ld.get("description", ""))
                    published = ld.get("datePosted")
                    jl = ld.get("jobLocation")
                    jl = jl[0] if isinstance(jl, list) and jl else jl
                    addr = (jl or {}).get("address") or {}
                    if isinstance(addr, dict):
                        parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
                        loc2 = ", ".join(x for x in parts if isinstance(x, str) and x)
                        if loc2 and len(loc2) > len(loc):
                            loc = loc2
                    if str(ld.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                        loc = f"Remote - {loc}" if loc else "Remote"
                    bs = ld.get("baseSalary") or {}
                    v = bs.get("value") if isinstance(bs, dict) else None
                    if isinstance(v, dict) and v.get("minValue") and v.get("maxValue"):
                        salary = {"min": int(float(v["minValue"])), "max": int(float(v["maxValue"])),
                                  "currency": bs.get("currency", "USD"), "source": "posting"}
                if not content:
                    m = re.search(r'jv-job-detail-description[^>]*>(.*?)<div[^>]*class="jv-job-detail-apply', page, re.S)
                    content = strip_html(m.group(1)) if m else strip_html(page)[:8000]
                if salary is None:
                    salary = salary_from_text(content)
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {jid}: {e}")
        out.append({
            "job_id": jid, "req_id": None, "title": title, "location": loc, "location_detail": "",
            "department": "", "published_at": published, "updated_at": None, "url": url, "salary": salary,
            "_meta_text": "", "_content": content,
        })
    debug_dump(company, {"rows_found": len(rows), "first_detail": first, "sample_rows": [r[2][:80] + " @ " + strip_html(re.search(r'jv-job-list-location[^>]*>(.*?)</', r[3], re.S).group(1) if re.search(r'jv-job-list-location[^>]*>(.*?)</', r[3], re.S) else '?') for r in rows[:5]],
                         "html_head": html_list[:1500] if not rows else None,
                         "job_links": sorted(set(re.findall(r'href="([^"]*?/job/[^"]*)"', html_list)))[:40] if not rows else None,
                         "around_first_job": (lambda i: html_list[max(0, i - 1500): i + 1500] if i >= 0 else None)(html_list.find("/job/")) if not rows else None,
                         "scripts": re.findall(r'<script[^>]+src="([^"]+)"', html_list)[:20] if not rows else None,
                         "json_blobs": [b[:300] for b in re.findall(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', html_list, re.S)][:5] if not rows else None,
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:300]} for r in out if title_matches(r["title"])]})
    return out


def fetch_unknown(company):
    """ats: unknown -- placeholder for a company whose job-board system hasn't been identified yet.
    Returns no jobs; in debug mode it records the careers page's scripts/iframes/links so the
    board behind it can be found."""
    if DEBUG and company.get("url"):
        page = wd_get(company["url"], as_json=False)
        debug_dump(company, {
            "scripts": re.findall(r'<script[^>]+src="([^"]+)"', page)[:40],
            "iframes": re.findall(r'<iframe[^>]+src="([^"]+)"', page)[:10],
            "ats_mentions": sorted(set(re.findall(r'https?://[^"\' <>]*(?:greenhouse|lever\.co|ashbyhq|myworkdayjobs|smartrecruiters|icims|jobvite|bamboohr|rippling|workable|applytojob|phenom|eightfold|successfactors|taleo|ultipro|paylocity|paycom|adp|recruitee|teamtailor|breezy|jazz|comeet|pinpoint|dover|wellfound|gem\.com)[^"\' <>]*', page, re.I)))[:30],
            "data_attrs": re.findall(r'(data-[a-z\-]*(?:board|job|career|token|company)[a-z\-]*="[^"]+")', page, re.I)[:20],
            "fetch_calls": re.findall(r'(?:fetch|axios\.get|\$\.getJSON|XMLHttpRequest)\(["\']([^"\']+)', page)[:20],
            "head": page[:800],
        })
    return []


def fetch_rippling(company):
    """Rippling ATS boards (https://ats.rippling.com/<board>/jobs) via Rippling's public board API."""
    board = company["board"]
    base = f"https://api.rippling.com/platform/api/ats/v1/board/{board}"
    data = wd_get(f"{base}/jobs")
    items = data if isinstance(data, list) else (data.get("items") or data.get("jobs") or data.get("results") or [])
    out, first_detail = [], None
    for j in items:
        title = j.get("name") or j.get("title") or ""
        wl = j.get("workLocation") or j.get("location") or {}
        loc = (wl.get("label") or wl.get("name") or ", ".join(x for x in (wl.get("city"), wl.get("state"), wl.get("country")) if x)) if isinstance(wl, dict) else str(wl or "")
        locs = j.get("workLocations") or []
        extra = "; ".join((x.get("label") or x.get("name") or "") for x in locs if isinstance(x, dict) and (x.get("label") or x.get("name")))
        if not loc and extra:
            loc = extra
        content, salary, published = "", None, j.get("createdAt") or j.get("publishedAt") or j.get("postedAt")
        jid = j.get("uuid") or j.get("id")
        url = j.get("url") or f"https://ats.rippling.com/{board}/jobs/{jid}"
        if title_matches(title):
            try:
                try:
                    d = wd_get(f"{base}/jobs/{jid}")
                except Exception:                       # no detail endpoint: read the public job page instead
                    page = wd_get(url, as_json=False)
                    m = re.search(r"<main[^>]*>(.*?)</main>", page, re.S)
                    d = {"description": (m.group(1) if m else page)}
                if first_detail is None:
                    first_detail = {k: (v if k != "description" else str(v)[:300]) for k, v in d.items()} if isinstance(d, dict) else d
                if isinstance(d, dict):
                    content = strip_html(d.get("description") or d.get("descriptionHtml") or d.get("content") or "")
                    published = d.get("createdAt") or d.get("publishedAt") or d.get("postedAt") or published
                    comp = d.get("compensation") or d.get("salary") or {}
                    if isinstance(comp, dict) and comp.get("min") and comp.get("max"):
                        salary = {"min": int(float(comp["min"])), "max": int(float(comp["max"])), "currency": comp.get("currency", "USD"), "source": "posting"}
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {jid}: {e}")
            if salary is None:
                salary = salary_from_text(content)
        if isinstance(published, (int, float)):
            published = datetime.fromtimestamp(published / (1000 if published > 1e11 else 1), tz=timezone.utc).isoformat()
        dept = j.get("department")
        dept = dept.get("name", "") if isinstance(dept, dict) else (dept or "")
        wt = j.get("employmentType") or ""
        out.append({
            "job_id": str(jid), "req_id": j.get("requisitionId") or None, "title": title, "location": loc,
            "location_detail": extra if extra != loc else "", "department": dept, "published_at": published, "updated_at": None,
            "url": url, "salary": salary,
            "_meta_text": f"type: {wt} " + " ".join(f"{k}: {v}" for k, v in j.items() if isinstance(v, str) and k.lower() in ("workplacetype", "locationtype", "remote")),
            "_content": content,
        })
    debug_dump(company, {"count": len(items), "top_type": type(data).__name__, "top_keys": list(data.keys()) if isinstance(data, dict) else None,
                         "first_items": items[:2], "first_detail": first_detail,
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:300]} for r in out if title_matches(r["title"])]})
    return out


def fetch_bamboohr(company):
    """BambooHR career sites (https://<token>.bamboohr.com/careers) via their JSON list/detail endpoints."""
    token = company["board"]
    base = f"https://{token}.bamboohr.com/careers"
    data = wd_get(f"{base}/list")
    items = (data.get("result") or []) if isinstance(data, dict) else (data or [])
    out, first_item, first_detail = [], None, None
    for j in items:
        if first_item is None:
            first_item = j
        title = j.get("jobOpeningName") or j.get("title") or ""
        loc = j.get("location") or {}
        loc_name = ", ".join(x for x in (loc.get("city"), loc.get("state"), loc.get("country") if isinstance(loc, dict) else None) if x) if isinstance(loc, dict) else str(loc or "")
        ats = j.get("atsLocation") if isinstance(j.get("atsLocation"), dict) else {}
        for part in (ats.get("city"), ats.get("state") or ats.get("province"), ats.get("country")):
            if part and part.lower() not in loc_name.lower():
                loc_name = f"{loc_name}, {part}" if loc_name else part
        if not loc_name and isinstance(j.get("locationLabel"), str):
            loc_name = j["locationLabel"]
        remote = bool(j.get("isRemote"))
        if remote:
            loc_name = f"Remote - {loc_name}" if loc_name else "Remote"
        jid = str(j.get("id"))
        url = f"{base}/{jid}"
        content, published, salary = "", j.get("datePosted") or None, None
        if title_matches(title):
            try:
                d = wd_get(f"{base}/{jid}/detail")
                jo = ((d.get("result") or {}).get("jobOpening") or {}) if isinstance(d, dict) else {}
                if first_detail is None:
                    first_detail = {k: (str(v)[:200] if k == "description" else v) for k, v in jo.items()}
                content = strip_html(jo.get("description") or "")
                published = jo.get("datePosted") or published
                comp = jo.get("compensation") or ""
                if isinstance(comp, str) and comp:
                    salary = salary_from_text(comp)
                if salary is None:
                    salary = salary_from_text(content)
                if not loc_name and isinstance(jo.get("location"), dict):
                    lo = jo["location"]; loc_name = ", ".join(x for x in (lo.get("city"), lo.get("state"), lo.get("country")) if x)
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {jid}: {e}")
        out.append({
            "job_id": jid, "req_id": None, "title": title, "location": loc_name, "location_detail": "",
            "department": j.get("departmentLabel") or "", "published_at": published, "updated_at": None,
            "url": url, "salary": salary, "_meta_text": ("remote " if remote else "") + f"type: {j.get('employmentStatusLabel', '')}",
            "_content": content,
        })
    debug_dump(company, {"count": len(items), "first_item": first_item, "first_detail": first_detail,
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:300]} for r in out if title_matches(r["title"])]})
    return out


def fetch_oracle(company):
    """Oracle Recruiting Cloud (Candidate Experience) sites, e.g. https://apply.ford.com/en/sites/CX_1/...
    board = "<host>/<siteNumber>", e.g. "apply.ford.com/CX_1".  Uses the public CE REST API with a
    keyword search for "product" (these boards are large), then the usual title filter."""
    host, site = company["board"].split("/", 1)
    api = f"https://{host}/hcmRestApi/resources/latest"
    reqs, offset, total, first = [], 0, None, None
    while True:
        finder = f'findReqs;siteNumber={site},keyword="product",limit=25,offset={offset},sortBy=POSTING_DATES_DESC'
        page = fetch_json(f"{api}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations"
                          f"&finder={urllib.parse.quote(finder, safe=';=,')}", timeout=45)
        it = (page.get("items") or [{}])[0]
        items = it.get("requisitionList") or []
        if first is None:
            first = {"keys": list(it.keys()), "total": it.get("TotalJobsCount"), "sample": items[:2]}
        total = int(it.get("TotalJobsCount") or 0)
        reqs.extend(items)
        offset += len(items)
        if not items or offset >= total or offset >= 2000:
            break
    out = []
    for j in reqs:
        title = j.get("Title") or ""
        rid = str(j.get("Id") or "")
        sec = [x.get("Name") for x in (j.get("secondaryLocations") or []) if x.get("Name")]
        content, meta = strip_html(j.get("ShortDescriptionStr") or ""), f"workplace: {j.get('WorkplaceType') or ''}"
        if title_matches(title):
            try:
                byid = 'ById;Id="' + rid + '",siteNumber=' + site
                d = fetch_json(f"{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                               f"&finder={urllib.parse.quote(byid, safe=';=,')}", timeout=45)
                dj = (d.get("items") or [{}])[0]
                content = strip_html(" ".join(dj.get(k) or "" for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                                         "ExternalQualificationsStr", "CorporateDescriptionStr")))
                meta = f"workplace: {dj.get('WorkplaceType') or j.get('WorkplaceType') or ''} {dj.get('JobSchedule') or ''}"
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {rid}: {e}")
        out.append({
            "job_id": rid,
            "req_id": rid or None,
            "title": title,
            "location": j.get("PrimaryLocation") or "",
            "location_detail": "; ".join(sec),
            "department": j.get("JobFamily") or j.get("Organization") or "",
            "published_at": j.get("PostedDate"),
            "updated_at": None,
            "url": f"https://{host}/en/sites/{site}/job/{rid}",
            "salary": salary_from_text(content) if content else None,
            "_meta_text": meta,
            "_content": content,
        })
    debug_dump(company, {"first_page": first, "scanned": len(reqs), "kept": len([o for o in out if title_matches(o["title"])])})
    return out


def fetch_talentbrew(company):
    """Radancy / TalentBrew career sites (URLs like https://<host>/<lang>/search-jobs), e.g. careers.ford.com.
    board = "<host>/<lang>", e.g. "www.careers.ford.com/en".  The search page has a JSON endpoint that
    returns the result list as HTML; each job page carries schema.org JobPosting data for the details."""
    host, lang = company["board"].split("/", 1)
    base = f"https://{host}"
    hdr = {"User-Agent": _BROWSER_UA, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*"}
    seen, rows, first, page, pages = set(), [], None, 1, 1
    while page <= min(pages, 30):
        q = urllib.parse.urlencode({"ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": 100, "Distance": 50,
                                    "RadiusUnitType": 0, "Keywords": "product", "Location": "", "ShowRadius": "False",
                                    "IsPagination": "False" if page == 1 else "True", "CustomFacetName": "", "FacetTerm": "",
                                    "FacetType": 0, "SearchResultsModuleName": "Search Results",
                                    "SearchFiltersModuleName": "Search Filters", "SortCriteria": 0, "SortDirection": 0,
                                    "SearchType": 5, "ResultsType": 0})
        d = fetch_json(f"{base}/{lang}/search-jobs/results?{q}", timeout=45, headers=hdr)
        h = d.get("results") or ""
        if first is None:
            first = {"keys": list(d.keys()), "html_head": h[:1500]}
        m = re.search(r'data-total-pages="(\d+)"', h)
        pages = int(m.group(1)) if m else 1
        found = 0
        for li in re.split(r"<li\b", h)[1:]:
            a = re.search(r'href="([^"]*/job/[^"]+?/(\d+))"', li)
            if not a or a.group(2) in seen:
                continue
            seen.add(a.group(2)); found += 1
            title = re.search(r"<h2[^>]*>(.*?)</h2>", li, re.S) or re.search(r'class="[^"]*job-title[^"]*"[^>]*>(.*?)<', li, re.S)
            loc = re.search(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</span>', li, re.S)
            posted = re.search(r'class="[^"]*job-date-posted[^"]*"[^>]*>(.*?)</span>', li, re.S)
            clean = lambda x: re.sub(r"\s+", " ", strip_html(x.group(1))).strip() if x else ""
            href = a.group(1)
            rows.append({"id": a.group(2), "url": href if href.startswith("http") else base + href,
                         "title": clean(title), "location": clean(loc), "posted": clean(posted)})
        if not found:
            break
        page += 1
    out = []
    for r in rows:
        content, meta, salary, posted, loc = "", "", None, None, r["location"]
        if r["posted"]:
            for fmt in ("%m/%d/%Y", "%b. %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
                try: posted = datetime.strptime(r["posted"], fmt).replace(tzinfo=timezone.utc).isoformat(); break
                except ValueError: pass
        if title_matches(r["title"]):
            try:
                page_html, _ = fetch_text(r["url"], timeout=45, headers={"User-Agent": _BROWSER_UA})
                for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page_html, re.S):
                    try: jp = json.loads(blob)
                    except ValueError: continue
                    jp = next((x for x in (jp if isinstance(jp, list) else [jp]) if isinstance(x, dict) and x.get("@type") == "JobPosting"), None)
                    if not jp: continue
                    content = strip_html(jp.get("description") or "")
                    posted = jp.get("datePosted") or posted
                    meta = f"workplace: {jp.get('jobLocationType') or ''} {jp.get('employmentType') or ''}"
                    locs = jp.get("jobLocation") or []
                    for L in (locs if isinstance(locs, list) else [locs]):
                        ad = (L or {}).get("address") or {}
                        c = (ad.get("addressCountry") or {})
                        c = c.get("name") if isinstance(c, dict) else c
                        full = ", ".join(x for x in (ad.get("addressLocality"), ad.get("addressRegion"), c) if x)
                        if full: loc = full if not loc or loc == r["location"] else loc + "; " + full
                    bs = (jp.get("baseSalary") or {}).get("value") or {}
                    if isinstance(bs, dict) and bs.get("minValue") and bs.get("maxValue") and str(bs.get("unitText", "YEAR")).upper() == "YEAR":
                        salary = {"min": float(bs["minValue"]), "max": float(bs["maxValue"]), "currency": (jp.get("baseSalary") or {}).get("currency") or "USD", "source": "posting"}
                    break
                if not content:
                    m = re.search(r'class="[^"]*ats-description[^"]*"[^>]*>(.*?)</section>', page_html, re.S)
                    content = strip_html(m.group(1)) if m else ""
            except Exception as e:          # noqa: BLE001
                log(f"[{company['name']}] detail fetch failed for {r['id']}: {e}")
        out.append({
            "job_id": r["id"], "req_id": None, "title": r["title"], "location": loc or r["location"],
            "location_detail": r["location"] if loc and loc != r["location"] else "",
            "department": "", "published_at": posted, "updated_at": None, "url": r["url"],
            "salary": salary or (salary_from_text(content) if content else None),
            "_meta_text": meta, "_content": content,
        })
    debug_dump(company, {"first_page": first, "pages": pages, "scanned": len(rows),
                         "kept_rows": [{k: v for k, v in o.items() if k != "_content"} for o in out if title_matches(o["title"])][:10]})
    return out


def fetch_jibe(company):
    """iCIMS / Jibe career sites (https://<host>/careers-home/jobs) via the site's /api/jobs JSON endpoint.
    board = "<host>", e.g. "careers.rivian.com" """
    host = company["board"]
    base = f"https://{host}/api/jobs"
    items, page, first = [], 1, None
    while True:
        data = wd_get(f"{base}?page={page}&limit=100&sortBy=relevance&descending=false&internal=false&keywords={urllib.parse.quote(' '.join(TITLE_KEYWORDS))}")
        if first is None:
            first = {"keys": list(data.keys()) if isinstance(data, dict) else type(data).__name__, "totalCount": data.get("totalCount") if isinstance(data, dict) else None,
                     "sample": (data.get("jobs") or [None])[0] if isinstance(data, dict) else None}
        jobs = (data.get("jobs") or []) if isinstance(data, dict) else []
        items.extend(jobs)
        total = int((data.get("totalCount") or 0) if isinstance(data, dict) else 0)
        if not jobs or len(items) >= total or page > 60:
            break
        page += 1
    out = []
    for w in items:
        j = w.get("data") if isinstance(w, dict) and isinstance(w.get("data"), dict) else w
        title = j.get("title", "") or ""
        loc = j.get("location_name") or j.get("full_location") or ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        content = strip_html(j.get("description") or "")
        meta = " ".join(f"{k}: {v}" for k, v in j.items() if isinstance(v, str) and k.lower() in ("category", "employment_type", "remote", "workplace_type", "work_location_type", "location_type"))
        posted = j.get("posted_date") or j.get("create_date") or j.get("update_date") or None
        out.append({
            "job_id": str(j.get("req_id") or j.get("id") or j.get("slug")),
            "req_id": j.get("req_id") or None,
            "title": title, "location": loc or "", "location_detail": "",
            "department": ", ".join(x.strip() for x in (j.get("category") if isinstance(j.get("category"), list) else [j.get("category") or j.get("department") or ""]) if x),
            "published_at": posted, "updated_at": j.get("update_date"),
            "url": f"https://{host}/careers-home/jobs/{j.get('req_id') or j.get('slug')}?lang=en-us",
            "salary": salary_from_text(content),
            "_meta_text": meta, "_content": content,
        })
    debug_dump(company, {"first": first, "count": len(items),
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:300]} for r in out if title_matches(r["title"])][:8]})
    return out


ADAPTERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters, "workday": fetch_workday, "phenom": fetch_phenom,
            "jobvite": fetch_jobvite, "rippling": fetch_rippling, "bamboohr": fetch_bamboohr, "jibe": fetch_jibe, "oracle": fetch_oracle, "talentbrew": fetch_talentbrew, "unknown": fetch_unknown}


# --------------------------------------------------------------- filtering --

def title_matches(title, extra_exclude=()):
    t = title.lower()
    if not any(re.search(r"\b" + re.escape(k) + r"s?\b", t) for k in TITLE_KEYWORDS):   # whole word: "Products" yes, "Production" no
        return False
    return not any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in list(TITLE_EXCLUDE_WORDS) + list(extra_exclude))


def company_excludes(company):
    return [x.strip().lower() for x in (company or {}).get("exclude", "").split(",") if x.strip()]


def matches(job, company=None):
    """Apply the watch criteria. Returns (keep, annotated_job)."""
    if not title_matches(job["title"], company_excludes(company)):
        return False, None
    # The posting's own location field is authoritative. Office metadata is only a
    # fallback: boards often attach a generic "United States" office to every role.
    us = classify_us(job["location"])
    if us is None and job["location_detail"]:
        us = classify_us(job["location_detail"])
    if us is None and company and (company.get("country") or "").upper() in ("US", "USA", "UNITED STATES"):
        us = True                                   # company declares it only posts US roles
    if us is False:
        return False, None
    remote, conf = classify_remote(job["location"], job["title"],
                                   " ".join(x for x in (job["_meta_text"], job["location_detail"]) if x),
                                   job["_content"])
    j = {k: v for k, v in job.items() if not k.startswith("_")}
    j["us"] = us                      # True / None (None => "verify" flag on dashboard)
    j["remote"] = remote
    j["remote_confidence"] = conf
    return True, j


# ------------------------------------------------------------------ merge ---

def load_previous():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"generated_at": None, "companies": [], "jobs": []}


def append_changes(lines):
    if not lines:
        return
    os.makedirs(os.path.dirname(CHANGES_FILE), exist_ok=True)
    with open(CHANGES_FILE, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def main(only=None):
    companies = read_companies()
    if only:
        wanted = {o.lower() for o in only}
        companies_to_fetch = [c for c in companies if c["name"].lower() in wanted]
        if not companies_to_fetch:
            raise SystemExit(f"No company named {only} in companies.yml")
    else:
        companies_to_fetch = companies

    prev = load_previous()
    prev_jobs = {j["key"]: j for j in prev.get("jobs", [])}
    prev_companies = {c["name"]: c for c in prev.get("companies", [])}
    ts = now_iso()
    changes = []

    company_status = []
    fetched_keys_by_company = {}

    for c in companies:
        if c not in companies_to_fetch:
            company_status.append(prev_companies.get(c["name"], {**c, "status": "never fetched"}))
            continue
        adapter = ADAPTERS.get(c["ats"])
        status = {**c, "fetched_at": ts}
        if not adapter:
            status.update(status="error", error=f"unknown ats '{c['ats']}' (use greenhouse, lever or ashby)")
            company_status.append(status)
            continue
        try:
            raw = adapter(c)
        except Exception as e:          # noqa: BLE001 - one bad board must never sink the whole sweep
            status.update(status="error", error=str(e)[:500])
            company_status.append(status)
            log(f"[{c['name']}] ERROR {e}")
            continue
        kept = []
        for job in raw:
            ok, j = matches(job, c)
            if ok:
                j["company"] = c["name"]
                j["key"] = f"{c['ats']}:{c['board']}:{j['job_id']}"
                kept.append(j)
        fetched_keys_by_company[c["name"]] = {j["key"] for j in kept}
        status.update(status="ok", error=None, total_jobs=len(raw), matched=len(kept))
        company_status.append(status)
        log(f"[{c['name']}] {len(raw)} jobs on board, {len(kept)} match")

        for j in kept:
            old = prev_jobs.get(j["key"])
            if old:
                j["first_seen"] = old.get("first_seen", ts)
                if old.get("status") == "closed":
                    changes.append(f"{ts} REOPENED {c['name']} | {j['title']} | {j['url']}")
            else:
                j["first_seen"] = ts
                changes.append(f"{ts} NEW {c['name']} | {j['title']} | {j['location']} | {j['url']}")
            j["last_seen"] = ts
            j["status"] = "open"
            j["closed_at"] = None
            prev_jobs[j["key"]] = j

    # Mark jobs that vanished from a successfully fetched board as closed.
    for key, j in list(prev_jobs.items()):
        cname = j.get("company")
        if cname in fetched_keys_by_company and key not in fetched_keys_by_company[cname]:
            if j.get("status") != "closed":
                j["status"] = "closed"
                j["closed_at"] = ts
                changes.append(f"{ts} CLOSED {cname} | {j['title']} | {j['url']}")
        # Prune long-closed roles and roles from companies removed from companies.yml.
        if j.get("status") == "closed" and j.get("closed_at"):
            closed = datetime.fromisoformat(j["closed_at"])
            if datetime.now(timezone.utc) - closed > timedelta(days=KEEP_CLOSED_DAYS):
                del prev_jobs[key]
        if cname not in {c["name"] for c in companies}:
            prev_jobs.pop(key, None)
        elif not title_matches(j.get("title", "")):      # criteria changed since this was recorded
            prev_jobs.pop(key, None)

    def _ts(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0

    jobs = sorted(prev_jobs.values(),
                  key=lambda j: (j["status"] != "open", -_ts(j.get("published_at") or j["first_seen"])))

    out = {
        "generated_at": ts,
        "criteria": {"title_keywords": TITLE_KEYWORDS, "title_exclude_words": TITLE_EXCLUDE_WORDS, "country": "United States"},
        "companies": company_status,
        "jobs": jobs,
    }
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    append_changes(changes)
    log(f"wrote {DATA_FILE}: {sum(1 for j in jobs if j['status']=='open')} open, "
        f"{sum(1 for j in jobs if j['status']=='closed')} recently closed, {len(changes)} changes")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
