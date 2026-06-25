너는 `Daikisong/stock-web` 저장소의 수석 데이터 엔지니어다.

이번 작업은 설계안만 제시하는 작업이 아니다.
현재 저장소를 직접 조사하고, 실제 코드·데이터 산출물·테스트·문서까지 완성하라.

질문을 되묻지 말고 합리적인 기본값으로 끝까지 진행하라.
계획만 작성하고 멈추지 마라.
구현 후 실제로 2016년부터 현재 atlas 최대일인 2026-06-22까지 백필하고,
모든 테스트와 검증을 통과시킨 뒤 결과를 보고하라.

────────────────────────────────────────
0. 프로젝트 배경과 핵심 문제
────────────────────────────────────────

현재 저장소는 FinanceData/marcap을 바탕으로 다음 종목 중심 구조를 제공한다.

```text
atlas/ohlcv_tradable_by_symbol_year/{prefix}/{code}/{year}.csv
atlas/ohlcv_raw_by_symbol_year/{prefix}/{code}/{year}.csv
atlas/symbol_profiles/
atlas/universe/
atlas/corporate_actions/
````

이 구조는 개별 종목 연구에는 적합하지만,
특정 거래일 D의 전 시장 결과를 읽으려면 수천 개 종목 파일을 열어야 한다.

GPT Web 연구 세션에서는 다음 문제가 반복됐다.

```text
- 전 시장 D 결과를 완성하지 못함
- 일부 후보 종목 가격만 읽고 PARTIAL outcome이 됨
- 상한가 전체 census와 주도섹터 breadth 연구가 불완전해짐
- all_symbols 및 symbol_profile의 latest 필드가 BLIND 전에 노출될 위험
- parquet 같은 바이너리 파일을 GPT 환경이 직접 읽지 못함
```

이를 해결하기 위해, 기존 atlas를 유지하면서
GPT가 GitHub Raw URL 한두 개만 다운로드해 안전하게 연구할 수 있는
날짜 중심의 정적 plain-text 접근층을 추가한다.

────────────────────────────────────────

1. 최종 목표
   ────────────────────────────────────────

다음 구조를 구현하라.

```text
atlas/research_daily/
├─ README.md
├─ README_LLM.md
├─ manifest.json
├─ schema.json
├─ trading_calendar.csv
├─ snapshots/
│  └─ YYYY/
│     └─ MM/
│        └─ YYYYMMDD.csv
└─ access/
   └─ YYYY/
      └─ MM/
         └─ YYYYMMDD.json
```

핵심 설계는 다음과 같다.

## 1.1 일별 snapshot 하나만 저장

동일한 전 시장 데이터를 blind/outcome 디렉터리에 두 번 복제하지 않는다.

거래일별 immutable snapshot을 하나만 생성한다.

예:

```text
atlas/research_daily/snapshots/2026/06/20260619.csv
atlas/research_daily/snapshots/2026/06/20260622.csv
```

2026-06-22 거래일 D를 연구할 때:

```text
BLIND 전:
20260619.csv만 읽음

