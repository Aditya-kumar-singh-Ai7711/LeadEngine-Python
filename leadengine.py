
import requests
import re
import os
import json
import time
import random
import pandas as pd
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # reads from .env file in same folder

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────
# Create a .env file in this folder with: SERPER_API_KEY=your_key_here
# NEVER paste your key directly in code — anyone who sees the file can steal it
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
if not SERPER_API_KEY:
    raise ValueError("SERPER_API_KEY not found. Create a .env file with: SERPER_API_KEY=your_key_here")

MAX_WORKERS    = 15   # reduced from 20 — avoids IP rate limits
SLEEP_BETWEEN  = 0.3  # seconds between requests

# ─────────────────────────────────────────────────────────────────
# CREDIT-SAVER: Persistent storage files
# ─────────────────────────────────────────────────────────────────
QUERY_HISTORY_FILE = "query_history.json"   # tracks every query run
SEEN_DOMAINS_FILE  = "seen_domains.json"    # tracks every domain ever scraped

def load_json_set(filepath: str) -> set:
    """Load a JSON list file as a Python set. Returns empty set if file missing."""
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return set(json.load(f))
    return set()

def save_json_set(filepath: str, data: set) -> None:
    """Save a Python set as a JSON list file."""
    with open(filepath, "w") as f:
        json.dump(list(data), f, indent=2)

# Load persistent state at startup
_query_history = load_json_set(QUERY_HISTORY_FILE)
_seen_domains  = load_json_set(SEEN_DOMAINS_FILE)

def mark_query_done(query: str) -> None:
    _query_history.add(query.strip().lower())
    save_json_set(QUERY_HISTORY_FILE, _query_history)

def is_query_done(query: str) -> bool:
    return query.strip().lower() in _query_history

def mark_domain_seen(domain: str) -> None:
    _seen_domains.add(domain.strip().lower())
    save_json_set(SEEN_DOMAINS_FILE, _seen_domains)

def is_domain_seen(domain: str) -> bool:
    return domain.strip().lower() in _seen_domains

def estimate_credits(query_pairs: list) -> None:
    """
    Print a credit cost estimate BEFORE running a batch.
    Each pair = 6 base Serper calls (3 queries × 2 endpoints).
    Founder enrichment = ~1 call per high-score lead (estimated at 40% of leads).
    """
    new_pairs = [(n, l) for n, l in query_pairs
                 if not is_query_done(f"{n}|{l}")]
    skipped   = len(query_pairs) - len(new_pairs)

    base_calls      = len(new_pairs) * 6
    enrichment_est  = int(len(new_pairs) * 10 * 0.4)  # ~10 leads/pair, 40% get enriched
    total_est       = base_calls + enrichment_est

    print(f"\n{'─'*50}")
    print(f"  CREDIT ESTIMATE")
    print(f"{'─'*50}")
    print(f"  Total pairs in batch   : {len(query_pairs)}")
    print(f"  Already done (skipped) : {skipped}  ← FREE, no credits used")
    print(f"  New pairs to run       : {len(new_pairs)}")
    print(f"  Base Serper calls      : {base_calls}  (6 per pair)")
    print(f"  Founder enrichment est : ~{enrichment_est}")
    print(f"  TOTAL ESTIMATED COST   : ~{total_est} Serper credits")
    print(f"{'─'*50}")
    if total_est > 300:
        print(f"  ⚠️  HIGH CREDIT USAGE — consider running a smaller batch first")
    else:
        print(f"  ✅ Reasonable credit usage")
    print()

# ─────────────────────────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────────────────────────
BAD_DOMAINS = [
    "clutch.co", "designrush.com", "manifest.com", "goodfirms.co",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com",
    "youtube.com", "reddit.com", "wikipedia.org", "justdial.com",
    "indiamart.com", "magicbricks.com", "99acres.com", "yelp.com",
    "bbb.org", "glassdoor.com", "trustpilot.com", "upwork.com",
    "fiverr.com", "sulekha.com", "amazon.com", "flipkart.com",
    "naukri.com", "indeed.com", "quora.com", "medium.com",
]
BAD_PATHS  = ["/dataset", "/glossary", "/what-is", "/templates",
              "/blog", "/articles", "/news", "/jobs", "/careers"]
