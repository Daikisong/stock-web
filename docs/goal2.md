You are working in the GitHub repository:

Songdaiki/stock-web

TASK:
Build a complete GitHub-readable Korean stock OHLC price atlas from FinanceData/marcap in one shot.

This must let ChatGPT perform E2R historical calibration by reading committed GitHub repo files directly.

Do NOT rely on:
- trycloudflare
- dynamic web API
- raw.githubusercontent.com fetch
- manual text copy-paste
- user uploading txt every time

The final result must be:
- all available KRX symbols from FinanceData/marcap
- all available years from FinanceData/marcap
- compact assistant-readable OHLC shards
- symbol metadata / universe index
- research-pack generator
- smoke research packs
- validation reports
- committed to GitHub
- paths printed so ChatGPT can read them with GitHub repo access

Important:
This is a data infrastructure task, not a UI task.
This is not investment advice.
This is not a live stock scan.
This is not a trading bot.
This is not a scoring model patch.
This is only to prepare collector-generated OHLC artifacts for E2R historical calibration.

Primary source:
https://github.com/FinanceData/marcap

Source assumptions:
FinanceData/marcap contains KRX daily OHLC / market-cap rows from 1995-05-02 to current, with columns such as:
Date, Code, Name, Open, High, Low, Close, Volume, Amount, Changes, ChangeCode, ChagesRatio, Marcap, Stocks, MarketId, Market, Dept.

Critical E2R use requirements:
The downstream E2R historical calibration prompt requires:
- actual_1D_OHLC_available = true
- minimum_forward_window_available = 180_trading_days
- price_source_is_usable_for_backtest = true
- actual Date/Open/High/Low/Close/Volume rows
- high/low based MFE and MAE
- entry_date or next trading day close
- compact D+ path summaries
- no narrative-only calibration
- no weight/gate calibration without OHLC-derived MFE/MAE

Build the repo so ChatGPT can inspect:
1. manifest
2. universe
3. symbol profile
4. specific symbol/year OHLC shard
5. generated research pack JSON/MD

Do not commit the original FinanceData/marcap raw repo as-is.
Do not commit DuckDB cache.
Do not commit one giant CSV.
Do not commit a single huge file.
Instead, convert the data into small deterministic text shards.

GitHub size rules:
- No individual committed file over 50 MiB.
- Absolutely no file over 100 MiB.
- Prefer small CSV/JSON text files.
- Do not use gzip/parquet for assistant-readable files, because ChatGPT repo reading works best with plain text.
- Raw source data and DuckDB cache must stay gitignored.
- If the full plain-text atlas is too large for main, create and push a separate branch named price-atlas-data and put the full atlas there. Do not ask for confirmation. Main branch must still contain manifests, scripts, diagnostics, smoke packs, and pointers to price-atlas-data.
- If the full atlas is small enough, commit everything to main.
- In either case, complete the job in one pass.

Desired final repo structure:

stock-web/
  README.md
  .gitignore

  scripts/
    bootstrap_marcap.py
    build_price_atlas.py
    validate_price_atlas.py
    build_research_pack.py
    probe_price_atlas.py

  atlas/
    README.md
    manifest.json
    source_manifest.json
    schema.json

    universe/
      all_symbols.csv
      current_symbols.csv
      symbol_spans.csv
      name_history.csv
      market_coverage_by_year.csv

    index/
      by_code_prefix/
        000.json
        001.json
        ...
        999.json
      by_market/
        KOSPI.csv
        KOSDAQ.csv
        KONEX.csv
      by_name/
        name_search.csv

    symbol_profiles/
      000/
        000020.json
        ...
      005/
        005930.json
      ...

    ohlcv_min_by_symbol_year/
      000/
        000020/
          1995.csv
          1996.csv
          ...
      005/
        005930/
          1995.csv
          ...
          2026.csv
      ...

    research_packs/
      README.md
      smoke/
        smoke_005930_000660_298040_267260_086520.json
        smoke_005930_000660_298040_267260_086520.md
      custom/
        .gitkeep

    samples/
      chatgpt_bundle.txt
      chatgpt_bundle.json
      sample_005930_2024.csv
      sample_research_pack.json
      sample_research_pack.md

  diagnostics/
    chatgpt_bundle.txt
    chatgpt_bundle.json
    probe_report.txt
    atlas_build_report.md
    atlas_validation_report.md
    atlas_size_report.md

  tests/
    test_price_atlas.py
    test_research_pack.py