BLIND 봉인 후:
20260622.csv를 읽음
```

즉 이전 실제 거래일 P의 snapshot이 BLIND 시장팩이고,
현재 거래일 D의 snapshot이 POSTMORTEM 결과팩이다.

이 방식은 미래정보 누수를 막으면서도 데이터 중복을 절반으로 줄인다.

## 1.2 날짜별 access manifest

각 연구 거래일 D에는 예측 가능한 경로의 JSON을 만든다.

예:

```text
atlas/research_daily/access/2026/06/20260622.json
```

내용 예시:

```json
{
  "schema_version": "stock_web.research_daily_access.v1",
  "trade_date": "2026-06-22",
  "previous_trade_date": "2026-06-19",
  "next_trade_date": "2026-06-23",
  "blind_snapshot_date": "2026-06-19",
  "blind_snapshot_path": "atlas/research_daily/snapshots/2026/06/20260619.csv",
  "outcome_snapshot_date": "2026-06-22",
  "outcome_snapshot_path": "atlas/research_daily/snapshots/2026/06/20260622.csv",
  "blind_snapshot_sha256": "...",
  "outcome_snapshot_sha256": "...",
  "blind_snapshot_row_count": 0,
  "outcome_snapshot_row_count": 0,
  "blind_max_source_date": "2026-06-19",
  "outcome_max_source_date": "2026-06-22",
  "source_manifest_sha256": "...",
  "build_status": "complete"
}
```

이 JSON에는 가격 숫자를 넣지 않는다.
경로·날짜·해시·행 수·검증상태만 넣는다.

GPT 연구원은 이 JSON을 먼저 읽으면,
어떤 파일을 BLIND 전에 읽고 어떤 파일을 봉인 후 읽어야 하는지
혼동하지 않아야 한다.

────────────────────────────────────────
2. 연구 대상 기간과 시장
────────────────────────────────────────

기본 연구 범위:

```text
start_date = 2016-01-01
end_date = atlas/manifest.json의 max_date
현재 기대 max_date = 2026-06-22
```

달력 날짜가 아니라 실제 거래일만 생성한다.

2016년 첫 연구 거래일의 BLIND 파일을 제공하려면,
그 거래일의 이전 실제 거래일 snapshot도 seed snapshot으로 생성한다.

기본 포함 시장:

```text
KOSPI
KOSDAQ
KOSDAQ GLOBAL
```

KONEX는 기본 연구 대상에서 제외하되 CLI 옵션으로 포함 가능하게 한다.

```text
--include-konex
```

코드는 6자리 문자열로 유지하고 선행 0을 절대 잃지 않는다.

────────────────────────────────────────
3. Snapshot CSV 스키마
────────────────────────────────────────

모든 snapshot은 UTF-8, LF, BOM 없음, plain CSV여야 한다.

gzip, zip, parquet, Git LFS는 canonical GPT용 산출물로 사용하지 않는다.

행 정렬:

```text
code 오름차순
```

컬럼 순서는 아래로 고정한다.

```text
snapshot_date
previous_market_trade_date
code
name
name_resolution_status
name_candidates
market
prev_symbol_trade_date
days_since_prev_symbol_trade
prev_close
open
high
low
close
volume
amount
market_cap
listed_shares
open_gap_pct
high_return_pct
low_return_pct
close_return_pct
turnover_pct
return_3d_pct
return_5d_pct
return_10d_pct
return_20d_pct
amount_rank
turnover_rank
market_cap_rank
high_return_rank
close_return_rank
limit_up_price
upper_limit_touched
upper_limit_closed
upper_limit_released
one_price_upper_limit
upper_limit_label_status
upper_limit_touch_count_5d
upper_limit_close_count_5d
high_return_ge_10_count_5d
high_return_ge_20_count_5d
corporate_action_warning
new_listing_or_no_reference
data_quality_status
max_source_date
```

## 3.1 기본값과 계산 규칙

정수 필드:

```text
prev_close
open
high
low
close
volume
amount
market_cap
listed_shares
limit_up_price
rank 필드
```

퍼센트 필드:

```text
소수점 이하 최대 6자리
과학적 표기법 금지
```

Boolean:

```text
true
false
```

알 수 없는 값:

```text
빈 필드
```

숫자 0과 null을 혼동하지 않는다.

## 3.2 현재 거래일 수익률

```text
open_gap_pct
= (open / prev_close - 1) * 100

high_return_pct
= (high / prev_close - 1) * 100

low_return_pct
= (low / prev_close - 1) * 100

close_return_pct
= (close / prev_close - 1) * 100

turnover_pct
= volume / listed_shares * 100
```

prev_close 또는 listed_shares가 유효하지 않으면 null로 둔다.

## 3.3 후행 수익률

각 종목의 자체 거래 가능 행 기준으로 계산한다.

```text
return_3d_pct
return_5d_pct
return_10d_pct
return_20d_pct
```

예:

```text
return_5d_pct
= snapshot_date 종가 / 5번째 이전 종목 거래일 종가 - 1
```

달력 날짜 기준이 아니라 종목별 tradable row 기준이다.

필요한 과거 행이 부족하면 null이다.

## 3.4 전 시장 순위

해당 snapshot_date의 기본 시장 전체 안에서 계산한다.

```text
amount_rank
turnover_rank
market_cap_rank
high_return_rank
close_return_rank
```

정렬 기준:

```text
값 내림차순
동률이면 code 오름차순
1부터 시작하는 deterministic ordinal rank
```

null 값에는 rank를 주지 않는다.

────────────────────────────────────────
4. 역사적 회사명과 엔티티 안전성
────────────────────────────────────────

절대로 `current_or_latest_name`을 과거 날짜의 회사명으로 무조건 사용하지 마라.

이 저장소에는 이름 이력과 코드 재사용·복수 이름 가능성이 있다.

회사명 결정 우선순위:

```text
1. 원본 FinanceData/marcap의 해당 날짜 Name
2. atlas/universe/name_history.csv에서
   first_date <= snapshot_date <= last_date인 이름