BAD_EMAILS = ["example", "wix", "wordpress", "sentry", "noreply",
              "no-reply", "test@", "spam@", "donotreply",
              "unsubscribe", "cdn@", "static@", "mailer@"]
# NOTE: support@ intentionally removed — for small Indian SMBs (CA firms,
# wedding planners, brokers) support@ often goes directly to the owner
BAD_IG     = ['/p/', '/reel/', '/explore/', '/tags/', '/stories/',
              '/tv/', '/accounts/', '/share/']

EMAIL_REGEX = re.compile(
    r"(?<![a-zA-Z0-9_.+-])"
    r"[a-zA-Z0-9_.+-]{2,64}@[a-zA-Z0-9-]{2,255}\.[a-zA-Z]{2,10}"
    r"(?![a-zA-Z0-9_.+-])"
)
PHONE_REGEX = re.compile(r"\+?\d[\d\s\-()]{7,14}\d")
IG_PATTERN  = re.compile(r'instagram\.com/([A-Za-z0-9_.]{2,30})/?(?:\?.*)?$')

# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def is_junk_url(url: str) -> bool:
    u = url.lower()
    return (any(d in u for d in BAD_DOMAINS) or
            any(p in u for p in BAD_PATHS) or
            u.endswith(('.pdf', '.doc', '.docx', '.jpg', '.png')))


def best_email(emails: list) -> str:
    """Score emails and return the most likely to be a real contact."""
    if not emails:
        return "No Email"
    priority = ['founder', 'ceo', 'owner', 'contact', 'hello',
                'info', 'sales', 'team', 'marketing', 'admin']
    for p in priority:
        for e in emails:
            if p in e.lower():
                return e
    # Fallback: shortest email (usually most direct)
    return sorted(emails, key=len)[0]


def clean_phone(raw_phones: list) -> str:
    """Return first phone with 10–13 digits only."""
    for p in raw_phones:
        digits = re.sub(r'\D', '', p)
        if 10 <= len(digits) <= 13:
            return p.strip()
    return "N/A"


def get_company_name(soup, url: str) -> str:
    """Extract clean company name from page."""
    for sel in ["meta[property='og:site_name']", "meta[name='application-name']"]:
        tag = soup.select_one(sel)
        if tag and tag.get("content"):
            return tag["content"].strip()[:60]
    h1 = soup.find("h1")
    if h1:
        txt = h1.get_text().strip()[:60]
        if 2 < len(txt) < 60:
            return txt
    # Fallback to domain name
    domain = urlparse(url).netloc.replace("www.", "")
    return domain.split(".")[0].title()


# ─────────────────────────────────────────────────────────────────
# STAGE 1: URL DISCOVERY (Serper)
# ─────────────────────────────────────────────────────────────────

