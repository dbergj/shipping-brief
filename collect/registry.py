"""Every metric, feed and company the dashboard knows about.

This is the one file to edit when adding or removing a number. `run.py` walks
METRICS and asks the named adapter for each value, so nothing else needs to
change.

`max_age_days` is how many TRADING SESSIONS old a value may be before the page
flags it. Sessions, not calendar days: on a Monday morning the newest crude
print is Friday's, which is one session old and perfectly current, while the
same three-day gap on a Thursday means a day was missed. Counting calendar days
treated those two identically and was why a four-day-old Brent print sat inside
the tolerance and reached the brief unflagged.

It is per-metric because a daily oil price going two sessions without an update
means something broke, whereas a weekly Baltic route assessment is expected to
be most of a week behind.
"""

# --- Prices fetched from Yahoo's chart endpoint (verified working) -----------
# `verified` records whether I confirmed the symbol resolves to the instrument
# the label claims. Yahoo's `GOGL` is a Google leveraged ETF, not Golden Ocean,
# so this column exists to stop that class of silent error.
METRICS = [
    # Energy — the KPI strip and the energy table
    dict(id="brent", label="Brent crude", unit="USD/bbl", group="energy",
         source="yahoo", symbol="BZ=F", kpi=True, max_age_days=1, verified=True,
         blurb="The global benchmark price for crude oil. Most oil cargoes price off it."),
    dict(id="wti", label="WTI crude", unit="USD/bbl", group="energy",
         source="yahoo", symbol="CL=F", kpi=True, max_age_days=1, verified=True,
         blurb="The US crude benchmark. Its gap to Brent drives US export volumes."),
    dict(id="ttf", label="Dutch TTF gas", unit="EUR/MWh", group="gas",
         source="yahoo", symbol="TTF=F", kpi=True, max_age_days=1, verified=True,
         blurb="Europe's benchmark gas price, set in the Netherlands."),
    dict(id="henry_hub", label="Henry Hub gas", unit="USD/mmBtu", group="gas",
         source="yahoo", symbol="NG=F", kpi=True, max_age_days=1, verified=True,
         blurb="The US benchmark gas price, set in Louisiana."),
    dict(id="coal_api2", label="Coal API2 (ARA)", unit="USD/t", group="energy",
         source="yahoo", symbol="MTF=F", max_age_days=2, verified=True,
         blurb="Price of coal delivered into northwest Europe. A dry bulk demand signal."),
    dict(id="gasoil", label="Heating oil / gasoil", unit="USD/gal", group="energy",
         source="yahoo", symbol="HO=F", max_age_days=1, verified=True,
         blurb="A refined product price that tracks what bunker fuel costs."),
    dict(id="gasoline", label="RBOB gasoline", unit="USD/gal", group="energy",
         source="yahoo", symbol="RB=F", max_age_days=1, verified=True,
         blurb="US gasoline price. Strong margins here pull product tankers west."),

    # Freight proxies — tradeable, daily, free. Not the real indices.
    dict(id="bdry", label="Dry bulk ETF (BDRY)", unit="USD", group="proxy",
         source="yahoo", symbol="BDRY", max_age_days=2, verified=True,
         blurb="An ETF holding dry bulk freight futures. A rough daily read on bulker sentiment."),
    dict(id="bwet", label="Tanker ETF (BWET)", unit="USD", group="proxy",
         source="yahoo", symbol="BWET", max_age_days=2, verified=True,
         blurb="An ETF holding tanker freight futures. A rough daily read on tanker sentiment."),

    # Scraped / parsed sources
    # Baltic sub-indices, read out of the daily Reuters report rather than the
    # licensed feed. See collect/sources/dry_rates.py.
    dict(id="bci", label="Baltic Capesize Index", unit="points", group="rates",
         source="dry_rates", max_age_days=2, verified=True,
         blurb="The Capesize component of the Baltic Dry Index. The most volatile part, "
               "and effectively a bet on Chinese steel production."),
    dict(id="bpi", label="Baltic Panamax Index", unit="points", group="rates",
         source="dry_rates", max_age_days=2, verified=True,
         blurb="The Panamax component. Coal and grain rather than iron ore, so it "
               "follows harvests and power demand more than steel."),
    dict(id="bsi", label="Baltic Supramax Index", unit="points", group="rates",
         source="dry_rates", max_age_days=2, verified=True,
         blurb="The Supramax component. These ships have their own cranes and serve "
               "smaller ports, so it reflects broad trade rather than one commodity."),
    dict(id="bdi", label="Baltic Dry Index", unit="points", group="rates",
         source="bdi", kpi=True, max_age_days=2, verified=True,
         blurb="The Baltic Exchange's headline dry bulk freight index. Published late "
               "afternoon London time, so a morning brief shows the previous session."),
    dict(id="vlsfo_sing", label="VLSFO Singapore", unit="USD/t", group="bunkers",
         source="bunkers", port="Singapore", kpi=True, max_age_days=1, verified=True,
         blurb="Price of the main low-sulphur marine fuel at the world's biggest bunker port."),
    dict(id="vlsfo_rott", label="VLSFO Rotterdam", unit="USD/t", group="bunkers",
         source="bunkers", port="Rotterdam", max_age_days=1, verified=True,
         blurb="The same fuel in Europe's biggest bunker port."),
    dict(id="vlsfo_fuj", label="VLSFO Fujairah", unit="USD/t", group="bunkers",
         source="bunkers", port="Fujairah", max_age_days=1, verified=True,
         blurb="The Middle East bunker hub, where most VLCC voyages refuel."),
    dict(id="vlsfo_hou", label="VLSFO Houston", unit="USD/t", group="bunkers",
         source="bunkers", port="Houston", max_age_days=1, verified=True,
         blurb="The US Gulf bunker price."),
]

