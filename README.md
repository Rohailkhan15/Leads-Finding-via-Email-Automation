# Email Lead Generation — Apollo CSV → Google Sheets

Manually-run script that imports leads from Apollo CSV exports into the `Email`
tab of the lead-tracking Google Sheet, skipping rows without an email and rows
whose email is already in the sheet.

Apollo's API is no longer used — the free plan blocks programmatic search, so
leads are exported by hand from Apollo's web UI.

## Weekly routine

1. Export leads from Apollo's web UI and put the CSV(s) into `apollo_exports/`.
2. Run `python email_automation.py`.
3. Each CSV is appended to the sheet and then moved into `apollo_exports/processed/`,
   so re-running never re-imports it.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file (git-ignored) with:

```env
SHEET_ID=<id from https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit>
GOOGLE_CREDS_JSON=<full service-account JSON key, on one line>
```

The service account (Google Cloud → Sheets API enabled → Service Accounts →
Keys → JSON) must be shared on the Sheet with **Editor** access.

The `Email` tab must have these headers in row 1:

| Name | Business/Profile | Platform | Date Found | Message Sent | Response | Follow-up 1 | Follow-up 2 | Status |
|---|---|---|---|---|---|---|---|---|

Rows are appended as: name, `email | company`, `Email`, today's date, then blanks.

## CSV column mapping

Headers are matched by a normalised form — lowercased with non-alphanumeric
characters removed, so `First Name` → `firstname` and `Company Name` →
`companyname`. For each field the candidate headers are tried in order and the
first one present in the CSV *with a non-empty value in that row* wins:

| Field | Headers tried (in order) |
|---|---|
| Email | `Email`, `Email Address`, `Primary Email`, `Work Email`, `Personal Email`, `Contact Email`, `Secondary Email`, `Tertiary Email` |
| Name | `Name`, `Full Name`, `Contact Name`, `Person Name`; otherwise `First Name` + `Last Name`; otherwise `Unknown` |
| Company | `Company`, `Company Name`, `Organization`, `Organisation`, `Organization Name`, `Account Name`, `Employer`, `Company Name for Emails`; otherwise `Unknown Company` |

Rows with no email in any of those columns are skipped.

## Output

Per file and in total, the script prints how many CSVs were found, rows read,
rows skipped (no email / duplicate), and new rows added — plus the detected
headers of each CSV.