def get_urls_serper(query: str, endpoint: str = "search") -> list:
    """Get URLs from Google via Serper API."""
    url     = f"https://google.serper.dev/{endpoint}"
    payload = {"q": query, "num": 100} if endpoint == "search" else {"q": query}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    try:
        res    = requests.post(url, headers=headers, json=payload, timeout=12)
        data   = res.json()
        key    = "organic" if endpoint == "search" else "places"
        lk     = "link" if endpoint == "search" else "website"
        return [r[lk] for r in data.get(key, [])
                if r.get(lk) and not is_junk_url(r[lk])]
    except Exception as e:
        print(f"   [Serper error] {query}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# STAGE 1.5: FOUNDER ENRICHMENT (throttled)
# ─────────────────────────────────────────────────────────────────

def find_founder(company_name: str) -> tuple:
    """Search LinkedIn for founder/CEO via Serper (only for top leads)."""
    query   = f'site:linkedin.com/in/ "founder" OR "ceo" OR "owner" "{company_name}"'
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    try:
        res = requests.post(
        "https://google.serper.dev/search",
            headers=headers,
            json={"q": query, "num": 1},
            timeout=6
        )
        organic = res.json().get("organic", [])
        if organic:
            name = organic[0].get("title", "").split(" - ")[0].split("|")[0].strip()
            link = organic[0].get("link", "")
            return name[:50], link
    except Exception:
        pass
    return "Not Found", "N/A"


# ─────────────────────────────────────────────────────────────────
# STAGE 2: SCRAPING + INTEL
# ─────────────────────────────────────────────────────────────────

def scrape_site(url: str, mode: str = "volume") -> dict | None:
    """
    Full scrape pipeline:
    1. Fetch homepage
    2. Extract all signals
    3. Actually scrape contact page (v11 fix — huge email improvement)
    4. Score and filter
    5. Enrich top leads only
    """
    time.sleep(random.uniform(0.2, SLEEP_BETWEEN))
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

    try:
        res = scraper.get(url, timeout=10)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(separator=" ")

        company_name = get_company_name(soup, url)

        # ── 1. Social + contact links ──────────────────────────────
        contact_page     = "N/A"
        linkedin_company = "N/A"
        ig_url           = "N/A"

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            href_lower = href.lower()

            # Contact page
            if ("contact" in href_lower and
                    not href_lower.startswith(("mailto:", "tel:"))):
                if contact_page == "N/A":
                    contact_page = urljoin(url, href)

            # LinkedIn company page
            if "linkedin.com/company" in href_lower:
                linkedin_company = href

            # Instagram — strict pattern matching
            m = IG_PATTERN.search(href)
            if m:
                handle = m.group(1)
                blocklist = {"p", "reel", "explore", "stories", "tv",
                             "accounts", "share", "direct", "about"}
                if handle.lower() not in blocklist:
                    ig_url = f"https://instagram.com/{handle}"

        # ── 2. Email extraction from homepage ──────────────────────
        raw_emails = list(set(EMAIL_REGEX.findall(res.text)))
        clean_emails = [
            e for e in raw_emails
            if not any(b in e.lower() for b in BAD_EMAILS)
            and "." in e.split("@")[-1]
        ]

        # ── 3. CONTACT PAGE SCRAPE (v11 KEY FIX) ──────────────────
        # This is where most businesses hide their real emails
        if contact_page != "N/A":
            try:
                c_res = scraper.get(contact_page, timeout=8)
                if c_res.status_code == 200:
                    c_soup = BeautifulSoup(c_res.text, "html.parser")
                    c_text = c_soup.get_text(separator=" ")
                    # Also check mailto links on contact page
                    for a in c_soup.find_all("a", href=True):
                        if a["href"].startswith("mailto:"):
                            email = a["href"].replace("mailto:", "").split("?")[0].strip()
                            clean_emails.append(email)
                    extra = [
                        e for e in EMAIL_REGEX.findall(c_res.text)
                        if not any(b in e.lower() for b in BAD_EMAILS)
                    ]
                    clean_emails.extend(extra)
            except Exception:
                pass

        # Also check mailto: links on homepage
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                email = a["href"].replace("mailto:", "").split("?")[0].strip()
                if email and "@" in email:
                    clean_emails.append(email)

        clean_emails = list(set(clean_emails))
        primary_email = best_email(clean_emails)

        # ── 4. Phone extraction ────────────────────────────────────
        raw_phones  = PHONE_REGEX.findall(text)
        phone_number = clean_phone(raw_phones)

        # ── 5. Scoring ─────────────────────────────────────────────
        score = 0
        if primary_email != "No Email":     score += 3
        if phone_number != "N/A":           score += 2
        if ig_url != "N/A":                 score += 1
        if linkedin_company != "N/A":       score += 1
        if contact_page != "N/A":           score += 1

        # ── Mode filtering ─────────────────────────────────────────
        # quality = 3: email alone is enough (was 4 — was dropping email-only leads)
        # volume  = 1: any real signal at all (was 2 — was dropping contact-page-only leads)
        min_score = 3 if mode == "quality" else 1
        if score < min_score:
            return None

        # ── 6. Sales angle generation ──────────────────────────────
        text_lower = text.lower()
        word_count = len(text.split())

        has_ga       = "google-analytics" in res.text.lower() or "gtag(" in res.text
        has_fb_pixel = "fbq(" in res.text or "facebook.net/tr" in res.text
        has_cta      = any(cta in text_lower for cta in
                          ["book a call", "schedule", "free consultation",
                           "get a quote", "contact us today", "book now"])
        has_reviews  = any(r in text_lower for r in
                          ["testimonial", "review", "client says", "what our"])

        # Pick the most specific angle (best for cold outreach)
        if primary_email == "No Email":
            angle = "No contact email visible — likely losing inbound leads"
        elif not has_ga and not has_fb_pixel:
            angle = "No analytics tracking — flying blind on website performance"
        elif not has_cta:
            angle = "No clear call-to-action — visitors leave without converting"
        elif not has_reviews:
            angle = "No social proof visible — losing trust from cold visitors"
        elif word_count < 200:
            angle = "Thin website content — poor SEO and low conversion trust"
        else:
            angle = "Standard outreach — mention specific service improvement"

        # ── 7. Founder enrichment (only top leads) ─────────────────
        founder_name    = "Not Found"
        founder_linkedin = "N/A"
        if score >= 5 and len(company_name) > 3:
            founder_name, founder_linkedin = find_founder(company_name)

        return {
            "Company":          company_name,
            "Website":          url,
            "Primary Email":    primary_email,
            "Phone Number":     phone_number,
            "Instagram":        ig_url,
            "LinkedIn Company": linkedin_company,
            "Contact Page":     contact_page,
            "Founder Name":     founder_name,
            "Founder LinkedIn": founder_linkedin,
            "Outreach Angle":   angle,    # ← Use this in your DM/email
            "Has Analytics":    "Yes" if (has_ga or has_fb_pixel) else "No",
            "Has CTA":          "Yes" if has_cta else "No",
            "Score":            score,
            "Status":           "To Contact",
            "Notes":            "",
        }

    except Exception as e:
        return None


# ─────────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────

def run_single(niche: str, location: str, mode: str = "volume") -> pd.DataFrame:
    """Run a single niche + location query."""
    pair_key = f"{niche}|{location}"

    # ── CREDIT SAVER 1: Skip if this exact pair was already run ───
    if is_query_done(pair_key):
        print(f"\n⏭️  SKIPPED (already done): {niche} | {location}  ← 0 credits used")
        return pd.DataFrame()

    print(f"\n🔍 Scanning: {niche} | {location} | mode={mode}")

    queries = [
        f"{niche} {location}",
        f"{niche} agency {location}",
        f"best {niche} {location}",
    ]

    unique_domains: dict = {}
    for q in queries:
        for u in (get_urls_serper(q, "search") + get_urls_serper(q, "places")):
            root = urlparse(u).netloc.replace("www.", "")
            if root and root not in unique_domains:
                unique_domains[root] = u

    # ── CREDIT SAVER 2: Remove domains seen in any previous run ───
    new_domains = {
        root: url for root, url in unique_domains.items()
        if not is_domain_seen(root)
    }
    skipped_count = len(unique_domains) - len(new_domains)
    if skipped_count:
        print(f"   → {skipped_count} domains skipped (seen before) — saved ~{skipped_count} scrape calls")

    final_urls = list(new_domains.values())
    print("\nDEBUG")
    print("Unique domains found:", len(unique_domains))
    print("New domains found:", len(new_domains))
    print(list(unique_domains.keys())[:10])
    print(f"   → {len(final_urls)} new domains to scrape")

    if not final_urls:
        mark_query_done(pair_key)
        return pd.DataFrame()

    leads = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(scrape_site, url, mode): url for url in final_urls}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result:
                leads.append(result)
                # Mark domain as seen immediately so concurrent runs don't overlap
                domain = urlparse(futures[future]).netloc.replace("www.", "")
                mark_domain_seen(domain)
            if i % 20 == 0:
                print(f"   ... {i}/{len(final_urls)} processed | {len(leads)} leads so far")

    # Mark this query pair as done — won't run again
    mark_query_done(pair_key)

    if not leads:
        return pd.DataFrame()

    df = (pd.DataFrame(leads)
          .drop_duplicates(subset=["Company"], keep="first")
          .sort_values("Score", ascending=False)
          .reset_index(drop=True))
    return df


