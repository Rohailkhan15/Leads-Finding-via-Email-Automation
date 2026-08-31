"""
Email Lead Generation Automation — Apollo CSV exports + Google Sheets
=====================================================================
Reads manually-exported Apollo lead CSVs from apollo_exports/, deduplicates
them against the existing Google Sheet, appends the new leads to the "Email"
tab, and moves each processed CSV into apollo_exports/processed/.

This script is run manually (no API calls to Apollo, no scheduled job).

Required environment variables
------------------------------
  SHEET_ID           - Google Sheets spreadsheet ID.
                       Found in the sheet URL:
                         https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit

  GOOGLE_CREDS_JSON  - The full contents of a Google service-account JSON key
                       file, pasted as a single-line string.
                       The service account must have Editor access to the sheet.

Usage
-----
  1. Export leads from Apollo's web UI and drop the CSV(s) into apollo_exports/.
  2. Create a .env file (already in .gitignore) with the two variables above.
  3. Run:  pip install -r requirements.txt
           python email_automation.py
"""

import csv
import datetime
import json
import os
import shutil

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apollo_exports")
PROCESSED_DIR = os.path.join(EXPORT_DIR, "processed")

# ---------------------------------------------------------------------------
# CSV column mapping
# ---------------------------------------------------------------------------
# Apollo's export headers vary between export types, so headers are matched by
# a normalised form (lowercased, non-alphanumeric characters stripped):
#   "First Name" -> "firstname",  "Company Name" -> "companyname"
# For each field the candidates below are tried in order and the first header
# present in the CSV *with a non-empty value in the row* wins.

EMAIL_HEADERS = [
    "email",
    "emailaddress",
    "primaryemail",
    "workemail",
    "personalemail",
    "contactemail",
    "secondaryemail",
    "tertiaryemail",
]

FULL_NAME_HEADERS = ["name", "fullname", "contactname", "personname"]
FIRST_NAME_HEADERS = ["firstname", "first"]
LAST_NAME_HEADERS = ["lastname", "last", "surname"]

COMPANY_HEADERS = [
    "company",
    "companyname",
    "organization",
    "organisation",
    "organizationname",
    "accountname",
    "employer",
    "companynameforemails",
]


def normalize_header(header):
    """'Company Name for Emails' -> 'companynameforemails'."""
    return "".join(ch for ch in (header or "").lower() if ch.isalnum())


def build_header_index(fieldnames):
    """Map normalised header -> original header, keeping the first occurrence."""
    index = {}
    for name in fieldnames or []:
        key = normalize_header(name)
        if key and key not in index:
            index[key] = name
    return index


def pick(row, header_index, candidates):
    """Return the first non-empty value among the candidate headers."""
    for candidate in candidates:
        original = header_index.get(candidate)
        if original is None:
            continue
        value = (row.get(original) or "").strip()
        if value:
            return value
    return ""


def extract_lead(row, header_index):
    """
    Extract (name, email, company) from one CSV row.

    Name falls back to "First Name" + "Last Name" when there is no single
    full-name column, and to "Unknown" when neither is present.
    """
    email = pick(row, header_index, EMAIL_HEADERS)

    name = pick(row, header_index, FULL_NAME_HEADERS)
    if not name:
        first = pick(row, header_index, FIRST_NAME_HEADERS)
        last = pick(row, header_index, LAST_NAME_HEADERS)
        name = f"{first} {last}".strip()
    if not name:
        name = "Unknown"

    company = pick(row, header_index, COMPANY_HEADERS) or "Unknown Company"

    return name, email, company


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