# Yahoo refuses datacenter IPs — confirmed over five consecutive CI runs — so
# every Yahoo-sourced metric needs an answer for what happens when it is absent.
# `fallback` names the key that restores it; `no_free_source` marks the ones
# that no free provider publishes at all, which the page explains rather than
# showing as a permanent failure.
FALLBACK_NOTES = {
    # Brent and WTI come from the daily oil report's prose, which outranks EIA:
    # EIA is official but publishes spot three to five days in arrears, and a
    # morning brief needs the level the market last traded at. See
    # sources/oil_prices.py. EIA and FRED remain behind it as fallbacks.
    "brent":     {"fallback": "read from the daily oil report (EIA/FRED behind it)"},
    "wti":       {"fallback": "read from the daily oil report (EIA/FRED behind it)"},
    "henry_hub": {"fallback": "FRED_API_KEY or EIA_API_KEY"},
    # TTF now comes from the daily gas report's prose; see sources/gas_prices.py.
    "ttf": {"fallback": "read from the daily European gas report"},
    "coal_api2": {"no_free_source":
            "API2 coal is an Argus/McCloskey assessment and is not published free."},
    "gasoil": {"no_free_source":
            "Refined product futures are exchange-licensed; no free daily source."},
    "gasoline": {"no_free_source":
            "Refined product futures are exchange-licensed; no free daily source."},
    "bdry": {"no_free_source":
            "ETF quotes need an equity data provider; none is free from a server."},
    "bwet": {"no_free_source":
            "ETF quotes need an equity data provider; none is free from a server."},
}

for _m in METRICS:
    _m.update(FALLBACK_NOTES.get(_m["id"], {}))

# --- Derived metrics -------------------------------------------------------
# Computed from the metrics above rather than fetched. Plain arithmetic, so
# there is no extra source to break.
DERIVED = [
    dict(id="refining_margin", label="Diesel crack vs Brent", unit="USD/bbl", group="energy",
         kind="crack", inputs=["gasoil", "brent"], max_age_days=1,
         blurb="Roughly what a refinery makes turning a barrel of crude into diesel. "
               "Computed as the gasoil price converted to barrels minus Brent. Fat "
               "margins make refineries run hard, which creates product tanker cargo."),
    dict(id="brent_wti_spread", label="Brent–WTI spread", unit="USD/bbl", group="energy",
         kind="spread", inputs=["brent", "wti"], max_age_days=1,
         blurb="How much more Brent costs than WTI. A wide gap makes exporting US crude "
               "profitable, which means more long-haul cargoes and stronger tanker demand."),
]