Raw data handling:
- Clone FinanceData/marcap into:
  .cache/marcap
- This path must be gitignored.
- If .cache/marcap already exists, git pull --ff-only.
- If git is unavailable but files exist, continue.
- Build optional DuckDB cache at:
  data/cache/marcap.duckdb
- DuckDB cache must be gitignored.
- Do not push .cache, data/raw, data/cache, .duckdb, original marcap yearly csv.gz files, or parquet files.

Add to .gitignore:
.cache/
data/raw/
data/cache/
*.duckdb
*.duckdb.wal
FinanceData_marcap/
marcap/
__pycache__/
.pytest_cache/

Step 1 — bootstrap_marcap.py:
Create scripts/bootstrap_marcap.py.

It must:
- Clone https://github.com/FinanceData/marcap into .cache/marcap if missing.
- Pull latest if present.
- Detect .cache/marcap/data/marcap-*.csv.gz files.
- Print detected years.
- Print latest file.
- Exit nonzero if no marcap data files exist.

Step 2 — Normalize source data:
Create scripts/build_price_atlas.py.

It must:
- Read all .cache/marcap/data/marcap-*.csv.gz files.
- Normalize columns to snake_case:

date
rank
code
name
open
high
low
close
volume
amount
changes
change_code
changes_ratio
marcap
stocks
market_id
market
dept

Rules:
- code must always be zero-padded 6-character string.
- date must be ISO YYYY-MM-DD.
- numeric fields must be numeric.
- preserve Korean names.
- preserve market, market_id, dept.
- preserve stocks.
- price_adjustment_status must be raw_unadjusted_marcap.
- Do not pretend OHLC is adjusted.
- If a numeric field is missing, write empty string in CSV and null in JSON.
- No NaN in JSON.

Step 3 — Build assistant-readable OHLC shards:
For every code and every year with rows, write:

atlas/ohlcv_min_by_symbol_year/{prefix3}/{code}/{year}.csv

Example:
atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv

CSV header:
d,o,h,l,c,v,a,mc,s,m

Meanings:
d = date
o = open
h = high
l = low
c = close
v = volume
a = amount
mc = marcap
s = stocks
m = market

Example:
2024-01-02,78200,79800,78200,79600,17142847,1356958225913,475194690980000,5969782550,KOSPI

Do not repeat company name in OHLC row.
Company names live in symbol_profiles and universe files.

Step 4 — Build symbol profiles:
For every code, write:

atlas/symbol_profiles/{prefix3}/{code}.json

Example:
atlas/symbol_profiles/005/005930.json

Schema:
{
  "code": "005930",
  "current_or_latest_name": "삼성전자",
  "name_history": [
    {"name": "삼성전자", "first_date": "1995-05-02", "last_date": "2026-02-20"}
  ],
  "markets": ["KOSPI"],
  "market_history": [
    {"market": "KOSPI", "first_date": "1995-05-02", "last_date": "2026-02-20"}
  ],
  "first_date": "1995-05-02",
  "last_date": "2026-02-20",
  "trading_day_count": 0,
  "available_years": [1995, 1996, 1997],
  "year_files": [
    "atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv"
  ],
  "latest_close": 0,
  "latest_marcap": 0,
  "latest_market": "KOSPI",
  "status_inferred": "active_like",
  "price_data_source": "FinanceData/marcap",
  "source_repo_url": "https://github.com/FinanceData/marcap",
  "price_adjustment_status": "raw_unadjusted_marcap",
  "caveat": "Raw/unadjusted OHLC from FinanceData/marcap. Corporate actions are not adjusted unless explicitly added later."
}

status_inferred:
- active_like if last_date == global max_date
- inactive_or_delisted_like if last_date < global max_date

Important:
This is inferred only from marcap presence.
Do not claim official delisting status.

Step 5 — Build universe files:

atlas/universe/all_symbols.csv
columns:
code,current_or_latest_name,first_date,last_date,trading_day_count,markets,available_year_count,latest_close,latest_marcap,status_inferred,profile_path

atlas/universe/current_symbols.csv
- rows where last_date == global max_date

atlas/universe/symbol_spans.csv
columns:
code,current_or_latest_name,first_date,last_date,trading_day_count,available_years,profile_path

