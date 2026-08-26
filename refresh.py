#!/usr/bin/env python3
"""
SEM EMEA Activity Dashboard — Refresh Script
Pulls data from 5 Google Ads accounts via Funnel Gate,
segments by matched geo location (EMEA countries),
splits brand/non-brand, aggregates by week,
and updates the DATA constants in index.html.

╔══════════════════════════════════════════════════════════════════╗
║  LOCKED METRIC DEFINITIONS — DO NOT CHANGE WITHOUT TAL'S OK    ║
║                                                                 ║
║  Hard Signups  = "Hard Signup (MCC)"     ctID 402542787         ║
║  Payers        = "Paying (MCC)"          ctID 241978033         ║
║  Agents Created = "Agent Created (MCC)"  ctID 7638407984        ║
║  VBB ROAS      = value of "VBB - HT prod - offline conversions"║
║                                                                 ║
║  All use metrics.all_conversions (secondary actions).            ║
║  Verified by Tal Herman on 2026-08-16.                          ║
╚══════════════════════════════════════════════════════════════════╝
"""
import json
import subprocess
import sys
import re
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DASHBOARD_HTML = SCRIPT_DIR / "index.html"
FUNNEL_GATE_URL = "http://localhost:9400/execute"

# 5 standard accounts (no Japan agency)
ACCOUNTS = {
    "3746504118": "Main",
    "6629846296": "Verticals",
    "9194503735": "Verticals2",
    "9441310809": "Locals",
    "6073520942": "Brand",
}

START_DATE = "2026-06-01"

# Conversion actions — LOCKED (same as WoW dashboard)
CONV_ACTIONS = {
    "Hard Signup (MCC)": "signups",
    "Paying (MCC)": "payers",
    "Agent Created (MCC)": "agents_created",
    "VBB - HT prod - offline conversions": "vbb",
}

# Campaign name exclusions
CAMPAIGN_EXCLUSIONS = {"crm", "service", "globster", "elevate", "taka"}

# ── EMEA Country Criteria IDs (Google Ads geo_target_country) ──
EMEA_COUNTRIES = {
    # Western Europe
    "2826": "United Kingdom",
    "2372": "Ireland",
    "2250": "France",
    "2528": "Netherlands",
    "2056": "Belgium",
    "2442": "Luxembourg",
    # DACH
    "2276": "Germany",
    "2040": "Austria",
    "2756": "Switzerland",
    # Nordics
    "2752": "Sweden",
    "2578": "Norway",
    "2208": "Denmark",
    "2246": "Finland",
    "2352": "Iceland",
    # Southern Europe
    "2724": "Spain",
    "2620": "Portugal",
    "2380": "Italy",
    "2300": "Greece",
    "2470": "Malta",
    "2196": "Cyprus",
    # CEE (Central & Eastern Europe)
    "2616": "Poland",
    "2203": "Czech Republic",
    "2348": "Hungary",
    "2642": "Romania",
    "2100": "Bulgaria",
    "2703": "Slovakia",
    "2191": "Croatia",
    "2705": "Slovenia",
    "2688": "Serbia",
    "2233": "Estonia",
    "2428": "Latvia",
    "2440": "Lithuania",
    "2804": "Ukraine",
    # MENA (Middle East & North Africa)
    "2784": "United Arab Emirates",
    "2682": "Saudi Arabia",
    "2376": "Israel",
    "2818": "Egypt",
    "2504": "Morocco",
    "2792": "Turkey",
    "2634": "Qatar",
    "2414": "Kuwait",
    "2048": "Bahrain",
    "2512": "Oman",
    "2400": "Jordan",
    "2422": "Lebanon",
    "2368": "Iraq",
    "2788": "Tunisia",
    "2012": "Algeria",
    "2434": "Libya",
    # Sub-Saharan Africa
    "2710": "South Africa",
    "2566": "Nigeria",
    "2404": "Kenya",
    "2288": "Ghana",
    "2834": "Tanzania",
    "2800": "Uganda",
    "2231": "Ethiopia",
    "2646": "Rwanda",
    # Other
    "2643": "Russia",
}