GALLONS_PER_BARREL = 42

# --- Freight route rates ----------------------------------------------------
# Filled from the Baltic Exchange's weekly market commentary and Reuters' daily
# dry bulk report, both republished as plain text by Hellenic. The underlying
# assessments are licensed, but the prose quoting them is public — see
# collect/sources/route_rates.py and collect/sources/dry_rates.py.
#
# A row with no number still carries its label and explanation: knowing what a
# VLCC on TD3C is has teaching value with or without today's figure.
RATE_ROWS = [
    dict(id="vlcc_tce", label="VLCC (TD3C, AG→China)", unit="USD/day", segment="tanker",
         cadence="weekly",
         blurb="Daily earnings of a 2m-barrel crude tanker on the Arabian Gulf to China "
               "run. The most quoted tanker rate in the world."),
    dict(id="suezmax_tce", label="Suezmax (TD20, W.Africa→UKC)", unit="USD/day", segment="tanker",
         cadence="weekly",
         blurb="Daily earnings of a 1m-barrel crude tanker from West Africa to Europe."),
    dict(id="aframax_tce", label="Aframax (TD7, N.Sea→UKC)", unit="USD/day", segment="tanker",
         cadence="weekly",
         blurb="Daily earnings of a 600k-barrel crude tanker on a short North Sea haul — "
               "the route closest to Norwegian crude exports."),
    dict(id="lr2_tce", label="LR2 (TC1, AG→Japan)", unit="USD/day", segment="tanker",
         cadence="weekly",
         blurb="Daily earnings of a large product tanker carrying clean refined products."),
    dict(id="mr_tc2_tce", label="MR (TC2, Rotterdam→NY)", unit="USD/day", segment="tanker",
         cadence="weekly",
         blurb="Daily earnings of a mid-size product tanker on the transatlantic gasoline run."),
    dict(id="mr_tc14_tce", label="MR (TC14, US Gulf→UKC)", unit="USD/day", segment="tanker",
         cadence="weekly",
         blurb="Daily earnings of a mid-size product tanker carrying clean products east "
               "across the Atlantic."),

    dict(id="cape_5tc", label="Capesize 5TC", unit="USD/day", segment="dry", cadence="daily",
         blurb="Average daily earnings of a 180,000t bulker across five iron ore and coal "
               "routes."),
    dict(id="panamax_4tc", label="Panamax 4TC", unit="USD/day", segment="dry", cadence="daily",
         blurb="Average daily earnings of an 82,000t bulker, mostly coal and grain."),
    dict(id="supramax_10tc", label="Supramax 10TC", unit="USD/day", segment="dry", cadence="daily",
         blurb="Average daily earnings of a 58,000t geared bulker across ten routes."),
    dict(id="handysize_7tc", label="Handysize 7TC", unit="USD/day", segment="dry", cadence="daily",
         blurb="Average daily earnings of a 38,000t bulker, the smallest Baltic-assessed class."),

    dict(id="lng_blng1", label="LNG (BLNG1, Australia→Japan)", unit="USD/day", segment="gas",
         cadence="weekly",
         blurb="Spot charter rate for a standard LNG carrier on the Pacific route."),
    dict(id="lng_blng2", label="LNG (BLNG2, US Gulf→Continent)", unit="USD/day", segment="gas",
         cadence="weekly",
         blurb="The Atlantic LNG route. US cargoes to Europe are the short haul; compare "
               "with BLNG3 to see which way cargoes are being pulled."),
    dict(id="lng_blng3", label="LNG (BLNG3, US Gulf→Japan)", unit="USD/day", segment="gas",
         cadence="weekly",
         blurb="The long-haul LNG route. It pays more than BLNG2 because the voyage is far "
               "longer, so the gap between them tracks the TTF–JKM arbitrage."),
    dict(id="vlgc_baltic", label="VLGC (BLPG1, Ras Tanura→Chiba)", unit="USD/day", segment="gas",
         cadence="weekly",
         blurb="Daily earnings of a very large gas carrier moving LPG from the Gulf to Japan."),
]

# Kept as the old name so nothing downstream has to change.
MANUAL_RATES = RATE_ROWS