3. 그 외에는 빈 name + 명시적 상태
```

`name_resolution_status` 허용값:

```text
exact_source_row
unique_history_match
ambiguous_history_match
unresolved
```

복수 후보가 있으면 임의로 하나를 선택하지 않는다.

```text
name = 빈 값
name_candidates = 후보명을 | 로 연결
name_resolution_status = ambiguous_history_match
```

미래 날짜에서 처음 등장한 이름을 과거 snapshot에 소급하지 않는다.

────────────────────────────────────────
5. Snapshot 모집단
────────────────────────────────────────

snapshot_date D 파일에는 기본 시장에서 D에 실제 tradable row가 있는
모든 종목을 정확히 한 번 포함한다.

```text
volume > 0
OHLC > 0
기존 tradable shard 기준 충족
```

다음은 포함하지 않는다.

```text
zero-volume raw row
invalid OHLC row
다른 날짜의 latest snapshot
현재 기준 active 목록만으로 재구성한 행
```

결과적으로 각 snapshot은 해당 거래일의
KOSPI·KOSDAQ 전 시장 거래 가능 단면이어야 한다.

신규상장 첫날처럼 이전 종목 거래일이 없는 경우:

```text
prev_symbol_trade_date = 빈 값
prev_close = 빈 값
new_listing_or_no_reference = true
수익률 및 상한가 필드 = 빈 값
upper_limit_label_status = no_reference_price
```

────────────────────────────────────────
6. 상한가 라벨
────────────────────────────────────────

`high_return_pct >= 29.5` 같은 임의 임계치만으로
상한가를 verified 처리하지 마라.

다음 우선순위를 따른다.

```text
1. upstream marcap에 공식 ChangeCode 또는 동등한 명시 정보가 있다면 활용 검토
2. 공식 KRX 가격제한폭과 당시 호가단위에 따라 limit_up_price 계산
3. corporate action·신규상장·기준가격 불명확일은 라벨 차단
```

2016년 이후의 역사적 호가단위 변경도 반영한다.

특히 2023년 전후 호가단위 체계가 달라질 수 있으므로
날짜·시장·가격구간별 규칙을 공식 자료로 검증하고,
하나의 현재 호가단위 표를 전 기간에 적용하지 않는다.

상한가 필드:

```text
limit_up_price

upper_limit_touched
= high == limit_up_price

upper_limit_closed
= close == limit_up_price

upper_limit_released
= high == limit_up_price and close < limit_up_price