def run_batch(query_pairs: list[tuple], mode: str = "volume") -> pd.DataFrame:
    """Run multiple niche/location pairs and combine results."""
    all_dfs = []
    for niche, location in query_pairs:
        df = run_single(niche, location, mode)
        if not df.empty:
            df["Query"] = f"{niche} | {location}"
            all_dfs.append(df)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = (combined
                .drop_duplicates(subset=["Website"], keep="first")
                .sort_values("Score", ascending=False)
                .reset_index(drop=True))
    return combined


def save_results(df: pd.DataFrame, filename: str) -> None:
    """Save to Excel with formatting."""
    if df.empty:
        print("⚠️  No leads to save.")
        return

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Leads")

        # Auto-size columns
        ws = writer.sheets["Leads"]
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    print(f"\n✅ {len(df)} leads saved → {filename}")


# ─────────────────────────────────────────────────────────────────
# ENTRY POINTS
# ─────────────────────────────────────────────────────────────────

def interactive_mode():
    """Simple interactive CLI."""
    print("\n⚡ LeadEngine v12 — Credit-Safe Mode")
    print("─" * 40)
    print("  Queries done so far  :", len(_query_history))
    print("  Domains seen so far  :", len(_seen_domains))
    print("─" * 40)

    choice = input(
        "Run mode:\n"
        "  1. Single query\n"
        "  2. Batch — Phase 1 (local SMBs, highest conversion)\n"
        "  3. Batch — Phase 2 (agencies, medium term)\n"
        "  4. Show credit history\n"
        "Choice (1/2/3/4): "
    ).strip()

    # ── 1. Single query ───────────────────────────────────────────
    if choice == "1":
        niche    = input("Niche (e.g., real estate broker): ").strip()
        location = input("Location (e.g., Delhi): ").strip()
        mode     = input("Mode — quality / volume [quality]: ").strip() or "quality"

        estimate_credits([(niche, location)])
        confirm  = input("Proceed? (y/n): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

        df    = run_single(niche, location, mode)
        fname = f"{niche}_{location}_{datetime.now():%Y%m%d_%H%M}.xlsx".replace(" ", "_")
        save_results(df, fname)

    # ── 2. Phase 1 — Local SMBs ───────────────────────────────────
    elif choice == "2":
        # Expanded Phase 1 — local SMBs across 6 cities
        # With threshold fix (quality=3), expect ~18-22 leads per pair
        # 28 pairs × ~18 avg = ~500 raw → ~320-400 after dedup
        PHASE_1 = [
            # ── Real estate (highest intent to pay) ──────────────────
            ("real estate broker",   "Delhi"),
            ("real estate broker",   "Noida"),
            ("real estate broker",   "Gurgaon"),
            ("real estate broker",   "Lucknow"),
            ("real estate broker",   "Jaipur"),
            ("property dealer",      "Delhi"),
            ("property dealer",      "Noida"),
            ("property dealer",      "Lucknow"),

            # ── Interior designers ────────────────────────────────────
            ("interior designer",    "Delhi"),
            ("interior designer",    "Noida"),
            ("interior designer",    "Gurgaon"),
            ("interior designer",    "Lucknow"),
            ("interior designer",    "Chandigarh"),

            # ── Wedding planners ──────────────────────────────────────
            ("wedding planner",      "Delhi"),
            ("wedding planner",      "Noida"),
            ("wedding planner",      "Lucknow"),
            ("wedding planner",      "Jaipur"),

            # ── Recruitment (operational pain, pays for automation) ───
            ("recruitment agency",   "Delhi"),
            ("recruitment agency",   "Noida"),
            ("recruitment agency",   "Lucknow"),

            # ── Coaching institutes ───────────────────────────────────
            ("coaching institute",   "Delhi"),
            ("coaching institute",   "Lucknow"),
            ("coaching institute",   "Chandigarh"),

            # ── CA firms (professional services, pay for tools) ───────
            ("CA firm",              "Delhi"),
            ("CA firm",              "Lucknow"),
            ("CA firm",              "Jaipur"),
            ("CA firm",              "Chandigarh"),
        ]
        # Target: ~300-400 usable leads with quality threshold=3
        # Serper cost: ~28 pairs × 6 = 168 base + ~100 enrichment = ~270 credits

        estimate_credits(PHASE_1)
        confirm = input("Proceed? (y/n): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

        df    = run_batch(PHASE_1, mode="quality")
        fname = f"PHASE1_LOCAL_{datetime.now():%Y%m%d_%H%M}.xlsx"
        save_results(df, fname)
        _print_summary(df)

    # ── 3. Phase 2 — Agencies (run AFTER Phase 1 outreach results) ─
    elif choice == "3":
        print("\n⚠️  Run Phase 1 outreach FIRST. Only run Phase 2 after getting")
        print("   at least 5 replies from Phase 1. Market feedback first.")
        confirm = input("Are you sure you want to run Phase 2 now? (y/n): ").strip().lower()
        if confirm != "y":
            print("Good call. Go do outreach first.")
            return

        PHASE_2 = [
            ("lead generation agency",   "Delhi"),
            ("lead generation agency",   "Bangalore"),
            ("lead generation agency",   "Mumbai"),
            ("PPC agency",               "Delhi"),
            ("PPC agency",               "Bangalore"),
            ("growth marketing agency",  "Delhi"),
            ("growth marketing agency",  "Mumbai"),
            ("appointment setting",      "Delhi"),
            ("appointment setting",      "Bangalore"),
            ("outbound sales agency",    "Delhi"),
            ("outbound sales agency",    "Bangalore"),
            ("demand generation agency", "Delhi"),
        ]
        # Target: ~120–200 usable leads. Cost: ~100 Serper credits.

        estimate_credits(PHASE_2)
        confirm = input("Proceed? (y/n): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

        df    = run_batch(PHASE_2, mode="quality")
        fname = f"PHASE2_AGENCIES_{datetime.now():%Y%m%d_%H%M}.xlsx"
        save_results(df, fname)
        _print_summary(df)

    # ── 4. Show history ───────────────────────────────────────────
    elif choice == "4":
        print(f"\n📋 QUERY HISTORY ({len(_query_history)} queries run)")
        for q in sorted(_query_history):
            print(f"   ✓ {q}")
        print(f"\n🌐 DOMAINS SEEN: {len(_seen_domains)} unique domains scraped across all runs")


def _print_summary(df: pd.DataFrame) -> None:
    """Print a clean stats summary after any batch."""
    if df.empty:
        print("⚠️  No leads generated.")
        return
    print(f"\n{'─'*50}")
    print(f"  BATCH SUMMARY")
    print(f"{'─'*50}")
    print(f"  Total leads        : {len(df)}")
    print(f"  With real email    : {(df['Primary Email'] != 'No Email').sum()}")
    print(f"  With phone         : {(df['Phone Number'] != 'N/A').sum()}")
    print(f"  With Instagram     : {(df['Instagram'] != 'N/A').sum()}")
    print(f"  Founder found      : {(df['Founder Name'] != 'Not Found').sum()}")
    print(f"  Score ≥ 6 (hot)    : {(df['Score'] >= 6).sum()}  ← contact these first")
    print(f"\n  Top outreach angles:")
    for angle, count in df["Outreach Angle"].value_counts().head(5).items():
        print(f"    {count:3d}x — {angle}")
    print(f"{'─'*50}")
    print(f"\n  NEXT STEP: Filter Score ≥ 5 with real email → start outreach TODAY")
    print(f"  Target: 10 messages/day. No excuses.\n")


if __name__ == "__main__":
    interactive_mode()