atlas/universe/name_history.csv
columns:
code,name,first_date,last_date

atlas/universe/market_coverage_by_year.csv
columns:
year,market,row_count,symbol_count,first_date,last_date

Step 6 — Build index files:

atlas/index/by_code_prefix/{prefix3}.json

Example:
atlas/index/by_code_prefix/005.json

Schema:
{
  "prefix": "005",
  "codes": [
    {
      "code": "005930",
      "name": "삼성전자",
      "profile_path": "atlas/symbol_profiles/005/005930.json",
      "available_years": [1995, 1996, 1997],
      "first_date": "1995-05-02",
      "last_date": "2026-02-20",
      "status_inferred": "active_like"
    }
  ]
}

atlas/index/by_market/KOSPI.csv
atlas/index/by_market/KOSDAQ.csv
atlas/index/by_market/KONEX.csv

columns:
code,current_or_latest_name,first_date,last_date,trading_day_count,profile_path

atlas/index/by_name/name_search.csv

columns:
name,code,current_or_latest_name,first_date,last_date,markets,profile_path

Step 7 — Build manifest files:

atlas/manifest.json

Schema:
{
  "atlas_version": "1.0.0",
  "generated_at": "ISO_TIMESTAMP",
  "source_name": "FinanceData/marcap",
  "source_repo_url": "https://github.com/FinanceData/marcap",
  "price_adjustment_status": "raw_unadjusted_marcap",
  "min_date": "1995-05-02",
  "max_date": "YYYY-MM-DD",
  "row_count": 0,
  "symbol_count": 0,
  "active_like_symbol_count": 0,
  "inactive_or_delisted_like_symbol_count": 0,
  "markets": ["KOSPI", "KOSDAQ", "KONEX"],
  "shard_type": "symbol_year_min_csv",
  "ohlcv_shard_root": "atlas/ohlcv_min_by_symbol_year",
  "schema_path": "atlas/schema.json",
  "universe_path": "atlas/universe/all_symbols.csv",
  "research_pack_generator": "scripts/build_research_pack.py",
  "data_branch_if_used": "price-atlas-data",
  "notes": [
    "Raw/unadjusted OHLC. Corporate actions are not adjusted.",
    "Original FinanceData/marcap data files are not committed directly.",
    "Assistant-readable files are compact text shards.",
    "Use generated research packs for E2R calibration whenever possible."
  ]
}

atlas/source_manifest.json
Include:
- source_name
- source_repo_url
- source_commit_hash if available
- source_data_files loaded
- loaded_years
- generated_at
- row_count by year
- min_date
- max_date

atlas/schema.json
Document:
- all shard columns
- all manifest fields
- symbol profile fields
- research pack fields
- MFE/MAE formulas
- calibration_usable rules
- raw_unadjusted caveat

Step 8 — Build research pack generator:
Create scripts/build_research_pack.py.

Purpose:
Generate compact E2R-ready price packs from atlas shards.

Usage:
python scripts/build_research_pack.py \
  --items 298040:2024-01-02,267260:2024-01-02,000660:2023-05-01 \
  --out-json atlas/research_packs/custom/R1_L1_pack.json \
  --out-md atlas/research_packs/custom/R1_L1_pack.md

Arguments:
--items code:trigger_date comma-separated
--entry-mode next_trading_day_close default
--windows 30,90,180,252,504 default
--points 1,2,3,5,10,20,30,60,90,180,252,504 default
--event-window-pre 10 default
--event-window-post 10 default
--include-ohlcv-sample true default
--include-event-window true default

Entry modes:
1. trigger_close
- first trading day on or after trigger_date
- entry_price = close

2. next_trading_day_close
- default
- first find first trading row on or after trigger_date
- then use next trading day close
- entry_date = next trading day
- entry_price = close

3. next_trading_day_open
- first find first trading row on or after trigger_date
- then use next trading day open
- entry_date = next trading day
- entry_price = open