one_price_upper_limit
= open == high == low == close == limit_up_price
```

`upper_limit_label_status` 허용값:

```text
verified_normal_day
blocked_corporate_action
blocked_new_listing
blocked_no_reference_price
blocked_ambiguous_reference
unsupported_market_rule
```

검증할 수 없으면 Boolean을 false로 만들지 말고 null로 둔다.

────────────────────────────────────────
7. 기업행위와 데이터 품질
────────────────────────────────────────

기존 파일을 활용한다.

```text
atlas/corporate_actions/corporate_action_candidates.csv
atlas/ohlcv_raw_by_symbol_year
atlas/schema.json
```

snapshot 날짜 또는 기준가격에 영향을 주는 직전 구간에
기업행위 후보가 존재하면:

```text
corporate_action_warning = true
```

해당 날짜의 수익률·상한가 라벨이 신뢰 불가하면:

```text
data_quality_status = blocked_by_corporate_action
upper_limit_label_status = blocked_corporate_action
```

`data_quality_status` 허용값 예:

```text
clean
usable_with_caveat
blocked_by_corporate_action
blocked_no_reference
blocked_invalid_ohlc
blocked_unsupported_market_rule
```

기업행위 원시 OHLC를 수정하거나 보정하지 않는다.

────────────────────────────────────────
8. 최근 상한가·강한 상승 특징
────────────────────────────────────────

snapshot_date까지의 종목 tradable row만 사용한다.

```text
upper_limit_touch_count_5d
upper_limit_close_count_5d
high_return_ge_10_count_5d
high_return_ge_20_count_5d
```

현재 snapshot_date를 포함한 최근 5개 종목 거래일 기준으로 계산한다.

상한가 라벨이 검증되지 않은 날은 상한가 count에 넣지 않는다.

────────────────────────────────────────
9. 거래일 캘린더
────────────────────────────────────────

`atlas/research_daily/trading_calendar.csv`를 생성한다.

컬럼:

```text
trade_date
previous_trade_date
next_trade_date
blind_snapshot_date
blind_snapshot_path
outcome_snapshot_date
outcome_snapshot_path
access_manifest_path
blind_snapshot_sha256
outcome_snapshot_sha256
blind_snapshot_row_count
outcome_snapshot_row_count
blind_snapshot_bytes
outcome_snapshot_bytes
blind_max_source_date
outcome_max_source_date
source_manifest_sha256
build_status
```

거래일 D의 행에서:

```text
blind_snapshot_date = previous_trade_date
outcome_snapshot_date = trade_date
```

반드시 성립해야 한다.

주말·공휴일·비거래일은 행으로 생성하지 않는다.

────────────────────────────────────────
10. Research Daily Manifest와 Schema
────────────────────────────────────────

## 10.1 manifest.json

최소 필드:

```json
{
  "research_daily_version": "1.0.0",
  "generated_at": "",
  "source_atlas_version": "",
  "source_atlas_generated_at": "",
  "source_manifest_sha256": "",
  "source_name": "FinanceData/marcap",
  "price_adjustment_status": "raw_unadjusted_marcap",
  "research_start_date": "2016-01-01",
  "first_research_trade_date": "",
  "seed_snapshot_date": "",
  "max_trade_date": "",
  "markets": ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"],
  "snapshot_count": 0,
  "access_manifest_count": 0,
  "total_snapshot_rows": 0,
  "snapshot_root": "atlas/research_daily/snapshots",
  "access_root": "atlas/research_daily/access",
  "calendar_path": "atlas/research_daily/trading_calendar.csv",
  "schema_path": "atlas/research_daily/schema.json",
  "full_backfill_complete": true,
  "validation_passed": true
}
```

## 10.2 schema.json

다음을 모두 명시한다.

```text
컬럼 순서
자료형
단위
null 정책
수식
rank 방식
상한가 라벨 의미
기업행위 차단 규칙
기본 시장
이름 해석 규칙
```

## 10.3 기존 atlas/manifest.json 연동

기존 필드를 깨뜨리지 않고 다음 객체를 추가한다.

```json
{
  "research_daily": {
    "root": "atlas/research_daily",
    "manifest_path": "atlas/research_daily/manifest.json",
    "calendar_path": "atlas/research_daily/trading_calendar.csv",
    "first_research_trade_date": "",
    "max_trade_date": "",
    "snapshot_count": 0,
    "access_manifest_count": 0,
    "validation_passed": true
  }
}
```

기존 row count·shard root·atlas 의미를 변경하지 않는다.

────────────────────────────────────────
11. GPT용 README
────────────────────────────────────────

`atlas/research_daily/README_LLM.md`를 특히 명확하게 작성한다.

반드시 아래 절차를 포함한다.

```text
거래일 D 연구 절차

1. access/YYYY/MM/YYYYMMDD.json을 연다.
2. BLIND 봉인 전에는 blind_snapshot_path만 다운로드한다.
3. blind_snapshot의 snapshot_date가 previous_trade_date인지 확인한다.
4. blind_snapshot의 max_source_date가 previous_trade_date보다 늦지 않은지 확인한다.
5. 장전 예측을 파일로 저장하고 SHA-256 봉인한다.
6. 그 뒤에만 outcome_snapshot_path를 다운로드한다.
7. outcome snapshot으로 D의 전 시장 상한가·강한 상승·거래대금·테마 breadth를 연구한다.
8. symbol_profiles, all_symbols latest 필드는 역사적 BLIND에 사용하지 않는다.
```

Raw URL 예시:

```text
https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/access/2026/06/20260622.json

