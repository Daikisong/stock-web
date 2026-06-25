# Research Daily Atlas

Date-centered plain-text snapshots for GPT historical market research.

Each trading date has one immutable market snapshot under `snapshots/YYYY/MM/YYYYMMDD.csv`.
The matching access manifest under `access/YYYY/MM/YYYYMMDD.json` tells a researcher which file is safe before prediction sealing and which file is the outcome file.

Example for trade date `2026-06-22`:

- BLIND before sealing: `atlas/research_daily/snapshots/2026/06/20260619.csv`
- POSTMORTEM after sealing: `atlas/research_daily/snapshots/2026/06/20260622.csv`

Default markets are KOSPI, KOSDAQ, and KOSDAQ GLOBAL. KONEX is excluded unless the builder is run with `--include-konex`.

## Build

```bash
python scripts/build_research_daily.py --start-date 2016-01-01 --validate
python scripts/build_research_daily.py --incremental --validate
python scripts/validate_research_daily.py --full
```

The canonical GPT files are plain CSV and JSON. No parquet, zip, gzip, or Git LFS is required to read this layer.
