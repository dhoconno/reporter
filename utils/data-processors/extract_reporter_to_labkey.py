#!/usr/bin/env python3
"""
Uniqueness is determined by appl_id alone: any record whose appl_id
already exists in LabKey will be skipped.

Run the sync::

       python ihireporter_sync_labkey.py         # last 30 days
       python ihireporter_sync_labkey.py --force # back-fill 10 years
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import date, timedelta
from typing import Dict, List, Set, Tuple

import requests
from labkey.api_wrapper import APIWrapper
from labkey.exceptions import RequestError
from labkey.query import QueryFilter

# -------------------------- Configuration -------------------------- #

API_URL    = "https://api.reporter.nih.gov/v2/projects/search"
BATCH_SIZE = 500               # NIH API max
TEST_LIMIT = None              # set to small int for quick tests

LABKEY_DOMAIN      = "openresearch.labkey.com"
LABKEY_PROJECT     = "Reporter"       # container path
CONTEXT_PATH       = ""               # '' if root context
LABKEY_SCHEMA      = "lists"
LABKEY_LIST        = "nih_reporter"   # your list name

# ------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ihireporter_sync")


# ------------------------ NIH RePORTER API ------------------------- #

def fetch_batch(from_date: str, to_date: str, offset: int) -> Tuple[List[dict], int]:
    payload = {
        "criteria": {"award_notice_date": {"from_date": from_date, "to_date": to_date}},
        "limit": BATCH_SIZE,
        "offset": offset,
    }
    r = requests.post(API_URL, json=payload, timeout=30)
    r.raise_for_status()
    js = r.json()
    return js.get("results", []), js.get("meta", {}).get("total", 0)


def compress(seq: List[str] | None) -> str | None:
    return ";".join(x for x in seq if x) if seq else None


def map_record(rec: dict) -> Dict[str, object]:
    org = rec.get("organization") or {}
    fs = rec.get("full_study_section" ) or {}
    return {
        "appl_id":           rec.get("appl_id"),
        "fiscal_year":       rec.get("fiscal_year"),
        "subproject_id":     str(rec.get("subproject_id") or "0"),
        "project_serial_num":rec.get("project_serial_num"),
        "core_project_num":  rec.get("core_project_num"),
        "application_type_code": rec.get("award_type"),
        "activity_code":     rec.get("activity_code"),
        "project_num":       rec.get("project_num"),
        "project_title":     rec.get("project_title"),

        "study_section_name":fs.get("name"),
        "study_section_code":fs.get("srg_code"),

        "project_start_date":rec.get("project_start_date"),
        "project_end_date":  rec.get("project_end_date"),
        "award_notice_date": rec.get("award_notice_date"),

        "opportunity_number":rec.get("opportunity_number"),
        "award_amount":      rec.get("award_amount"),
        "cfda_code":         rec.get("cfda_code"),
        "funding_mechanism": rec.get("funding_mechanism"),

        "direct_cost":       rec.get("direct_cost_amt"),
        "indirect_cost":     rec.get("indirect_cost_amt"),
        "budget_start_date": rec.get("budget_start"),
        "budget_end_date":   rec.get("budget_end"),

        "project_detail_url":rec.get("project_detail_url"),

        "org_name":          org.get("org_name"),
        "org_city":          org.get("org_city") or org.get("city"),
        "org_state":         org.get("org_state"),
        "org_zipcode":       org.get("org_zipcode"),
        "org_country":       org.get("org_country"),
        "org_department":    org.get("dept_type") or org.get("org_department"),
        "org_duns":          compress(org.get("org_duns")),
        "org_uei":           compress(org.get("org_ueis")),
        "external_org_id":   org.get("external_org_id"),

        "cong_dist":         rec.get("cong_dist"),

        "rcdc_terms": compress(re.findall(r"<([^>]+)>", rec.get("terms") or "")),
        "project_terms": compress(
            [t.strip() for t in (rec.get("pref_terms") or "").split(";") if t.strip()]
        ),
        "pi_names": compress(
            [pi.get("full_name") for pi in (rec.get("principal_investigators") or [])]
        ),
        "officer_names": compress(
            [po.get("full_name") for po in (rec.get("program_officers") or [])]
        ),

        "covid_response": json.dumps(rec.get("covid_response") or {}),
        "date_added":     rec.get("date_added"),
    }


# -------------------- LabKey APIWrapper Helpers -------------------- #

def fetch_existing_appl_ids(api: APIWrapper, start_date: str, end_date: str) -> Set[int]:
    """
    Page through the LabKey list, grabbing appl_id from every row
    within the given date range.
    """
    existing: Set[int] = set()
    offset = 0
    page_size = 1000

    while True:
        # build two QueryFilter objects (these are AND’ed together)
        filters = [
            QueryFilter(
                "award_notice_date",
                start_date,
                QueryFilter.Types.DATE_GREATER_THAN_OR_EQUAL,
            ),
            QueryFilter(
                "award_notice_date",
                end_date,
                QueryFilter.Types.DATE_LESS_THAN_OR_EQUAL,
            ),
        ]

        resp = api.query.select_rows(
            LABKEY_SCHEMA,
            LABKEY_LIST,
            columns=["appl_id"],
            filter_array=filters,      # <-- use filter_array here
            offset=offset,
            max_rows=page_size,
        )

        rows = resp.get("rows", [])
        if not rows:
            break

        for r in rows:
            existing.add(int(r["appl_id"]))
        offset += len(rows)

    log.info(
        "Loaded %d existing appl_id values from date range %s to %s",
        len(existing),
        start_date,
        end_date,
    )
    return existing

def insert_new_rows(api: APIWrapper, rows: List[Dict[str, object]]) -> int:
    """
    Insert rows via APIWrapper.  Returns number of rows accepted.
    Logs and skips HTTP 409 duplicates.
    """
    try:
        resp = api.query.insert_rows(LABKEY_SCHEMA, LABKEY_LIST, rows)
        inserted = len(resp.get("rows", []))
        return inserted
    except RequestError as err:
        msg = str(err).lower()
        if "duplicate" in msg or "constraint" in msg:
            log.warning("Duplicate rows skipped: %s", err)
            return 0
        raise


# ------------------------------ Sync Loop ------------------------------ #

def run_sync(force: bool, dry_run: bool = False) -> None:
    # 1) get API key & init client
    api_key = os.environ.get("LABKEY_API_KEY")
    if not api_key:
        raise RuntimeError("LABKEY_API_KEY environment variable not set")

    # Create API client with proper API key authentication
    api = APIWrapper(
        domain=LABKEY_DOMAIN,
        container_path=LABKEY_PROJECT,
        context_path=CONTEXT_PATH,
        use_ssl=True,
        api_key=api_key,
    )
    
    log.info(f"Connecting to {LABKEY_DOMAIN}/{LABKEY_PROJECT} with API key authentication {'(DRY RUN)' if dry_run else ''}")
    try:
        # Simple test query to verify connection
        test = api.query.select_rows("core", "containers", max_rows=1)
        log.info("Connection successful")
    except Exception as e:
        log.error(f"Connection test failed: {e}")
        raise

    # 2) determine date range
    today = date.today()  # 2025-05-08
    start_date = date(today.year - 10, 1, 1) if force else today - timedelta(days=14)
    log.info("Sync window begins %s (force=%s)", start_date, force)
    
    # 3) Fetch existing appl_ids from LabKey
    s_str = start_date.strftime("%Y-%m-%d")
    e_str = today.strftime("%Y-%m-%d")
    log.info(f"Fetching existing records from LabKey")
    existing_ids = fetch_existing_appl_ids(api, s_str, e_str)
    
    # 4) Process directly with the actual date range instead of by month
    s_str = start_date.strftime("%Y-%m-%d")
    e_str = today.strftime("%Y-%m-%d")
    log.info(f"Processing window {s_str} → {e_str} directly from RePORTER")
    
    total_new = 0
    reporter_total = 0
    already_exists = 0
    offset = 0
    
    while True:
        batch, total = fetch_batch(s_str, e_str, offset)
        if offset == 0:
            # Log the total records in RePORTER for this date range
            reporter_total = total
            log.info(f"Found {reporter_total} records in RePORTER for this date range")
        
        if not batch:
            break
            
        to_insert = []
        for rec in batch:
            appl = int(rec.get("appl_id")) if rec.get("appl_id") else None
            if appl and appl in existing_ids:
                already_exists += 1
                continue
            to_insert.append(map_record(rec))
            existing_ids.add(appl)
            total_new += 1
            if TEST_LIMIT and total_new >= TEST_LIMIT:
                break
                
        # Process batch statistics
        if to_insert:
            if dry_run:
                log.info(f"  Would insert {len(to_insert)} rows (running {total_new}) [DRY RUN]")
                inserted = len(to_insert)
            else:
                inserted = insert_new_rows(api, to_insert)
                log.info(f"  +{inserted} rows (running {total_new})")
                
        if len(batch) < BATCH_SIZE or (TEST_LIMIT and total_new >= TEST_LIMIT):
            break
            
        offset += len(batch)
        time.sleep(0.4)
    
    # Final statistics
    log.info(f"RePORTER statistics for {s_str} → {e_str}:")
    log.info(f"  - Total records found in RePORTER: {reporter_total}")
    log.info(f"  - Already in LabKey: {already_exists}")
    log.info(f"  - New records added: {total_new}")
    log.info(f"Sync complete — {total_new} new rows {'would be' if dry_run else 'were'} inserted")
    
    return


# ---------------------------- Entrypoint ---------------------------- #

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Sync NIH RePORTER → LabKey nih_reporter list (unique by appl_id)."
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Back-fill ten years (instead of last 14 days)",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Simulate the sync without inserting records into LabKey",
    )
    args = p.parse_args()
    try:
        run_sync(force=args.force, dry_run=args.dry_run)
    except KeyboardInterrupt:
        log.warning("Interrupted by user; partial progress kept.")