https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2026/06/20260619.csv

https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/snapshots/2026/06/20260622.csv
```

누락 파일을 휴장일로 추정하지 말고 trading_calendar를 기준으로 판단하라고 명시한다.

루트 README와 `atlas/README.md`의 “What To Read First”에도
뉴스 연구용으로 `atlas/research_daily/README_LLM.md`를 추가한다.

────────────────────────────────────────
12. 구현 파일
────────────────────────────────────────

최소 다음을 구현한다.

```text
scripts/research_daily_utils.py
scripts/build_research_daily.py
scripts/validate_research_daily.py
tests/test_research_daily.py
```

필요하면 기존 `scripts/atlas_utils.py`의 안전한 공통 함수를 재사용한다.

기존 atlas 생성·연구팩 기능을 깨뜨리지 않는다.

## 12.1 build CLI

```bash
python scripts/build_research_daily.py \
  --start-date 2016-01-01 \
  --end-date 2026-06-22
```

지원 옵션:

```text
--start-date
--end-date
--markets
--include-konex
--overwrite
--incremental
--resume
--validate
```

기본 end-date는 `atlas/manifest.json`의 max_date다.

## 12.2 incremental 갱신

atlas가 새 날짜까지 업데이트되면:

```bash
python scripts/build_research_daily.py --incremental --validate
```

만으로 새 snapshot·access manifest·calendar·manifest를 생성해야 한다.

이미 정상이고 source hash가 같은 파일은 다시 쓰지 않는다.

source 데이터가 바뀐 날짜는 해시를 비교해 재생성한다.

## 12.3 재개 가능성

10년 백필 중 중단될 수 있으므로:

```text
임시 파일
체크포인트
연도·월별 진행상태
원자적 rename
```

을 사용한다.

중간 실패로 완성 CSV가 반쪽 상태로 남으면 안 된다.

────────────────────────────────────────
13. 빌드 성능과 저장소 크기
────────────────────────────────────────

수천 개 Raw URL을 네트워크로 다시 받는 방식으로 구현하지 않는다.
현재 로컬 저장소의 atlas 파일을 직접 읽는다.

효율적인 구현을 선택한다.

예:

```text
종목 shard를 한 번 순회
→ 종목별 후행 특징 계산
→ 날짜별 임시 bucket 기록
→ 날짜별 전 시장 rank 계산
→ 최종 snapshot 원자적 저장
```

또는 로컬 임시 SQLite/DuckDB를 사용할 수 있다.

단, 임시 DB·cache는 Git에 커밋하지 않는다.

출력 크기 원칙:

```text
각 파일 50 MiB 미만 필수
각 snapshot 10 MiB 미만 권장
불필요한 문자열 반복 금지
설명 산문을 CSV에 넣지 않음
plain CSV 가독성 유지
```

전체 research_daily 크기와 파일 수를 diagnostics에 보고한다.

────────────────────────────────────────
14. 검증 스크립트
────────────────────────────────────────

```bash
python scripts/validate_research_daily.py --full
```

다음을 전수 검사한다.

## 14.1 캘린더 완전성

```text
2016-01-01 이후 실제 거래일 누락 0
중복 거래일 0
비거래일 파일 0
각 거래일 access JSON 존재
각 access의 blind·outcome 파일 존재
```

## 14.2 미래정보 누수 방지

거래일 D마다:

```text
blind_snapshot_date == previous_trade_date
blind snapshot의 모든 snapshot_date == previous_trade_date
blind snapshot의 모든 max_source_date <= previous_trade_date
outcome snapshot_date == D
```

어느 하나라도 실패하면 전체 검증 실패다.

## 14.3 전 시장 completeness

각 snapshot D에 대해:

```text
snapshot row count
==
기존 tradable shard에서
d == D이고 기본 시장에 속한 고유 code 수
```

중복 code는 0이어야 한다.

## 14.4 원천 일치

무작위 표본과 고정 smoke 종목에 대해 기존 shard와 비교한다.

최소 고정 smoke:

```text
005930
000660
298040
267260
```

비교:

```text
open
high
low
close
volume
amount
market_cap
listed_shares
market
```

## 14.5 이름 누수 방지

```text
snapshot_date 이후 처음 등장한 회사명을 과거 snapshot에 사용하지 않음
복수 name history가 있으면 ambiguous 상태
latest name을 과거에 조용히 소급한 사례 0
```

## 14.6 상한가 라벨

synthetic fixture와 실제 clean sample을 사용해 검증한다.

```text
호가단위 경계
2023년 전후 규칙 변화
상한가 터치
상한가 마감
상한가 이탈
일자형 상한가
신규상장
기업행위 차단
```

단순 29.x% threshold 테스트만 만들지 않는다.

## 14.7 재현성

동일 source·동일 옵션으로 두 번 실행하면:

```text
snapshot SHA-256 동일
access manifest 동일
calendar 동일
manifest의 비시간 필드 동일
```

이어야 한다.

────────────────────────────────────────
15. pytest
────────────────────────────────────────

`tests/test_research_daily.py`에 최소 다음 테스트를 작성한다.

```text
test_research_daily_manifest_exists
test_schema_columns_are_exact
test_calendar_has_no_duplicate_dates
test_calendar_paths_exist
test_blind_path_points_to_previous_trade_date
test_no_future_date_in_blind_snapshot
test_snapshot_has_unique_zero_padded_codes
test_snapshot_row_count_matches_source_day
test_samsung_values_match_source_shard
test_historical_name_does_not_use_future_name
test_ambiguous_name_is_not_silently_resolved
test_upper_limit_touch
test_upper_limit_close
test_upper_limit_release
test_one_price_upper_limit
test_new_listing_has_no_false_limit_label
test_corporate_action_blocks_limit_label
test_incremental_build_is_idempotent
test_access_manifest_hashes_match_files
test_no_research_daily_file_over_50_mib
test_20260622_access_bundle_is_complete
```

기존 테스트도 모두 통과해야 한다.

```bash
pytest -q
```

────────────────────────────────────────
16. Diagnostics
────────────────────────────────────────

다음을 생성한다.

```text
diagnostics/research_daily_build_report.json
diagnostics/research_daily_build_report.md
diagnostics/research_daily_validation_report.json
diagnostics/research_daily_validation_report.md
diagnostics/research_daily_size_report.json
```

보고 내용:

```text
생성 기간
실제 거래일 수
snapshot 수
access manifest 수
총 행 수
총 크기
최대 파일 크기
이름 ambiguous 수
기업행위 차단 수
상한가 라벨 verified 수
라벨 blocked 수
누락 날짜
누락 파일
검증 실패
```

────────────────────────────────────────
17. 2026-06-22 필수 smoke 검증
────────────────────────────────────────

작업 완료 전에 반드시 다음을 실제 확인한다.

```text
access:
atlas/research_daily/access/2026/06/20260622.json