# ── Geo Groups for comparison ──
GEO_GROUPS = {
    "DACH": {"Germany", "Austria", "Switzerland"},
    "Nordics": {"Sweden", "Norway", "Denmark", "Finland", "Iceland"},
    "Benelux": {"Netherlands", "Belgium", "Luxembourg"},
    "UK & Ireland": {"United Kingdom", "Ireland"},
    "Southern Europe": {"Spain", "Portugal", "Italy", "Greece", "Malta", "Cyprus"},
    "CEE": {"Poland", "Czech Republic", "Hungary", "Romania", "Bulgaria", "Slovakia",
            "Croatia", "Slovenia", "Serbia", "Estonia", "Latvia", "Lithuania", "Ukraine"},
    "MENA": {"United Arab Emirates", "Saudi Arabia", "Israel", "Egypt", "Morocco",
             "Turkey", "Qatar", "Kuwait", "Bahrain", "Oman", "Jordan", "Lebanon",
             "Iraq", "Tunisia", "Algeria", "Libya"},
    "Sub-Saharan Africa": {"South Africa", "Nigeria", "Kenya", "Ghana", "Tanzania",
                           "Uganda", "Ethiopia", "Rwanda"},
    "France": {"France"},
    "Russia": {"Russia"},
}


def is_brand_campaign(campaign_name: str, account_id: str) -> bool:
    """Check if a campaign is brand (vs non-brand)."""
    if account_id == "6073520942":
        return True
    base_name = campaign_name.split(" ")[0] if " " in campaign_name else campaign_name
    parts_lower = [p.lower() for p in base_name.split("-")]
    return any(p == "brand" or p.startswith("brand_") or p == "brands_t" for p in parts_lower)


def should_exclude(campaign_name: str) -> bool:
    """Check if campaign should be excluded."""
    base_name = campaign_name.split(" ")[0] if " " in campaign_name else campaign_name
    parts_lower = [p.lower() for p in base_name.split("-")]
    if any(excl in p for p in parts_lower for excl in CAMPAIGN_EXCLUSIONS):
        return True
    if any(p in ("lead_management", "account_management", "lead_agent") for p in parts_lower):
        return True
    return False


