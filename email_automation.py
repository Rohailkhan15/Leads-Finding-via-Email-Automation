"""
Email Lead Generation Automation — Apollo.io + Google Sheets
=============================================================
Searches Apollo for e-commerce founders/owners, reveals their emails,
deduplicates against the existing Google Sheet, and appends new leads.

Required environment variables / secrets
-----------------------------------------
  APOLLO_API_KEY     - Apollo.io API key (Settings → API Keys).
                       The key must have the following scopes enabled:
                         • mixed_people_api_search  (people search, free)
                         • people_bulk_match        (bulk enrichment, costs credits)

  SHEET_ID           - Google Sheets spreadsheet ID.
                       Found in the sheet URL:
                         https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit

  GOOGLE_CREDS_JSON  - The full contents of a Google service-account JSON key
                       file, pasted as a single-line string.
                       The service account must have Editor access to the sheet.

Local testing
-------------
  1. Create a .env file (already in .gitignore) with the three variables above.
  2. The script loads it automatically via python-dotenv.
  3. Run:  pip install -r requirements.txt
           python email_automation.py

GitHub Actions
--------------
  Add the three variables as repository secrets (Settings → Secrets and
  variables → Actions). The workflow passes them as environment variables
  so no .env file is needed there.
"""

import os
import json
import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load .env when running locally; on GitHub Actions this is a no-op because
# the environment variables are already set by the workflow's env: block.
load_dotenv()

# ---------------------------------------------------------------------------
# Config — all values come from environment variables, never hardcoded
# ---------------------------------------------------------------------------
APOLLO_API_KEY = os.environ["APOLLO_API_KEY"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]

TARGET_LEADS = 15    # stop appending once this many new leads have been added
SEARCH_BUFFER = 25   # how many candidates to pull from Apollo's search step


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