BLIND:
access JSON의 blind_snapshot_date == 2026-06-19
blind snapshot의 max_source_date <= 2026-06-19

OUTCOME:
access JSON의 outcome_snapshot_date == 2026-06-22
outcome snapshot에 2026-06-22 전 시장 행이 존재

005930:
outcome snapshot의 값이
atlas/ohlcv_tradable_by_symbol_year/005/005930/2026.csv의
2026-06-22 행과 정확히 일치

completeness:
2026-06-22 snapshot row count가
해당 날짜 기본 시장 tradable code 수와 일치
```

부분 후보 74개만 읽는 상태를 성공으로 처리하지 않는다.
전 시장 snapshot이 완성돼야 한다.

────────────────────────────────────────
18. 기존 빌드 파이프라인 연동
────────────────────────────────────────

현재 저장소의 atlas 업데이트 스크립트와 GitHub Actions가 있다면 직접 조사하라.

가장 덜 파괴적인 방식으로:

```text
가격 atlas 갱신
→ research_daily incremental 생성
→ validate
→ tests
```

순서가 자동 실행되게 연결한다.

기존 빌드가 너무 오래 걸리지 않도록 incremental을 기본으로 사용한다.

워크플로가 없다면 README에 정확한 갱신 명령을 추가하되,
불필요하게 별도 서버나 API를 요구하지 않는다.

이 문제를 `marcap-price-gateway` 서버 실행으로만 해결하지 않는다.
핵심 산출물은 GitHub Raw에서 직접 읽을 수 있는 정적 파일이어야 한다.

────────────────────────────────────────
19. 금지사항
────────────────────────────────────────

다음을 금지한다.

```text
기존 종목별 atlas 삭제 또는 교체
기존 schema 의미 변경
current/latest 이름을 역사적 이름으로 소급
BLIND D에 D snapshot을 연결
outcome 일부 종목만 생성하고 complete로 선언
후보 종목만 가격 수집
포털 TOP30으로 전 시장을 대체
29.5% 임계치만으로 verified 상한가 선언
기업행위 의심일의 상한가를 무조건 확정
parquet·zip만 생성하고 GPT용 CSV를 생략
Git LFS 의존
수동 파일 업로드를 필수로 요구
계획만 제출하고 구현 중단
테스트 실패를 남긴 채 완료 보고
```

────────────────────────────────────────
20. 실제 백필 실행
────────────────────────────────────────

구현만 하고 멈추지 마라.

아래 범위를 실제 생성한다.

```text
연구 시작: 2016-01-01
연구 종료: atlas manifest max_date
현재 기대 종료: 2026-06-22
```

첫 2016년 연구 거래일에 필요한 이전 거래일 seed snapshot도 생성한다.

백필 완료 후:

```bash
python scripts/validate_research_daily.py --full
pytest -q
```

를 실행하고 모든 오류를 수정한다.

────────────────────────────────────────
21. 완료 기준
────────────────────────────────────────

다음이 모두 충족되어야 완료다.

```text
- 2016년부터 2026-06-22까지 실제 거래일 access manifest가 모두 존재
- 필요한 seed snapshot을 포함한 모든 snapshot 존재
- 전 시장 KOSPI·KOSDAQ·KOSDAQ GLOBAL 단면이 날짜별로 완성
- BLIND 경로가 항상 P snapshot을 가리킴
- outcome 경로가 항상 D snapshot을 가리킴
- future leak audit 100% 통과
- 각 snapshot 행 수가 기존 atlas 원천과 일치
- historical name 누수 검사 통과
- 상한가 라벨 quality status가 존재
- corporate action·신규상장 차단이 작동
- 20260622 smoke 검증 통과
- incremental build 재실행 시 변경 없음
- 기존 pytest 포함 전체 테스트 통과
- GitHub Raw에서 access JSON과 snapshot CSV를 바로 다운로드 가능
- README만 읽어도 GPT 연구원이 올바른 순서로 사용할 수 있음
```

────────────────────────────────────────
22. 작업 순서
────────────────────────────────────────

1. 현재 저장소 전체 구조와 기존 스크립트·테스트를 조사한다.
2. 구현 계획을 짧게 세운다.
3. 공통 로더와 계산 함수를 구현한다.
4. daily snapshot builder를 구현한다.
5. access manifest와 calendar를 구현한다.
6. schema와 manifest를 구현한다.
7. 상한가·기업행위·이름 as-of 로직을 구현한다.
8. 검증기와 테스트를 구현한다.
9. README와 LLM README를 작성한다.
10. 기존 업데이트 흐름에 incremental 생성을 연결한다.
11. 2016~2026-06-22 전체를 실제 백필한다.
12. full validation을 실행한다.
13. pytest 전체를 실행한다.
14. 실패를 수정한다.
15. 최종 산출물과 Raw URL을 검증한다.
16. 완료 결과만 보고한다.

────────────────────────────────────────
23. 최종 응답 방식
────────────────────────────────────────

최종 응답에는 다음을 명확히 보고한다.

```text
1. 구현한 구조
2. 추가·수정한 코드 파일
3. 생성한 날짜 범위
4. 거래일·snapshot·access 파일 수
5. 총 행 수와 총 크기
6. 20260622 smoke 결과
7. 미래정보 누수 검증 결과
8. 상한가 라벨 검증 결과
9. pytest 결과
10. incremental 재실행 결과
11. 실제 Raw URL 예시
12. 남은 데이터 한계
```

계획이나 제안만 말하지 말고 실제 구현·백필·검증 결과를 제출하라.