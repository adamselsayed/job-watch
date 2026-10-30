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
import urllib.error
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANIES_FILE = os.path.join(ROOT, "companies.yml")
DATA_FILE = os.path.join(ROOT, "data", "jobs.json")
CHANGES_FILE = os.path.join(ROOT, "data", "changes.log")

# ---------------------------------------------------------------- criteria --

TITLE_KEYWORDS = ["product"]          # title must contain one of these (case-insensitive)
TITLE_EXCLUDE_WORDS = ["design", "designer", "marketing"]      # ...and must not contain any of these as a standalone word
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


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "job-watch/1.0 (+github)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


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


def salary_from_text(text):
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
        if country and country.lower() not in loc_name.lower():
            loc_name = f"{loc_name}, {country}" if loc_name else country
        remote_type = (info.get("remoteType") or "")
        published = info.get("startDate") or None          # YYYY-MM-DD (Workday gives no time of day)
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
                         "kept_rows": [{k: v for k, v in r.items() if k != "_content"} | {"_content_head": r["_content"][:500]} for r in out]})
    return out


ADAPTERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters, "workday": fetch_workday}


# --------------------------------------------------------------- filtering --

def title_matches(title):
    t = title.lower()
    if not any(re.search(r"\b" + re.escape(k) + r"s?\b", t) for k in TITLE_KEYWORDS):   # whole word: "Products" yes, "Production" no
        return False
    return not any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in TITLE_EXCLUDE_WORDS)


def matches(job):
    """Apply the watch criteria. Returns (keep, annotated_job)."""
    if not title_matches(job["title"]):
        return False, None
    # The posting's own location field is authoritative. Office metadata is only a
    # fallback: boards often attach a generic "United States" office to every role.
    us = classify_us(job["location"])
    if us is None and job["location_detail"]:
        us = classify_us(job["location_detail"])
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
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
            status.update(status="error", error=str(e))
            company_status.append(status)
            log(f"[{c['name']}] ERROR {e}")
            continue
        kept = []
        for job in raw:
            ok, j = matches(job)
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
