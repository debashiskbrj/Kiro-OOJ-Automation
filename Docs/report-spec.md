# report-spec.md — Final Report Columns (LIVE DOC)

Authoritative column list given by the user on 2026-09-17. Exact header text matters
(copy from here; note trailing spaces in some template headers were dropped here on
purpose — the pipeline writes clean headers). Source mapping per column is the
current understanding; the user will confirm/adjust "from where what to pull".

| # | Column | Source | Field / rule | Status |
|---|---|---|---|---|
| 1 | IssueUrl | SIM-T | `https://t.corp.amazon.com/issues/<ShortId>` | WORKING |
| 2 | CreateDate | SIM-T | `createDate` | WORKING |
| 3 | LastUpdatedDate | SIM-T | `lastUpdatedDate` | WORKING |
| 4 | TID | SIM-T | one per row; regex `DOC\d+|\b\d{12}\b` over description; `TID not found` if none | WORKING |
| 5 | Station Code(SCC) | SCC | `source` (template: SCC col D "Source") | WORKING |
| 6 | Zone | DSR dashboard | lookup/export by TID → `zone`; missing/blank source data remains blank | WORKING — CURRENT LIVE SNAPSHOT VERIFIED |
| 7 | State | SCC | `currentPackageState` | WORKING |
| 8 | Destination | SCC | `destination` | WORKING |
| 9 | CX Pincode | GCRS/SIW | `address.postalCode` | WORKING |
| 10 | ADS Predicted Pincode | ADS | `predicted_pincode_BiLSTM` (int) | WORKING |
| 11 | Probability | ADS | `predicted_pincode_proba_BiLSTM` | WORKING |
| 12 | Pincode Validation | computed | `CX Pincode == ADS Predicted Pincode` | — |
| 13 | Remark | computed | remark engine (architecture.md §4) | — |
| 14 | Predicted Pincode served by station | Pin-Station ref | lookup ADS pincode → station | AVAILABLE |
| 15 | Station Validation | computed | `Station Code(SCC) == Predicted Pincode served by station` | — |
| 16 | In - Swa | SCC | `shipOption` (contains "swa") | WORKING |
| 17 | Short ID | SIM-T | first alias matching `^[A-Z]\d+$` | WORKING |
| 18 | URL | SIM-T | `https://t.corp.amazon.com/<ShortId>/communication` | WORKING |
| 19 | RTO Status | EagleEye | `package_type` (`REVERSE` = RTO) | WORKING; `REVERSE` live-verified 2026-09-18 |
| 20 | Past delivered record | PNAMA | `station_missort` (template col CP) | WORKING |
| 21 | Delivered Old shipment | PNAMA | `reference_tid` (col CC) | WORKING |
| 22 | Old shipment delivered by Station | PNAMA | `reference_station` (col CJ) | WORKING |
| 23 | SCC & misort Station validation | computed | `Old shipment delivered by Station == Station Code(SCC)` | — |
| 24 | Station Transfer Validation | computed | Station & Sub Station parent/child rule (template col AF) | AVAILABLE |
| 25 | GCRS Agent Space | local SIW polygon checker | row-aligned status from `tools\tid_polygon_checker.py`; exact Inside text is `Invalid - It is Inside Jurisdiction as per GCRS Tool` | WORKING; LIVE AUTO-FALLBACK + XLSX VERIFIED 2026-09-18 |

(25 rows because the user's list has 25 headers; "Destination" and "CX Pincode" had
trailing spaces in the template.)

## Dropped vs the template Working Sheet

Tire 3 city, Station Code(RAW Data), Images, Shipper, Baby shipper, Case1, Case2,
OBLT Event. **DSR Waterfall remains required only to map each TID to Zone**; its
Shipper/Baby-shipper fields are not used.

## PNAMA business meaning (user, 2026-09-17)

PNAMA finds Amazon.in (a.in) shipments **delivered earlier** whose address matches the
SWA shipment's address. If a match exists and the station that delivered it is the
station assigned now → the address is deliverable by that station → **NOT OOJ**
(remark: "Invalid - Correct Station-A.in Shipment delivered - LM to Reattempt and
Deliver"). If no match (or a different station) → OOJ remains possible → continue with
the pincode/ADS/transfer logic.

## Open until the user's mapping walkthrough

- Final ordering/formatting, sheet name, any summary block.
- Exact DSR dashboard field name containing Zone (to be captured during recon).
- Whether "Station Code(SCC)" should use SCC `Source` (template/manual understanding)
  or SCC `Station/currentStationCode` if those differ.