def get_sheet():
    """Authenticate with the service account and return the 'Email' worksheet."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet("Email")


def get_existing_emails(sheet):
    """
    Return a set of lowercased emails already in the sheet.

    The 'Business/Profile' column is formatted as 'email | Company Name',
    so we split on ' | ' and take the first segment as the email.
    """
    rows = sheet.get_all_records()
    existing = set()
    for row in rows:
        cell = row.get("Business/Profile", "")
        if cell:
            email_part = str(cell).split(" | ")[0].strip().lower()
            if "@" in email_part:
                existing.add(email_part)
    return existing


# ---------------------------------------------------------------------------
# CSV handling
# ---------------------------------------------------------------------------

def find_csv_files():
    """Return the CSV files sitting directly in apollo_exports/ (sorted)."""
    if not os.path.isdir(EXPORT_DIR):
        return []
    return sorted(
        os.path.join(EXPORT_DIR, name)
        for name in os.listdir(EXPORT_DIR)
        if name.lower().endswith(".csv")
        and os.path.isfile(os.path.join(EXPORT_DIR, name))
    )


def read_csv_leads(path):
    """Read one CSV and return (leads, rows_read) where leads = (name, email, company)."""
    leads = []
    rows_read = 0
    # utf-8-sig strips the BOM Apollo/Excel sometimes writes at the start.
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header_index = build_header_index(reader.fieldnames)
        print(f"  Headers detected: {', '.join(reader.fieldnames or []) or '(none)'}")
        for row in reader:
            rows_read += 1
            leads.append(extract_lead(row, header_index))
    return leads, rows_read


def move_to_processed(path):
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    destination = os.path.join(PROCESSED_DIR, os.path.basename(path))
    if os.path.exists(destination):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        base, ext = os.path.splitext(os.path.basename(path))
        destination = os.path.join(PROCESSED_DIR, f"{base}-{stamp}{ext}")
    shutil.move(path, destination)
    return destination


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Email Lead Generation — CSV import run")
    print("=" * 60)

    csv_files = find_csv_files()
    print(f"[CSV] Found {len(csv_files)} CSV file(s) in apollo_exports/.")
    if not csv_files:
        print("[Done] Nothing to process. Export a CSV from Apollo into apollo_exports/ first.")
        return

    print("[Sheets] Connecting to Google Sheet...")
    sheet = get_sheet()
    existing_emails = get_existing_emails(sheet)
    print(f"[Sheets] {len(existing_emails)} existing email(s) already in the Email tab.")

    today = datetime.date.today().isoformat()
    total_rows = 0
    total_skipped_no_email = 0
    total_skipped_duplicate = 0
    total_added = 0

    for path in csv_files:
        filename = os.path.basename(path)
        print("-" * 60)
        print(f"[CSV] Processing {filename}")

        try:
            leads, rows_read = read_csv_leads(path)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            print(f"[CSV] Failed to read {filename}: {exc} — leaving it in place.")
            continue

        new_rows = []
        skipped_no_email = 0
        skipped_duplicate = 0

        for name, email, company in leads:
            if not email:
                skipped_no_email += 1
                continue
            if email.lower() in existing_emails:
                skipped_duplicate += 1
                continue

            new_rows.append([
                name,
                f"{email} | {company}",
                "Email",
                today,
                "",   # Message Sent
                "",   # Response
                "",   # Follow-up 1
                "",   # Follow-up 2
                "",   # Status
            ])
            existing_emails.add(email.lower())   # prevents duplicates within this run too

        print(f"  Rows read:              {rows_read}")
        print(f"  Skipped (no email):     {skipped_no_email}")
        print(f"  Skipped (duplicate):    {skipped_duplicate}")
        print(f"  New rows to add:        {len(new_rows)}")

        if new_rows:
            sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
            print(f"  [Sheets] Appended {len(new_rows)} new lead(s).")
        else:
            print("  [Sheets] No new unique leads in this file.")

        destination = move_to_processed(path)
        print(f"  [Move] {filename} -> {os.path.relpath(destination, EXPORT_DIR)}/")

        total_rows += rows_read
        total_skipped_no_email += skipped_no_email
        total_skipped_duplicate += skipped_duplicate
        total_added += len(new_rows)

    print("=" * 60)
    print(f"[Summary] CSV files processed:    {len(csv_files)}")
    print(f"[Summary] Rows read:              {total_rows}")
    print(f"[Summary] Skipped (no email):     {total_skipped_no_email}")
    print(f"[Summary] Skipped (duplicate):    {total_skipped_duplicate}")
    print(f"[Summary] New rows added:         {total_added}")
    print("Run complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