Research pack JSON schema:
{
  "pack_id": "R1_L1_pack",
  "generated_at": "ISO_TIMESTAMP",
  "source_name": "FinanceData/marcap",
  "source_repo_url": "https://github.com/FinanceData/marcap",
  "price_adjustment_status": "raw_unadjusted_marcap",
  "items": [
    {
      "code": "298040",
      "name": "효성중공업",
      "trigger_date": "2024-01-02",
      "entry_mode": "next_trading_day_close",
      "entry_date": "2024-01-03",
      "entry_price": 0,
      "calibration_usable": true,
      "forward_window_trading_days": 504,
      "MFE_30D_pct": 0,
      "MFE_90D_pct": 0,
      "MFE_180D_pct": 0,
      "MFE_1Y_pct": 0,
      "MFE_2Y_pct": 0,
      "MAE_30D_pct": 0,
      "MAE_90D_pct": 0,
      "MAE_180D_pct": 0,
      "MAE_1Y_pct": 0,
      "below_entry_price_flag_30D": false,
      "below_entry_price_flag_90D": false,
      "peak_date": "YYYY-MM-DD",
      "peak_price": 0,
      "drawdown_after_peak_pct": null,
      "path_summary": [
        {
          "label": "D+30",
          "trading_day_offset": 30,
          "date": "YYYY-MM-DD",
          "close_return_pct": 0,
          "high_to_date_return_pct": 0,
          "low_to_date_return_pct": 0,
          "available": true
        }
      ],
      "event_window_pre10_post10": [
        {
          "relative_day_index": -10,
          "date": "YYYY-MM-DD",
          "open": 0,
          "high": 0,
          "low": 0,
          "close": 0,
          "volume": 0,
          "close_return_from_anchor_pct": 0
        }
      ],
      "ohlcv_sample": [
        {
          "date": "YYYY-MM-DD",
          "open": 0,
          "high": 0,
          "low": 0,
          "close": 0,
          "volume": 0
        }
      ],
      "warnings": []
    }
  ]
}

Backtest formulas:
MFE_N_pct = (max high from entry_date through N trading rows / entry_price - 1) * 100
MAE_N_pct = (min low from entry_date through N trading rows / entry_price - 1) * 100
below_entry_price_flag_N = any close < entry_price after entry row within N trading rows
peak_price = max high over observed forward window
peak_date = first date where high == peak_price
drawdown_after_peak_pct = (min low after peak_date / peak_price - 1) * 100
forward_window_trading_days = number of rows after entry_date, excluding entry row

calibration_usable = true only if:
- open/high/low/close/volume are present
- entry row exists
- at least 180 forward trading days are available
- MFE_30D/MFE_90D/MFE_180D computed
- MAE_30D/MAE_90D/MAE_180D computed

Path summary:
D+1, D+2, D+3, D+5, D+10, D+20, D+30, D+60, D+90, D+180, D+252, D+504

For each point:
- close_return_pct
- high_to_date_return_pct
- low_to_date_return_pct
- available

Do not include full 504-day OHLC in the research pack.
Only include compact path summary and small OHLC sample.

Research pack MD:
Create a human-readable Markdown beside JSON.
Include:
- source validation
- item summary table
- trigger backtest table
- path summary table
- event window table
- warnings
- machine-readable JSON block at the bottom
- no investment recommendation language

Step 9 — Generate smoke research pack:
After atlas build, generate:

atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json
atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.md

Items:
005930:2024-01-02
000660:2024-01-02
298040:2024-01-02
267260:2024-01-02
086520:2024-01-02

Expected:
- all five items should have real OHLC rows
- if forward 504 trading days available, report 504
- calibration_usable should be true if 180 forward days exist
- MFE/MAE fields must not be null for 30/90/180D

Step 10 — Diagnostics:
Update/create:

diagnostics/chatgpt_bundle.txt
diagnostics/chatgpt_bundle.json
diagnostics/atlas_build_report.md
diagnostics/atlas_validation_report.md
diagnostics/atlas_size_report.md

chatgpt_bundle.txt must include:
- CHATGPT_MARCAP_BUNDLE
- generated_at
- source_name=FinanceData/marcap
- source_repo_url=https://github.com/FinanceData/marcap
- price_adjustment_status=raw_unadjusted_marcap
- min_date
- max_date
- row_count
- symbol_count
- active_like_symbol_count
- inactive_or_delisted_like_symbol_count
- SELFTEST lines for:
  005930 삼성전자
  000660 SK하이닉스
  298040 효성중공업
  267260 HD현대일렉트릭
  086520 에코프로
- OHLC_SAMPLE lines for 삼성전자 first 5 and last 5 rows of 2024
- TRIGGER_SAMPLE for 삼성전자 trigger_date=2024-01-02
- PATH_SAMPLE for 삼성전자 entry_date after trigger

