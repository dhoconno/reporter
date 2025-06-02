#!/usr/bin/env python3
"""
Uniqueness is determined by appl_id alone: any record whose appl_id
already exists in LabKey will be skipped.

Run the sync::

       python extract_reporter_to_labkey.py         # last 14 days
       python extract_reporter_to_labkey.py --force # back-fill 10 years
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import time
import traceback
from datetime import date, datetime, timedelta
from typing import Dict, List, Set, Tuple, Any

import requests
from labkey.api_wrapper import APIWrapper
from labkey.exceptions import RequestError
from labkey.query import QueryFilter

# -------------------------- Configuration -------------------------- #

API_URL    = "https://api.reporter.nih.gov/v2/projects/search"
BATCH_SIZE = 500               # NIH API max
TEST_LIMIT = None              # set to small int for quick tests

# LABKEY_DOMAIN      = "openresearch.labkey.com"
# LABKEY_PROJECT     = "Reporter"       # container path
# CONTEXT_PATH       = ""               # '' if root context
# LABKEY_SCHEMA      = "lists"
# LABKEY_LIST        = "nih_reporter"   # your list name

LABKEY_DOMAIN      = "dholk.primate.wisc.edu"
LABKEY_PROJECT     = "dho/public/reporter"       # container path
CONTEXT_PATH       = ""               # '' if root context
LABKEY_SCHEMA      = "lists"
LABKEY_LIST        = "nih_reporter"   # your list name

# Add error output directory
ERROR_OUTPUT_DIR = "error_records"

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
    try:
        r = requests.post(API_URL, json=payload, timeout=30)
        r.raise_for_status()
        js = r.json()
        return js.get("results", []), js.get("meta", {}).get("total", 0)
    except requests.RequestException as e:
        log.error(f"RePORTER API error: {e}")
        if hasattr(e, 'response') and e.response:
            log.error(f"Response status: {e.response.status_code}")
            log.error(f"Response content: {e.response.text[:500]}")
        raise


def compress(seq: List[str] | None) -> str | None:
    return ";".join(x for x in seq if x) if seq else None


def map_record(rec: dict) -> Dict[str, object]:
    org = rec.get("organization") or {}
    fs = rec.get("full_study_section") or {}
    admin_ic_obj = rec.get("agency_ic_admin") or {}
    funding_ics_list = rec.get("agency_ic_fundings") or []
    
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

        # Add NIH institute/center fields
        "administering_ic": admin_ic_obj.get("abbreviation") or admin_ic_obj.get("abbreviation"),
        "funding_ics": compress([ic.get("abbreviation") for ic in funding_ics_list]),

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
        # build two QueryFilter objects (these are AND'ed together)
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

        try:
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
            
        except Exception as e:
            log.error(f"Error fetching existing appl_ids: {e}")
            log.error(traceback.format_exc())
            raise

    log.info(
        "Loaded %d existing appl_id values from date range %s to %s",
        len(existing),
        start_date,
        end_date,
    )
    return existing

def insert_new_rows(api: APIWrapper, rows: List[Dict[str, object]]) -> Tuple[int, List[Dict[str, object]]]:
    """
    Insert rows via APIWrapper.  
    Returns tuple of (number of rows accepted, list of failed rows).
    If batch insert fails, attempts individual record uploads.
    """
    failed_rows = []
    
    # No rows to insert
    if not rows:
        return 0, failed_rows
        
    try:
        resp = api.query.insert_rows(LABKEY_SCHEMA, LABKEY_LIST, rows)
        inserted = len(resp.get("rows", []))
        return inserted, failed_rows
    except RequestError as err:
        msg = str(err).lower()
        log.warning(f"Batch insert failed: {err}")
        if hasattr(err, 'response') and err.response:
            log.warning(f"Response content: {err.response.text[:500]}")
        
        # Try inserting records individually to minimize failed records
        log.info(f"Attempting individual record insertion for {len(rows)} records...")
        total_inserted = 0
        
        for record in rows:
            try:
                single_resp = api.query.insert_rows(LABKEY_SCHEMA, LABKEY_LIST, [record])
                total_inserted += 1
            except Exception as e:
                # This individual record failed, add to failed_rows
                log.debug(f"Individual record insert failed: {e}")
                failed_rows.append(record)
        
        log.info(f"Individual insertion complete: {total_inserted} succeeded, {len(failed_rows)} failed")
        return total_inserted, failed_rows

def write_failed_records_to_csv(records: List[Dict[str, Any]], date_range: str) -> str:
    """Write failed records to a CSV file for manual processing"""
    if not records:
        return None
        
    # Create output directory if it doesn't exist
    os.makedirs(ERROR_OUTPUT_DIR, exist_ok=True)
    
    # Create filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ERROR_OUTPUT_DIR}/failed_records_{date_range}_{timestamp}.csv"
    
    # Get field names from the first record
    fieldnames = list(records[0].keys())
    
    try:
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.DictWriter(
                csvfile, 
                fieldnames=fieldnames,
                quoting=csv.QUOTE_NONNUMERIC,  # Quote all non-numeric fields
                quotechar='"',                 # Use double quotes
                escapechar='\\'                # Escape character for quotes within fields
            )
            writer.writeheader()
            for record in records:
                # Handle None values to avoid type errors
                cleaned_record = {k: ('' if v is None else v) for k, v in record.items()}
                writer.writerow(cleaned_record)
        
        log.info(f"Wrote {len(records)} failed records to {filename}")
        return filename
    except Exception as e:
        log.error(f"Error writing failed records to CSV: {e}")
        log.error(traceback.format_exc())  # Add full traceback for CSV writing errors
        return None

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
        log.error(traceback.format_exc())
        raise

    # 2) determine date range
    today = date.today()
    
    # New: Collect all failed records across the entire run
    all_failed_records = []
    
    if not force:
        # Normal mode: process last 14 days in one query
        start_date = today - timedelta(days=14)
        s_str = start_date.strftime("%Y-%m-%d")
        e_str = today.strftime("%Y-%m-%d")
        log.info(f"Sync window begins {s_str} (normal mode)")
        
        # Fetch existing records from LabKey for deduplication
        log.info(f"Fetching existing records from LabKey for date range {s_str} to {e_str}")
        existing_ids = fetch_existing_appl_ids(api, s_str, e_str)
        
        # Process the date range directly and collect failed records
        reporter_total, inserted_total, failed_records = process_date_range(api, s_str, e_str, existing_ids, dry_run)
        all_failed_records.extend(failed_records)
    else:
        # Force mode: process 10 years month by month to avoid offset limitations
        start_date = date(today.year - 10, 1, 1)
        log.info(f"Sync window begins {start_date.strftime('%Y-%m-%d')} (force mode - processing month by month)")
        
        grand_total_expected = 0
        grand_total_inserted = 0
        grand_total_failed = 0
        current = start_date.replace(day=1)
        
        # Process each month separately
        while current <= today:
            y, m = current.year, current.month
            # Calculate end of month
            if m == 12:
                next_month = date(y + 1, 1, 1)
            else:
                next_month = date(y, m + 1, 1)
            end_date = min(next_month - timedelta(days=1), today)
            
            s_str = current.strftime("%Y-%m-%d")
            e_str = end_date.strftime("%Y-%m-%d")
            
            log.info(f"Processing month {s_str} to {e_str}")
            expected, inserted, failed_records = process_month(api, s_str, e_str, dry_run)
            
            # Add this month's failed records to the overall collection
            all_failed_records.extend(failed_records)
            
            grand_total_expected += expected
            grand_total_inserted += inserted
            grand_total_failed += len(failed_records)
            
            # Advance to next month
            current = next_month
            
        log.info(f"Force sync complete summary:")
        log.info(f"- Total records expected from RePORTER: {grand_total_expected}")
        log.info(f"- Total records inserted into LabKey: {grand_total_inserted}")
        log.info(f"- Total records failed: {grand_total_failed}")
            
        # Calculate percentage
        if grand_total_expected > 0:
            pct = (grand_total_inserted / grand_total_expected) * 100
            log.info(f"- Sync completion: {pct:.2f}% of expected records inserted")
    
    # Write all failed records to a single CSV at the end
    if all_failed_records and not dry_run:
        # Create a single date range string for the filename
        if force:
            date_range = f"10yr_{start_date.strftime('%Y%m%d')}_to_{today.strftime('%Y%m%d')}"
        else:
            date_range = f"14d_{start_date.strftime('%Y%m%d')}_to_{today.strftime('%Y%m%d')}"
            
        csv_file = write_failed_records_to_csv(all_failed_records, date_range)
        log.info(f"All {len(all_failed_records)} failed records written to {csv_file} for manual import")


def process_date_range(api, start_date, end_date, existing_ids, dry_run):
    """Process a specific date range with duplicate checking against existing_ids"""
    total_new = 0
    total_failed = 0
    reporter_total = 0
    already_exists = 0
    offset = 0
    all_failed_records = []
    
    while True:
        try:
            batch, total = fetch_batch(start_date, end_date, offset)
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
                if TEST_LIMIT and total_new + len(to_insert) >= TEST_LIMIT:
                    break
                    
            # Process batch statistics
            if to_insert:
                if dry_run:
                    log.info(f"  Would insert {len(to_insert)} rows (running {total_new + len(to_insert)}) [DRY RUN]")
                    inserted = len(to_insert)
                    failed_rows = []
                else:
                    inserted, failed_rows = insert_new_rows(api, to_insert)
                    if failed_rows:
                        log.warning(f"  Failed to insert {len(failed_rows)} rows in this batch")
                        all_failed_records.extend(failed_rows)
                    
                    log.info(f"  +{inserted} rows inserted (running total: {total_new + inserted})")
                    
                total_new += inserted
                    
            if len(batch) < BATCH_SIZE or (TEST_LIMIT and total_new >= TEST_LIMIT):
                break
                
            offset += len(batch)
            time.sleep(0.4)
        
        except Exception as e:
            log.error(f"Error processing batch at offset {offset}: {e}")
            log.error(traceback.format_exc())
            # Continue with the next batch
            offset += BATCH_SIZE
            time.sleep(1)  # Longer pause after error
    
    # Final statistics
    log.info(f"RePORTER statistics for {start_date} → {end_date}:")
    log.info(f"  - Total records found in RePORTER: {reporter_total}")
    log.info(f"  - Already in LabKey: {already_exists}")
    log.info(f"  - New records added: {total_new}")
    log.info(f"  - Failed records: {len(all_failed_records)}")
    
    if reporter_total > 0:
        expected_new = reporter_total - already_exists
        pct = (total_new / expected_new) * 100 if expected_new > 0 else 100
        log.info(f"  - Sync completion: {pct:.2f}% of expected new records inserted")
    
    log.info(f"Sync complete — {total_new} new rows {'would be' if dry_run else 'were'} inserted")
    
    return reporter_total, total_new, all_failed_records


def process_month(api, start_date, end_date, dry_run):
    """Process a single month without checking for duplicates in LabKey"""
    total_inserted = 0
    reporter_total = 0
    offset = 0
    all_failed_records = []
    
    while True:
        try:
            batch, total = fetch_batch(start_date, end_date, offset)
            if offset == 0:
                reporter_total = total
                log.info(f"Found {reporter_total} records in RePORTER for this month")
            
            if not batch:
                break
                
            # Don't check for duplicates in force mode - let LabKey handle it
            to_insert = [map_record(rec) for rec in batch]
            
            # Process batch
            if to_insert:
                if dry_run:
                    log.info(f"  Would insert {len(to_insert)} rows (running total: {total_inserted + len(to_insert)}) [DRY RUN]")
                    inserted = len(to_insert)
                    failed_rows = []
                else:
                    inserted, failed_rows = insert_new_rows(api, to_insert)
                    if failed_rows:
                        log.warning(f"  Failed to insert {len(failed_rows)} rows in this batch")
                        all_failed_records.extend(failed_rows)
                    
                    log.info(f"  +{inserted} rows inserted (running total: {total_inserted + inserted})")
                
                total_inserted += inserted
                    
            if len(batch) < BATCH_SIZE or offset + len(batch) >= 14999:
                # Stop if we've reached the API limit or have all records
                break
                
            offset += len(batch)
            time.sleep(0.4)  # Be nice to the API
        
        except Exception as e:
            log.error(f"Error processing batch at offset {offset}: {e}")
            log.error(traceback.format_exc())
            # Continue with the next batch
            offset += BATCH_SIZE
            time.sleep(1)  # Longer pause after error
    
    # Final statistics
    log.info(f"Month completed: {reporter_total} found, {total_inserted} inserted, {len(all_failed_records)} failed")
    if reporter_total > 0:
        pct = (total_inserted / reporter_total) * 100
        log.info(f"Month completion rate: {pct:.2f}%")
    
    return reporter_total, total_inserted, all_failed_records


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
    except Exception as e:
        log.error(f"Unhandled exception: {e}")
        log.error(traceback.format_exc())