def week_start_wed(date_str: str) -> str:
    """Convert YYYY-MM-DD to the Wednesday that starts its Wed-Tue week."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    wednesday = d - timedelta(days=(d.weekday() - 2) % 7)
    return wednesday.strftime("%Y-%m-%d")


def run_gaql(customer_id: str, query: str) -> list:
    """Execute a GAQL query via Funnel Gate."""
    payload = {
        "requester": "nymeria",
        "action": "gaql_query",
        "platform": "google_ads",
        "scope": {
            "customer_id": customer_id,
            "query": query,
        },
        "trail": {"reasoning": "EMEA dashboard refresh"},
        "skill_name": "emea-dashboard-refresh",
        "initiator": {"name": "Nymeria", "context": "EMEA dashboard refresh"},
    }
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", FUNNEL_GATE_URL,
         "-H", "Content-Type: application/json",
         "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=300
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  ERROR for {customer_id}: Failed to parse response: {result.stdout[:200]}", file=sys.stderr)
        return []
    if "error" in data:
        print(f"  ERROR for {customer_id}: {data['error']}", file=sys.stderr)
        return []
    return data.get("result", {}).get("results", [])


def pull_data():
    """Pull performance and conversion data segmented by geo."""
    today = datetime.now()
    end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"Pulling EMEA data from {START_DATE} to {end_date}")

    empty_metrics = lambda: {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
    data = {
        "brand": defaultdict(lambda: defaultdict(empty_metrics)),
        "nonbrand": defaultdict(lambda: defaultdict(empty_metrics)),
    }

    geo_ids = ", ".join(EMEA_COUNTRIES.keys())

    for acct_id, acct_name in ACCOUNTS.items():
        print(f"\n=== {acct_name} ({acct_id}) ===")

        perf_query = (
            f"SELECT campaign.name, campaign.advertising_channel_type, segments.date, geographic_view.country_criterion_id, "
            f"metrics.cost_micros, metrics.impressions, metrics.clicks "
            f"FROM geographic_view "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH' "
            f"AND geographic_view.country_criterion_id IN ({geo_ids})"
        )
        print(f"  Pulling geo performance metrics...")
        perf_rows = run_gaql(acct_id, perf_query)
        print(f"  Got {len(perf_rows)} geo performance rows")

        conv_names = ", ".join(f"'{name}'" for name in CONV_ACTIONS.keys())
        conv_query = (
            f"SELECT campaign.name, campaign.advertising_channel_type, segments.date, geographic_view.country_criterion_id, "
            f"segments.conversion_action_name, metrics.all_conversions, metrics.all_conversions_value "
            f"FROM geographic_view "
            f"WHERE segments.date BETWEEN '{START_DATE}' AND '{end_date}' "
            f"AND campaign.advertising_channel_type = 'SEARCH' "
            f"AND geographic_view.country_criterion_id IN ({geo_ids}) "
            f"AND segments.conversion_action_name IN ({conv_names})"
        )
        print(f"  Pulling geo conversion metrics...")
        conv_rows = run_gaql(acct_id, conv_query)
        print(f"  Got {len(conv_rows)} geo conversion rows")

        for row in perf_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date = row.get("segments", {}).get("date", "")
            geo_view = row.get("geographicView", {})
            criteria_id = str(geo_view.get("countryCriterionId", ""))
            metrics = row.get("metrics", {})

            if should_exclude(camp_name):
                continue
            if criteria_id not in EMEA_COUNTRIES:
                continue

            country = EMEA_COUNTRIES[criteria_id]
            brand_key = "brand" if is_brand_campaign(camp_name, acct_id) else "nonbrand"
            week = week_start_wed(date)

            data[brand_key][country][week]["spend"] += float(metrics.get("costMicros", 0)) / 1_000_000
            data[brand_key][country][week]["imp"] += int(metrics.get("impressions", 0))
            data[brand_key][country][week]["clicks"] += int(metrics.get("clicks", 0))

        for row in conv_rows:
            camp_name = row.get("campaign", {}).get("name", "")
            date = row.get("segments", {}).get("date", "")
            geo_view = row.get("geographicView", {})
            criteria_id = str(geo_view.get("countryCriterionId", ""))
            conv_name = row.get("segments", {}).get("conversionActionName", "")
            metrics = row.get("metrics", {})

            if should_exclude(camp_name):
                continue
            if criteria_id not in EMEA_COUNTRIES:
                continue

            country = EMEA_COUNTRIES[criteria_id]
            brand_key = "brand" if is_brand_campaign(camp_name, acct_id) else "nonbrand"
            week = week_start_wed(date)

            conversions = float(metrics.get("allConversions", 0))
            conv_value = float(metrics.get("allConversionsValue", 0))

            metric_key = CONV_ACTIONS.get(conv_name)
            if metric_key == "vbb":
                data[brand_key][country][week]["vbb_value"] += conv_value
            elif metric_key:
                data[brand_key][country][week][metric_key] += conversions

    return data


def compute_aggregates(data: dict) -> dict:
    """Compute 'All EMEA' and geo group aggregates."""
    for brand_key in ("brand", "nonbrand"):
        geo_data = data[brand_key]
        all_weeks = set()
        for weeks in geo_data.values():
            all_weeks.update(weeks.keys())

        # All EMEA aggregate
        for week in sorted(all_weeks):
            totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
            for geo_name, weeks in geo_data.items():
                if geo_name == "All EMEA" or geo_name in GEO_GROUPS:
                    continue
                if week in weeks:
                    for k in totals:
                        totals[k] += weeks[week][k]
            geo_data["All EMEA"][week] = totals

        # Geo group aggregates
        for group_name, group_countries in GEO_GROUPS.items():
            for week in sorted(all_weeks):
                totals = {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0}
                for country in group_countries:
                    if country in geo_data and week in geo_data[country]:
                        for k in totals:
                            totals[k] += geo_data[country][week][k]
                if any(v > 0 for v in totals.values()):
                    geo_data[group_name][week] = totals

    return data


def format_data_for_html(data: dict) -> tuple[str, str]:
    """Format brand and nonbrand data as JSON strings."""
    all_weeks = set()
    for brand_key in ("brand", "nonbrand"):
        for weeks in data[brand_key].values():
            all_weeks.update(weeks.keys())
    sorted_weeks = sorted(all_weeks)

    results = {}
    for brand_key in ("brand", "nonbrand"):
        output = {}
        for geo_name in sorted(data[brand_key].keys()):
            weeks_data = data[brand_key][geo_name]
            rows = []
            for week in sorted_weeks:
                d = weeks_data.get(week, {"spend": 0, "imp": 0, "clicks": 0, "signups": 0, "payers": 0, "vbb_value": 0, "agents_created": 0})
                rows.append({
                    "spend": round(d["spend"], 2),
                    "imp": d["imp"],
                    "clicks": d["clicks"],
                    "signups": round(d["signups"], 1),
                    "payers": int(round(d["payers"])),
                    "vbb_value": round(d["vbb_value"], 2),
                    "agents_created": round(d["agents_created"], 1),
                    "week": week,
                })
            output[geo_name] = rows
        results[brand_key] = json.dumps(output, separators=(",", ":"))

    return results["brand"], results["nonbrand"]


def update_html(brand_json: str, nonbrand_json: str):
    """Replace the DATA constants in index.html."""
    content = DASHBOARD_HTML.read_text()

    for var_name, json_str in [("DATA_BRAND", brand_json), ("DATA_NONBRAND", nonbrand_json)]:
        pattern = rf'const {var_name} = \{{.*?\}};'
        replacement = f'const {var_name} = {json_str};'
        content, count = re.subn(pattern, replacement, content, count=1, flags=re.DOTALL)
        if count == 0:
            print(f"ERROR: Could not find 'const {var_name} = {{...}};' in index.html", file=sys.stderr)
            sys.exit(1)

    DASHBOARD_HTML.write_text(content)
    print(f"\nUpdated index.html ({len(brand_json):,} + {len(nonbrand_json):,} chars)")


def git_commit_push():
    """Commit and push changes."""
    import subprocess as sp
    today = datetime.now().strftime("%Y-%m-%d")
    sp.run(["git", "add", "index.html"], cwd=SCRIPT_DIR, check=True)
    result = sp.run(["git", "diff", "--cached", "--quiet"], cwd=SCRIPT_DIR)
    if result.returncode == 0:
        print("No changes to commit")
        return False
    sp.run(["git", "commit", "-m", f"EMEA dashboard auto-refresh {today}"], cwd=SCRIPT_DIR, check=True)
    sp.run(["git", "push"], cwd=SCRIPT_DIR, check=True)
    print(f"Committed and pushed: EMEA dashboard refresh {today}")
    return True


def main():
    print("🌍 SEM EMEA Activity Dashboard — Refresh")
    print("=" * 50)

    data = pull_data()
    data = compute_aggregates(data)

    for brand_key in ("brand", "nonbrand"):
        geo_data = data[brand_key]
        print(f"\n📊 {brand_key.upper()}: {len(geo_data)} geos")
        for name in sorted(geo_data.keys()):
            weeks = geo_data[name]
            total_spend = sum(w["spend"] for w in weeks.values())
            if total_spend > 0:
                print(f"  {name}: {len(weeks)} weeks, ${total_spend:,.0f} total spend")

    brand_json, nonbrand_json = format_data_for_html(data)
    update_html(brand_json, nonbrand_json)
    pushed = git_commit_push()

    if pushed:
        print("\n✅ EMEA Dashboard refreshed and deployed!")
    else:
        print("\n✅ No new data to deploy")


if __name__ == "__main__":
    main()