# --- News sources -----------------------------------------------------------
# `cap` is how many items each feed may contribute to a day's brief. Without it
# Hellenic alone supplies roughly half the page simply because it publishes most
# often, which crowds out everything else.
FEEDS = [
    dict(name="gCaptain", url="https://gcaptain.com/feed/", kind="rss", cap=6),
    dict(name="Splash247", url="https://splash247.com/feed/", kind="rss", cap=6),
    dict(name="Hellenic Shipping News", url="https://www.hellenicshippingnews.com/feed/", kind="rss", cap=5),
    # Maritime Executive serves Atom (<entry>), not RSS (<item>).
    dict(name="Maritime Executive", url="https://maritime-executive.com/articles.rss", kind="atom", cap=5),
    dict(name="Seatrade Maritime", url="https://www.seatrade-maritime.com/rss.xml", kind="rss", cap=5),
    dict(name="MarineLink", url="https://www.marinelink.com/news/rss", kind="rss", cap=4),
    dict(name="LNG Prime", url="https://lngprime.com/feed/", kind="rss", cap=4),
    dict(name="Bunkerspot", url="https://www.bunkerspot.com/rss", kind="rss", cap=4),
]

# Publications with a Norwegian or Nordic desk that have NO public feed. Kept as
# documentation so nobody re-tests them hoping for a different answer:
# TradeWinds, Upstream and Fiskeribladet (all NHST), ShippingWatch, Finansavisen
# and Dagens Næringsliv are subscription products whose /rss paths serve the
# site's HTML shell. Skipsrevyen, Sysla and maritime.no publish no feed at all.
# E24 does publish one, but its terms of use explicitly forbid supplying the
# content to large language models, which this project's daily brief would do.
# The strongest free Norwegian signal we have is the Oslo Børs filings above.
NO_PUBLIC_FEED = {
    "TradeWinds": "subscription only (NHST); no public RSS",
    "Upstream": "subscription only (NHST); no public RSS",
    "ShippingWatch": "subscription only; /rss serves the site shell",
    "E24": "has RSS, but its terms forbid LLM processing of the content",
    "Skipsrevyen / Sysla / maritime.no": "no feed published",
}

# Terms that mark a story as Nordic-relevant, so the brief can surface the
# Norwegian angle even though the sources are international.
NORDIC_TERMS = [
    "norway", "norwegian", "norge", "norsk", "oslo", "bergen", "stavanger",
    "haugesund", "ålesund", "aalesund", "kristiansand", "trondheim",
    "nordic", "scandinavia", "fredriksen", "høegh", "hoegh", "wallenius",
    "wilhelmsen", "klaveness", "odfjell", "stolt", "grieg", "solstad",
    "knutsen", "ugland", "equinor", "dnv", "kongsberg", "nhst", "tradewinds",
    "euronext oslo", "oslo børs", "oslo bors", "nok ", "nkr",
    # Nordic neighbours whose shipping is read closely from Oslo
    "denmark", "danish", "copenhagen", "maersk", "torm", "norden", "dfds",
    "sweden", "swedish", "stena", "gothenburg", "finland", "finnish",
]

OSLO_NEWS_URL = "https://api3.oslo.oslobors.no/v1/newsreader/list?category=&issuer=&messageTitle="

# Oslo Borse tickers whose announcements we keep, verified against the exchange's
# own issuer directory (POST /v1/newsreader/issuers) rather than guessed.
#
# Two things that directory taught us, both of which would have been silent bugs:
#  - "ADSC" and "COOL" are not Oslo signs at all; ADS Maritime is "ADS".
#  - Golden Ocean (GOGL), Belships, Flex LNG and Avance Gas are flagged inactive
#    on Oslo — GOGL merged into CMB.TECH — so they publish nothing here. They
#    stay in content/companies.json for teaching, and the US-listed ones are
#    picked up via SEC below, but expecting Oslo news from them is a mistake.
#
# issuerId is resolved live each run from that directory, so nothing here rots.
OSLO_TICKERS = {
    # Crude and product tankers
    "FRO": "Frontline", "HAFNI": "Hafnia", "OET": "Okeanis Eco Tankers",
    "STST": "Stainless Tankers", "CAPT": "Capital Tankers", "OMC": "OMC Tankers",
    "VLCC": "Volare Shipping", "ADS": "ADS Maritime Holding",
    "PSHIP": "Performance Shipping",
    # Dry bulk
    "2020": "2020 Bulkers", "HSHP": "Himalaya Shipping", "JIN": "Jinhui Shipping",
    "KCC": "Klaveness Combination Carriers", "DIASH": "Diana Shipping",
    # Gas — LNG and LPG
    "ALNG": "Awilco LNG", "BWLPG": "BW LPG", "GLNG": "Golar LNG",
    "KNLH": "Kinetics LNG Holdings", "NMMP": "Navios Maritime Partners",
    # Chemical, car carrier and container names that dominate Oslo shipping news
    "ODF": "Odfjell", "SNI": "Stolt-Nielsen", "WAWI": "Wallenius Wilhelmsen",
    "HAUTO": "Höegh Autoliners", "MPCC": "MPC Container Ships",
}

