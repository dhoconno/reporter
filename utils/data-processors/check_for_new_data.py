#!/usr/bin/env python3
"""
Lightweight script to check if new data is available from NIH Reporter,
Federal Register, or TAGGS without doing full processing.

This script is designed to run quickly (< 1 minute) and output which
data sources have updates, so the GitHub Actions workflow can decide
whether to run the heavy processing jobs.

Exit codes:
  0 = New data available (outputs which sources)
  1 = No new data available
  2 = Error occurred
"""

import requests
import json
import os
import sys
import datetime
import hashlib
from pathlib import Path

# Data source output files
REPORTER_CSV = "data/processed/reporter/nih_awards_all.csv.zst"
FEDERAL_REGISTER_CSV = "data/processed/federal_register/nih_fr_meetings_all.csv.zst"
TAGGS_CSV = "data/processed/taggs/hhs_grants_terminated.csv"

# API endpoints
NIH_REPORTER_API = "https://api.reporter.nih.gov/v2/projects/search"
FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents"
TAGGS_PDF_URL = "https://taggs.hhs.gov/Content/Data/HHS_Grants_Terminated.pdf"

# Metadata file to track last known state
METADATA_FILE = "cache/last_check_metadata.json"


def load_metadata():
    """Load metadata from previous check."""
    try:
        with open(METADATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_metadata(metadata):
    """Save metadata for next check."""
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)


def check_nih_reporter():
    """
    Check NIH Reporter for new grants by querying the most recent award date.
    Returns (has_new_data, latest_date_str, message)
    """
    today = datetime.date.today()
    query = {
        "criteria": {},
        "limit": 50,
        "sort_field": "award_notice_date",
        "sort_order": "desc",
        "fields": ["award_notice_date", "project_num"]
    }

    try:
        response = requests.post(NIH_REPORTER_API, json=query, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data.get("results"):
            return False, None, "No results from API"

        # Find the most recent valid grant (not in the future)
        for grant in data["results"]:
            award_date = grant.get("award_notice_date")
            if not award_date:
                continue

            # Parse the date (format: YYYY-MM-DDTHH:MM:SS)
            try:
                dt = datetime.datetime.fromisoformat(award_date.replace("Z", "+00:00"))
                grant_date = dt.date()

                if grant_date <= today:
                    return True, grant_date.isoformat(), f"Latest grant: {grant_date}"
            except ValueError:
                continue

        return False, None, "No valid dates found"

    except requests.RequestException as e:
        return None, None, f"API error: {e}"


def check_federal_register():
    """
    Check Federal Register for new NIH meeting notices.
    Returns (has_new_data, latest_date_str, message)
    """
    today = datetime.date.today()

    # Query for recent NIH notices
    params = {
        "conditions[agencies][]": "national-institutes-of-health",
        "conditions[term]": "Notice of Closed Meeting",
        "conditions[publication_date][gte]": (today - datetime.timedelta(days=30)).isoformat(),
        "per_page": 10,
        "order": "newest"
    }

    try:
        response = requests.get(FEDERAL_REGISTER_API, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = data.get("results", [])
        if not results:
            return False, None, "No recent notices"

        # Get the most recent publication date
        latest = results[0]
        pub_date = latest.get("publication_date")
        doc_num = latest.get("document_number", "unknown")

        return True, pub_date, f"Latest notice: {pub_date} ({doc_num})"

    except requests.RequestException as e:
        return None, None, f"API error: {e}"


def check_taggs():
    """
    Check TAGGS PDF for updates by checking HTTP headers (ETag/Last-Modified).
    Returns (has_new_data, etag_or_date, message)
    """
    try:
        # Use HEAD request to check headers without downloading
        response = requests.head(TAGGS_PDF_URL, timeout=30, allow_redirects=True)
        response.raise_for_status()

        # Check for ETag or Last-Modified
        etag = response.headers.get("ETag", "").strip('"')
        last_modified = response.headers.get("Last-Modified")
        content_length = response.headers.get("Content-Length")

        # Create a composite identifier
        identifier = f"{etag}|{last_modified}|{content_length}"

        return True, identifier, f"PDF headers: ETag={etag}, Modified={last_modified}"

    except requests.RequestException as e:
        return None, None, f"Request error: {e}"


def main():
    print("=" * 60)
    print("Checking for new data...")
    print(f"Current time: {datetime.datetime.now().isoformat()}")
    print("=" * 60)

    # Load previous check metadata
    metadata = load_metadata()
    sources_with_updates = []
    new_metadata = {"check_time": datetime.datetime.now().isoformat()}

    # Check NIH Reporter
    print("\n[1/3] Checking NIH Reporter API...")
    has_new, latest, msg = check_nih_reporter()
    print(f"  {msg}")

    if has_new is None:
        print("  ERROR: Could not check NIH Reporter")
    else:
        new_metadata["reporter_latest"] = latest
        prev_latest = metadata.get("reporter_latest")
        if prev_latest != latest:
            print(f"  NEW DATA: Previous={prev_latest}, Current={latest}")
            sources_with_updates.append("reporter")
        else:
            print(f"  No change (still {latest})")

    # Check Federal Register
    print("\n[2/3] Checking Federal Register API...")
    has_new, latest, msg = check_federal_register()
    print(f"  {msg}")

    if has_new is None:
        print("  ERROR: Could not check Federal Register")
    else:
        new_metadata["federal_register_latest"] = latest
        prev_latest = metadata.get("federal_register_latest")
        if prev_latest != latest:
            print(f"  NEW DATA: Previous={prev_latest}, Current={latest}")
            sources_with_updates.append("federal_register")
        else:
            print(f"  No change (still {latest})")

    # Check TAGGS
    print("\n[3/3] Checking TAGGS PDF...")
    has_new, identifier, msg = check_taggs()
    print(f"  {msg}")

    if has_new is None:
        # If we can't check TAGGS, assume it might have updates (fail-safe)
        print("  ERROR: Could not check TAGGS - assuming updates available")
        sources_with_updates.append("taggs")
    else:
        new_metadata["taggs_identifier"] = identifier
        prev_identifier = metadata.get("taggs_identifier")
        if prev_identifier != identifier:
            print(f"  NEW DATA: PDF has changed")
            sources_with_updates.append("taggs")
        else:
            print(f"  No change")

    # Save metadata for next run
    save_metadata(new_metadata)

    # Output results
    print("\n" + "=" * 60)
    if sources_with_updates:
        print(f"RESULT: New data available from: {', '.join(sources_with_updates)}")
        # Output for GitHub Actions
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write(f"has_updates=true\n")
                f.write(f"update_reporter={'true' if 'reporter' in sources_with_updates else 'false'}\n")
                f.write(f"update_federal_register={'true' if 'federal_register' in sources_with_updates else 'false'}\n")
                f.write(f"update_taggs={'true' if 'taggs' in sources_with_updates else 'false'}\n")
                f.write(f"sources={','.join(sources_with_updates)}\n")
        print("=" * 60)
        return 0
    else:
        print("RESULT: No new data available")
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as f:
                f.write("has_updates=false\n")
                f.write("update_reporter=false\n")
                f.write("update_federal_register=false\n")
                f.write("update_taggs=false\n")
                f.write("sources=\n")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(2)