def get_sheet():
    """Authenticate with the service account and return the 'Email' worksheet."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Email")
    return sheet


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
            email_part = cell.split(" | ")[0].strip().lower()
            if "@" in email_part:   # basic sanity check it's actually an email
                existing.add(email_part)
    return existing


# ---------------------------------------------------------------------------
# Apollo API helpers
# ---------------------------------------------------------------------------

def search_candidates():
    """
    Search Apollo for e-commerce founders/owners (free — uses 0 credits).

    Apollo's people search endpoint accepts all filter parameters as query
    strings (not a JSON body). Array parameters must be sent as repeated keys.
    Returns a list of person objects, each containing at minimum: id, first_name,
    last_name_obfuscated, title, has_email, organization.name.

    NOTE: The search response does NOT include email addresses.
    Use enrich_candidates() to reveal them (costs credits).
    """
    url = "https://api.apollo.io/api/v1/mixed_people/api_search"
    headers = {
        "x-api-key": APOLLO_API_KEY,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    # All parameters are query strings for this endpoint.
    # Array params must be repeated (requests handles this via list values).
    params = {
        "person_titles[]": ["Founder", "Owner", "CEO"],
        "q_keywords": "ecommerce OR shopify OR \"online store\"",
        "page": 1,
        "per_page": SEARCH_BUFFER,
    }
    print(f"[Apollo Search] Searching for up to {SEARCH_BUFFER} candidates...")
    response = requests.post(url, headers=headers, params=params)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        print(f"[Apollo Search] HTTP error {response.status_code}: {response.text}")
        raise exc

    data = response.json()
    candidates = data.get("people", [])
    print(f"[Apollo Search] Found {len(candidates)} candidates.")
    return candidates


def enrich_candidates(people):
    """
    Reveal emails for a batch of up to 10 people (costs credits — 1 credit
    per person where credit-consuming data is found).

    Apollo's bulk enrichment endpoint:
      - Accepts the 'details' array in the JSON request body.
      - Accepts reveal_personal_emails as a query parameter (NOT in the body).
      - Returns results under the 'matches' key.

    Each match contains: id, name, first_name, last_name, email, email_status,
    title, organization (with .name), state, city, country, and more.
    """
    url = "https://api.apollo.io/api/v1/people/bulk_match"
    headers = {
        "x-api-key": APOLLO_API_KEY,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    # reveal_personal_emails is a query parameter, not a body field
    params = {
        "reveal_personal_emails": "true",
    }
    details = [{"id": p["id"]} for p in people]
    body = {
        "details": details,
    }
    response = requests.post(url, headers=headers, params=params, json=body)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        print(f"[Apollo Enrich] HTTP error {response.status_code}: {response.text}")
        raise exc

    data = response.json()
    matches = data.get("matches", [])
    credits_used = data.get("credits_consumed", "unknown")
    print(
        f"[Apollo Enrich]  Batch of {len(details)}: "
        f"{len(matches)} matched, {credits_used} credits consumed."
    )
    return matches


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Email Lead Generation — starting run")
    print(f"Target: {TARGET_LEADS} new leads | Search buffer: {SEARCH_BUFFER}")
    print("=" * 60)

    # --- Connect to Google Sheets ---
    print("[Sheets] Connecting to Google Sheet...")
    sheet = get_sheet()
    existing_emails = get_existing_emails(sheet)
    print(f"[Sheets] {len(existing_emails)} existing email(s) already in sheet.")

    # --- Step 1: Search (free) ---
    candidates = search_candidates()
    if not candidates:
        print("[Done] No candidates returned from Apollo search. Nothing to add.")
        return

    # Only enrich candidates that Apollo flagged as having an email, to minimise
    # wasted credit attempts on people with no email on file.
    enrichable = [p for p in candidates if p.get("has_email")]
    skipped_no_email_flag = len(candidates) - len(enrichable)
    print(
        f"[Filter] {len(enrichable)} candidates have has_email=true "
        f"({skipped_no_email_flag} skipped — has_email=false, no credit spent)."
    )

    if not enrichable:
        print("[Done] No enrichable candidates after filtering. Nothing to add.")
        return

    # --- Step 2: Enrich in batches of 10 (costs credits) ---
    print(f"[Apollo Enrich] Enriching {len(enrichable)} candidate(s) in batches of 10...")
    enriched = []
    for i in range(0, len(enrichable), 10):
        batch = enrichable[i:i + 10]
        try:
            enriched.extend(enrich_candidates(batch))
        except requests.HTTPError:
            print(f"[Apollo Enrich] Batch {i // 10 + 1} failed — skipping batch.")

    print(f"[Apollo Enrich] Total enriched records returned: {len(enriched)}")

    # --- Step 3: Build new rows, deduplicating against existing sheet data ---
    new_rows = []
    skipped_no_email = 0
    skipped_duplicate = 0

    for person in enriched:
        email = (person.get("email") or "").strip()

        if not email:
            skipped_no_email += 1
            continue

        if email.lower() in existing_emails:
            skipped_duplicate += 1
            continue

        name = person.get("name") or (
            f"{person.get('first_name', '')} {person.get('last_name', '')}".strip()
        ) or "Unknown"
        company = person.get("organization", {}).get("name", "Unknown Company")
        today = datetime.date.today().isoformat()

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
        existing_emails.add(email.lower())  # prevent within-run dupes

        if len(new_rows) >= TARGET_LEADS:
            break

    # --- Step 4: Append to sheet ---
    print("-" * 60)
    print(f"[Summary] Enriched:        {len(enriched)}")
    print(f"[Summary] Skipped (no email returned):  {skipped_no_email}")
    print(f"[Summary] Skipped (duplicate):          {skipped_duplicate}")
    print(f"[Summary] New rows to add:              {len(new_rows)}")

    if new_rows:
        sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"[Sheets] Successfully appended {len(new_rows)} new lead(s).")
    else:
        print("[Sheets] No new unique leads to add this run.")

    print("=" * 60)
    print("Run complete.")


if __name__ == "__main__":
    main()