Selftest line format:
SELFTEST|code|name|ohlcv_2024_count|first_date|last_date|has_ohlcv|calibration_usable|forward_window_trading_days|MFE_30D_pct|MFE_90D_pct|MFE_180D_pct|MAE_30D_pct|MAE_90D_pct|MAE_180D_pct|path_D180_available|status|warnings

OHLC sample line format:
OHLC_SAMPLE|code|date|open|high|low|close|volume|amount|marcap|market

Trigger sample line format:
TRIGGER_SAMPLE|code|trigger_date|entry_mode|entry_date|entry_price|calibration_usable|forward_window_trading_days|MFE_30D_pct|MFE_90D_pct|MFE_180D_pct|MAE_30D_pct|MAE_90D_pct|MAE_180D_pct|peak_date|peak_price|drawdown_after_peak_pct

Path sample line format:
PATH_SAMPLE|code|entry_date|label|date|close_return_pct|high_to_date_return_pct|low_to_date_return_pct|available

Step 11 — Validation:
Create scripts/validate_price_atlas.py.

Validate:
1. atlas/manifest.json exists.
2. source_name == FinanceData/marcap.
3. price_adjustment_status == raw_unadjusted_marcap.
4. Every code is 6 digits.
5. Every OHLC shard is sorted by date ascending.
6. No duplicate date per code/year shard.
7. high >= low.
8. high >= open and high >= close where values are present.
9. low <= open and low <= close where values are present.
10. close is not null.
11. volume is present.
12. all_symbols.csv row count equals symbol profile count.
13. every profile year_files path exists.
14. sample symbols exist:
    005930
    000660
    298040
    267260
    086520
    247540
    035420
    035720
15. sample symbols have 2024 rows if listed in 2024.
16. sample symbols have at least 180 forward trading days from 2024-01-03 if data exists through 2026.
17. no generated JSON contains NaN.
18. no generated file exceeds 50 MiB.
19. no file exceeds 100 MiB.
20. total atlas size is measured and reported.
21. research pack smoke file has 5 items.
22. smoke items have MFE/MAE 30/90/180D when calibration_usable=true.
23. D+180 path summary exists for smoke items when available.
24. inactive_or_delisted_like is explicitly inferred only.

Write report:
diagnostics/atlas_validation_report.md

Step 12 — Tests:
Create pytest tests:

tests/test_price_atlas.py
tests/test_research_pack.py

Minimum tests:
1. manifest exists.
2. 005930 profile exists.
3. 005930 2024 OHLC shard exists.
4. 005930 2024 OHLC shard has d/o/h/l/c/v.
5. code stays zero-padded 6 characters.
6. sample OHLC validation catches high < low.
7. research pack generator computes MFE correctly on a tiny fixture.
8. research pack generator computes MAE correctly on a tiny fixture.
9. next_trading_day_close entry mode works.
10. trigger_close entry mode works.
11. path summary includes D+180.
12. drawdown_after_peak only uses rows after peak.
13. smoke research pack has 5 items.
14. no generated JSON has NaN.
15. no file over 50 MiB in committed atlas.
16. no file over 100 MiB.
17. profile year_files all exist.
18. all_symbols count equals profile count.
19. inactive_or_delisted_like is inferred-only.
20. calibration_usable requires 180 forward trading days.

Run:
python -m pytest

Step 13 — README files:
Update root README.md and create atlas/README.md.

atlas/README.md must explain:
1. What this atlas is.
2. Why original FinanceData/marcap raw files are not committed directly.
3. How ChatGPT should read it:
   - open atlas/manifest.json
   - find code prefix
   - open atlas/index/by_code_prefix/{prefix}.json
   - open atlas/symbol_profiles/{prefix}/{code}.json
   - open atlas/ohlcv_min_by_symbol_year/{prefix}/{code}/{year}.csv
   - prefer generated research pack JSON/MD for calibration
4. Example paths:
   - atlas/manifest.json
   - atlas/index/by_code_prefix/005.json
   - atlas/symbol_profiles/005/005930.json
   - atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv
   - atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json
5. Source caveat:
   - raw_unadjusted_marcap
   - no corporate action adjustment unless added later
