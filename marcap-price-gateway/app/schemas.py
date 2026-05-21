from __future__ import annotations

PRICE_DATA_SOURCE = "FinanceData/marcap"
SOURCE_REPO_URL = "https://github.com/FinanceData/marcap"
PRICE_ADJUSTMENT_STATUS = "raw_unadjusted_marcap"
CAVEAT = (
    "FinanceData/marcap OHLC appears raw/unadjusted in this gateway. "
    "Corporate actions are not adjusted unless explicitly added later."
)


def source_metadata() -> dict[str, str]:
    return {
        "price_data_source": PRICE_DATA_SOURCE,
        "source_repo_url": SOURCE_REPO_URL,
        "price_adjustment_status": PRICE_ADJUSTMENT_STATUS,
        "caveat": CAVEAT,
    }


def source_notes() -> list[str]:
    return [
        f"price_data_source={PRICE_DATA_SOURCE}",
        f"source_repo_url={SOURCE_REPO_URL}",
        f"price_adjustment_status={PRICE_ADJUSTMENT_STATUS}",
        CAVEAT,
        "This gateway is a read-only research data access layer and does not provide investment advice.",
    ]