# Delisted or inactive on Oslo. Kept only so the collector can say so plainly
# instead of silently returning nothing for a name you expected to see.
OSLO_INACTIVE = {
    "GOGL": "Golden Ocean — merged into CMB.TECH",
    "BELCO": "Belships — taken private",
    "FLNG": "Flex LNG — trades on NYSE, see SEC list",
    "AGAS": "Avance Gas — inactive on Oslo",
}

ISSUERS_URL = "https://api3.oslo.oslobors.no/v1/newsreader/issuers"

# US-listed shipping companies, by SEC CIK. Only 6-K and 8-K are kept — those
# are the "something happened" filings; 10-Ks and ownership forms are noise here.
SEC_COMPANIES = [
    dict(ticker="FRO", name="Frontline", cik="0000913290"),
    dict(ticker="DHT", name="DHT Holdings", cik="0001331284"),
    dict(ticker="STNG", name="Scorpio Tankers", cik="0001483934"),
    dict(ticker="INSW", name="International Seaways", cik="0001679049"),
    dict(ticker="TNK", name="Teekay Tankers", cik="0001419945"),
    dict(ticker="NAT", name="Nordic American Tankers", cik="0001000177"),
    dict(ticker="ASC", name="Ardmore Shipping", cik="0001577437"),
    dict(ticker="SBLK", name="Star Bulk Carriers", cik="0001386716"),
    dict(ticker="GNK", name="Genco Shipping", cik="0001326200"),
    dict(ticker="GASS", name="StealthGas", cik="0001346980"),
    dict(ticker="LPG", name="Dorian LPG", cik="0001596993"),
    dict(ticker="FLNG", name="Flex LNG", cik="0001720116"),
]

SEC_FORMS_KEPT = {"6-K", "8-K"}

# Oslo Borse tags every announcement with a category. Some are real news; others
# are routine compliance filings that would otherwise dominate the company feed —
# a bank crossing a 5% ownership threshold is legally notifiable but is not the
# story of the day. Lower rank sorts first; "routine" rows are kept but grouped
# below the substantive ones and tagged in the page.
OSLO_CATEGORY_RANK = {
    "INSIDE INFORMATION": 0,
    "HALF YEAR FINANCIAL REPORT": 1,
    "ANNUAL FINANCIAL REPORT": 1,
    "ADDITIONAL REGULATED INFORMATION REQUIRED TO BE DISCLOSED UNDER THE LAWS OF A MEMBER STATE": 2,
    "NON-REGULATORY PRESS RELEASES": 2,
    "PROSPECTUS / ADMISSION DOCUMENT": 3,
}
OSLO_ROUTINE_CATEGORIES = {
    "MAJOR SHAREHOLDINGS NOTIFICATION",
    "ADJUSTMENT OF INTEREST RATE",
    "ACQUISITION OR DISPOSAL OF AN ISSUER'S OWN SHARES",
    "MANDATORY NOTIFICATION OF TRADE",
    "MANDATORY NOTIFICATION OF TRADE PRIMARY INSIDERS",
}
OSLO_ROUTINE_RANK = 8

ALL_IDS = ([m["id"] for m in METRICS] + [m["id"] for m in DERIVED]
           + [m["id"] for m in MANUAL_RATES])