6. E2R use:
   - this is a collector-generated OHLC artifact
   - use only calibration_usable=true rows
   - reject rows without 180 forward trading days
   - do not use narrative-only rows for weight changes

Step 14 — Size and branch strategy:
After generating atlas, compute:
- total atlas size
- largest file size
- file count
- row count
- symbol count

If all files are under 50 MiB and total atlas size <= 1.5 GiB:
- Commit full atlas to main.

If total atlas size > 1.5 GiB:
- Commit scripts, manifest, universe, profiles, diagnostics, samples, smoke packs to main.
- Create or update branch price-atlas-data.
- Commit full atlas/ohlcv_min_by_symbol_year to price-atlas-data.
- Main branch manifest must include:
  "full_ohlcv_atlas_branch": "price-atlas-data"
  "full_ohlcv_atlas_committed_to_main": false
- Do not ask user for approval. Do it automatically.
- Print exact branch and paths.

If any individual file > 50 MiB:
- Split more finely until below 50 MiB.

If any file > 100 MiB:
- Fail and fix splitting. Do not push.

Do not use Git LFS unless explicitly asked.
Do not use releases unless branch strategy fails.
Prefer plain Git branches.

Step 15 — Git commit and push:
After build, validation, smoke pack generation, and tests:

git status
git add README.md .gitignore scripts atlas diagnostics tests
git commit -m "Build assistant-readable marcap OHLC atlas"
git push origin main

If price-atlas-data branch is needed:
git switch -c price-atlas-data or git switch price-atlas-data
git add atlas/ohlcv_min_by_symbol_year atlas/manifest.json atlas/schema.json atlas/source_manifest.json
git commit -m "Add full assistant-readable marcap OHLC shards"
git push origin price-atlas-data
git switch main

Step 16 — Final output:
Final response must include exactly these blocks.

ATLAS_BUILD_SUMMARY_BEGIN
main_commit:
data_branch_commit:
source_name:
source_repo_url:
min_date:
max_date:
row_count:
symbol_count:
active_like_symbol_count:
inactive_or_delisted_like_symbol_count:
atlas_total_size_mb:
largest_file_mb:
file_count:
full_ohlcv_atlas_committed_to_main: true/false
full_ohlcv_atlas_branch:
price_adjustment_status: raw_unadjusted_marcap
pytest:
validation:
ATLAS_BUILD_SUMMARY_END

CHATGPT_REPO_PATHS_BEGIN
atlas/manifest.json
atlas/source_manifest.json
atlas/schema.json
atlas/universe/all_symbols.csv
atlas/universe/current_symbols.csv
atlas/index/by_code_prefix/005.json
atlas/symbol_profiles/005/005930.json
atlas/ohlcv_min_by_symbol_year/005/005930/2024.csv
atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.json
atlas/research_packs/smoke/smoke_005930_000660_298040_267260_086520.md
diagnostics/chatgpt_bundle.txt
diagnostics/chatgpt_bundle.json
diagnostics/atlas_build_report.md
diagnostics/atlas_validation_report.md
diagnostics/atlas_size_report.md
CHATGPT_REPO_PATHS_END

HOW_CHATGPT_SHOULD_USE_BEGIN
1. For broad validation, read atlas/manifest.json and diagnostics/chatgpt_bundle.json.
2. For a symbol, use the first 3 digits of the code as prefix.
3. Open atlas/symbol_profiles/{prefix}/{code}.json.
4. Open atlas/ohlcv_min_by_symbol_year/{prefix}/{code}/{year}.csv only when raw OHLC rows are needed.
5. Prefer atlas/research_packs/*.json for automatic E2R calibration because it already contains MFE/MAE/path_summary/event_window.
6. For a new case group, run scripts/build_research_pack.py with CODE:TRIGGER_DATE items, commit the generated JSON/MD, then ChatGPT can read the committed pack directly.
HOW_CHATGPT_SHOULD_USE_END

NEXT_RESEARCH_PACK_COMMAND_BEGIN
python scripts/build_research_pack.py --items CODE:TRIGGER_DATE,CODE:TRIGGER_DATE --out-json atlas/research_packs/custom/PACK_ID.json --out-md atlas/research_packs/custom/PACK_ID.md
NEXT_RESEARCH_PACK_COMMAND_END

Do not stop after scaffolding.
Actually build the atlas.
Actually generate smoke packs.
Actually run validation.
Actually run pytest.
Actually commit and push.