# Email Lead Generation Automation — Python + GitHub Actions

## 1. What This Automation Does

A Python script that:
1. Searches Apollo.io for e-commerce founders/owners matching our ICP (free — no credits used).
2. Reveals verified emails for the top candidates (this step uses Apollo credits).
3. Reads the existing `Email` tab in Google Sheets, checks for duplicates.
4. Appends only new, unique leads.

GitHub Actions runs this script automatically once a week — no server, no Docker, nothing that needs to stay "on."

Target: 15 new leads/week.

---

## 2. Before You Start

- [ ] A GitHub account (you already use this)
- [ ] An Apollo.io account (free plan)
- [ ] A Google account with the lead-tracking Sheet already created
- [ ] Python installed locally (for testing before automating) — Python 3.10+

---

## 3. Step 1 — Create the GitHub Repo

1. Create a new **private** repo — e.g. `trevolk-lead-gen` (private, since it'll reference config even though secrets themselves stay out of the code).
2. Clone it locally: `git clone <repo-url>`
3. Inside it, create this structure:
   ```
   trevolk-lead-gen/
   ├── .github/
   │   └── workflows/
   │       └── email_automation.yml
   ├── email_automation.py
   ├── requirements.txt
   └── .gitignore
   ```
4. In `.gitignore`, add:
   ```
   *.json
   .env
   __pycache__/
   ```
   (This stops you from ever accidentally committing your Google credentials file.)

---

## 4. Step 2 — Set Up Apollo.io

1. Sign up at [apollo.io](https://apollo.io) with `trevolk.official@gmail.com`, free plan.
2. Go to **Settings → Plan & Credits** and note your actual monthly credit allowance — check this yourself rather than trusting any number online, since Apollo's free tier has changed more than once recently.
3. Go to **Settings → API Keys** and generate a key. Copy it somewhere safe — you'll add it to GitHub Secrets in Step 7, never directly into your code.

---

## 5. Step 3 — Set Up Google Sheets Access (Service Account Method)

This is simpler than the n8n OAuth flow — no login screen, no redirect URI, no localhost issues. A script authenticates as its own "robot" account instead of as you.

1. Go to [Google Cloud Console](https://console.cloud.google.com/), create a project (e.g. "Trevolk Lead Gen").
2. Go to **APIs & Services → Library**, search **Google Sheets API**, click **Enable**.
3. Go to **IAM & Admin → Service Accounts → Create Service Account**.
   - Name: `trevolk-sheets-bot` (anything descriptive)
   - No special roles needed at the project level — access is granted directly on the Sheet in the next step.
4. Once created, click into the service account → **Keys → Add Key → Create New Key → JSON**. This downloads a `.json` file — this is your credential. Keep it private, never commit it to GitHub.
5. Open that JSON file and copy the `client_email` field (looks like `trevolk-sheets-bot@your-project.iam.gserviceaccount.com`).

---

## 6. Step 4 — Share the Google Sheet with the Service Account

1. Open your lead-tracking Google Sheet.
2. Click **Share**, paste in the `client_email` from Step 5, give it **Editor** access.
3. That's it — the script can now read/write this sheet, same as if it were a person you invited.

Make sure the sheet has an `Email` tab with these column headers in row 1:

| Name | Business/Profile | Platform | Date Found | Message Sent | Response | Follow-up 1 | Follow-up 2 | Status |
|---|---|---|---|---|---|---|---|---|

---

## 7. Step 5 — Project Dependencies

`requirements.txt`:
```
requests
gspread
google-auth
python-dotenv
```

- `requests` — calls the Apollo API
- `gspread` + `google-auth` — read/write Google Sheets using the service account
- `python-dotenv` — lets you test locally with a `.env` file instead of hardcoding secrets

---

## 8. Step 6 — Write the Script

`email_automation.py`:

```python
import os
import json
import datetime
import requests
import gspread
from google.oauth2.service_account import Credentials

# --- Config ---
APOLLO_API_KEY = os.environ["APOLLO_API_KEY"]
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]  # raw JSON string from secret

TARGET_LEADS = 15
SEARCH_BUFFER = 25  # pull a few extra since not all will have revealable emails

# --- Google Sheets setup ---
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).worksheet("Email")
    return sheet

# --- Apollo: search for candidates (free, no credits used) ---
def search_candidates():
    url = "https://api.apollo.io/api/v1/mixed_people/api_search"
    headers = {
        "X-Api-Key": APOLLO_API_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "person_titles": ["Founder", "Owner", "CEO"],
        "q_keywords": "ecommerce OR shopify OR online store",
        "page": 1,
        "per_page": SEARCH_BUFFER,
    }
    response = requests.post(url, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()
    return data.get("people", [])

# --- Apollo: reveal emails for candidates (uses credits) ---
def enrich_candidates(people):
    url = "https://api.apollo.io/api/v1/people/bulk_match"
    headers = {
        "X-Api-Key": APOLLO_API_KEY,
        "Content-Type": "application/json",
    }
    details = [{"id": p["id"]} for p in people[:10]]  # bulk_match caps at 10 per call
    body = {
        "details": details,
        "reveal_personal_emails": True,
    }
    response = requests.post(url, headers=headers, json=body)
    response.raise_for_status()
    return response.json().get("matches", [])

# --- Main logic ---
def main():
    sheet = get_sheet()
    existing_rows = sheet.get_all_records()
    existing_emails = {row["Business/Profile"].split(" | ")[0].lower() for row in existing_rows if row.get("Business/Profile")}

    candidates = search_candidates()
    if not candidates:
        print("No candidates found from Apollo search.")
        return

    # Enrich in batches of 10
    enriched = []
    for i in range(0, len(candidates), 10):
        batch = candidates[i:i + 10]
        enriched.extend(enrich_candidates(batch))

    new_rows = []
    for person in enriched:
        email = person.get("email")
        if not email or email.lower() in existing_emails:
            continue  # skip missing or duplicate emails

        name = person.get("name", "Unknown")
        company = person.get("organization", {}).get("name", "Unknown Company")
        today = datetime.date.today().isoformat()

        new_rows.append([
            name,
            f"{email} | {company}",
            "Email",
            today,
            "", "", "", "", ""  # Message Sent, Response, Follow-up 1, Follow-up 2, Status — left blank
        ])
        existing_emails.add(email.lower())

        if len(new_rows) >= TARGET_LEADS:
            break

    if new_rows:
        sheet.append_rows(new_rows)
        print(f"Added {len(new_rows)} new leads.")
    else:
        print("No new unique leads to add this run.")

if __name__ == "__main__":
    main()
```

**Note on Apollo field names:** the exact response structure (`people`, `matches`, field names like `organization.name`) is based on Apollo's documented behavior — double-check against a live test call (Step 9) and adjust field access if their response shape differs slightly, since API responses can change.

---

## 9. Step 7 — Store Secrets in GitHub

Never put API keys or credentials directly in your code. In your repo:

1. Go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Add three secrets:
   - `APOLLO_API_KEY` → your Apollo key from Step 4
   - `SHEET_ID` → the long ID from your Google Sheet's URL (the part between `/d/` and `/edit`)
   - `GOOGLE_CREDS_JSON` → open your service account `.json` file from Step 5, copy its **entire contents**, paste as the secret value

---

## 10. Step 8 — GitHub Actions Workflow

`.github/workflows/email_automation.yml`:

```yaml
name: Email Lead Generation

on:
  schedule:
    - cron: "0 9 * * 1"   # every Monday, 9:00 AM UTC
  workflow_dispatch: {}    # lets you trigger it manually from GitHub's UI, for testing

jobs:
  run-automation:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run email automation script
        env:
          APOLLO_API_KEY: ${{ secrets.APOLLO_API_KEY }}
          SHEET_ID: ${{ secrets.SHEET_ID }}
          GOOGLE_CREDS_JSON: ${{ secrets.GOOGLE_CREDS_JSON }}
        run: python email_automation.py
```

**Note on the cron time:** GitHub Actions schedules run in UTC. `0 9 * * 1` means Monday 9:00 AM UTC — convert that to your local time (Pakistan is UTC+5, so this fires at 2:00 PM PKT) and adjust the cron expression if you want a different local time.

---

## 11. Step 9 — Test Locally First

Before relying on GitHub Actions, run it on your own machine to catch errors early:

1. Create a `.env` file locally (already in `.gitignore`, so it won't get committed):
   ```
   APOLLO_API_KEY=your_key_here
   SHEET_ID=your_sheet_id_here
   GOOGLE_CREDS_JSON={"type": "service_account", ...}   # paste full JSON as one line
   ```
2. At the top of `email_automation.py`, temporarily add (for local testing only):
   ```python
   from dotenv import load_dotenv
   load_dotenv()
   ```
3. Run: `pip install -r requirements.txt` then `python email_automation.py`
4. Check the terminal output and the Google Sheet — did rows get added correctly, in the right columns, with no duplicates?
5. Run it a second time immediately — confirm duplicates are correctly skipped.
6. Once it works locally, you can leave the `dotenv` lines in (they simply do nothing on GitHub Actions, since no `.env` file exists there — the secrets come from the `env:` block in the workflow instead).

---

## 12. Step 10 — Push and Verify on GitHub

1. Commit and push everything **except** your `.env` and the service account `.json` (already excluded via `.gitignore` — double check with `git status` before committing).
2. Go to your repo's **Actions** tab on GitHub.
3. Find "Email Lead Generation," click **Run workflow** (this is the `workflow_dispatch` trigger — lets you fire it manually instead of waiting for Monday).
4. Watch the run logs. Green checkmark = it ran clean. Red X = click into the logs to see the exact error.
5. Check the Sheet to confirm the same result as your local test.
6. Once confirmed, leave it alone — it'll now run automatically every Monday.

---

## 13. Credit Budgeting

- Apollo **search** = free.
- Apollo **enrichment/reveal** (the `bulk_match` call) = costs credits.
- At 15 confirmed leads/week with some enrichment attempts not returning an email, expect roughly **20-25 credits/week**, ~85-110/month.
- Compare this against your actual free-tier limit (checked in Step 4.2). If it's lower, reduce `TARGET_LEADS` or `SEARCH_BUFFER` in the script.

---

## 14. Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| `KeyError` on environment variable | Secret name mismatch between GitHub Secrets and script | Confirm exact spelling in both places |
| 403 from Apollo | Wrong endpoint or invalid key | Confirm using `mixed_people/api_search`, not `mixed_people/search` |
| Google Sheets permission error | Sheet not shared with service account email | Re-check Step 4 — share the sheet with the exact `client_email` |
| Workflow doesn't run on schedule | GitHub disables scheduled workflows on inactive repos after 60 days with no commits | Push occasionally, or trigger manually via `workflow_dispatch` |
| Duplicate rows appearing | Email comparison logic missing an edge case (casing, blank fields) | Check `existing_emails` set logic in the script |
| No new rows added | All revealed emails already in sheet, or Apollo returned no results | Check Apollo search filters aren't too narrow |

---

## 15. Weekly Maintenance

- [ ] Check Apollo's remaining credits monthly.
- [ ] Skim the Actions tab occasionally to make sure runs are succeeding (green, not red).
- [ ] Spot-check new rows for lead quality — tighten `person_titles`/`q_keywords` in the script if too many irrelevant leads show up.
