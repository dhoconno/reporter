#!/usr/bin/env python3
"""
ihireporter_sync_labkey.py
========================================
Incrementally load NIH RePORTER project data into a **LabKey list**
(`Reporter / nih_reporter`) using the official *labkey* Python client
and an **API key** (no username/password).

-----------------------------------------------------------------------
Prerequisites
-----------------------------------------------------------------------
1. Install dependencies::

       pip install labkey requests

2. Set the following environment variable *once* for the session or in
   your shell startup file::

       export LABKEY_API_KEY="<your‑labkey‑personal‑access‑token>"

-----------------------------------------------------------------------
Behaviour
-----------------------------------------------------------------------
* Reads NIH RePORTER in 500‑record batches, windowed month‑by‑month.
* Inserts each batch with `labkey.query.insert_rows` (schema **lists**,
  query **nih_reporter**).
* Primary key in the LabKey list should be **(appl_id, fiscal_year)** to
  avoid duplicates (LabKey will reject duplicates automatically).
* Use `--force` to backfill the last ten years (otherwise syncs the last
  30 days by default).
* Safe to run repeatedly; duplicate rows are skipped by LabKey.

-----------------------------------------------------------------------
© 2025 — Dave O’Connor lab automation
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import re
import time
from datetime import date, timedelta
from typing import Dict, List, Tuple

import requests
from labkey.utils import create_server_context
from labkey.query import insert_rows, QueryResponse  # type: ignore [import-not-found]

# --------------------------- Configuration --------------------------- #

API_URL: str = "https://api.reporter.nih.gov/v2/projects/search"
BATCH_SIZE: int = 500          # NIH API max = 500
TEST_LIMIT: int | None = None  # small int for smoke‑testing

LABKEY_SERVER: str = "https://openresearch.labkey.com"
LABKEY_PROJECT: str = "Reporter"          # top‑level folder in LabKey
LABKEY_SCHEMA: str = "lists"
LABKEY_LIST: str = "nih_reporter"         # destination list name

# --------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ihireporter_sync")


# ------------------------- Helper functions -------------------------- #

def compress(seq: List[str] | None) -> str | None:
    """Semicolon‑join non‑empty strings; returns None for empty/None."""
    return ";".join(x for x in seq if x) if seq else None


def fetch_batch(from_date: str, to_date: str, offset: int) -> Tuple[List[dict], int]:
    """POST to NIH RePORTER and return (results, total_matches)."""
    payload = {
        "criteria": {"award_notice_date": {"from_date": from_date, "to_date": to_date}},
        "limit": BATCH_SIZE,
        "offset": offset,
    }
    resp = requests.post(API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    js = resp.json()
    return js.get("results", []), js.get("meta", {}).get("total", 0)


def map_record(rec: dict) -> Dict[str, object]:
    """Flatten a NIH RePORTER record to LabKey list‑row dict."""
    org = rec.get("organization") or {}
    return {
        "appl_id": rec.get("appl_id"),
        "fiscal_year": rec.get("fiscal_year"),
        "subproject_id": str(rec.get("subproject_id") or "0"),

        "project_serial_num": rec.get("project_serial_num"),
        "core_project_num": rec.get("core_project_num"),
        "application_type_code": rec.get("application_type_code"),
        "activity_code": rec.get("activity_code"),
        "suffix_code": rec.get("suffix_code"),
        "project_num": rec.get("project_num"),
        "project_title": rec.get("project_title"),

        "study_section_name": rec.get("study_section_name"),
        "study_section_code": rec.get("study_section"),

        "project_start_date": rec.get("project_start_date"),
        "project_end_date": rec.get("project_end_date"),
        "award_notice_date": rec.get("award_notice_date"),

        "opportunity_number": rec.get("opportunity_number"),
        "award_amount": rec.get("award_amount"),
        "cfda_code": rec.get("cfda_code"),
        "funding_mechanism": rec.get("funding_mechanism"),

        "direct_cost": rec.get("direct_cost_amt"),
        "indirect_cost": rec.get("indirect_cost_amt"),
        "budget_start_date": rec.get("budget_start"),
        "budget_end_date": rec.get("budget_end"),

        "project_detail_url": rec.get("project_detail_url"),

        "org_name": org.get("org_name"),
        "org_city": org.get("org_city") or org.get("city"),
        "org_state": org.get("org_state"),
        "org_zipcode": org.get("org_zipcode"),
        "org_country": org.get("org_country"),
        "org_department": org.get("dept_type") or org.get("org_department"),
        "org_duns": compress(org.get("org_duns")),
        "org_uei": compress(org.get("org_ueis")),
        "external_org_id": org.get("external_org_id"),

        "cong_dist": rec.get("cong_dist"),

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
        "date_added": rec.get("date_added"),
    }


# ------------------------------- Main -------------------------------- #

def run_sync(force: bool) -> None:
    """Main ETL loop."""
    api_key = os.environ.get("LABKEY_API_KEY")
    if not api_key:
        raise RuntimeError("LABKEY_API_KEY environment variable not set")

    # Create a reusable server context (keeps a requests.Session under the hood).
    ctx = create_server_context(
        LABKEY_SERVER, LABKEY_PROJECT, api_key=api_key, use_ssl=True
    )

    today = date.today()
    start_date = date(today.year - 10, 1, 1) if force else today - timedelta(days=30)
    log.info("Sync window begins %s (force=%s)", start_date, force)

    total_rows = 0
    current = start_date.replace(day=1)

    while current <= today and (TEST_LIMIT is None or total_rows < TEST_LIMIT):
        y, m = current.year, current.month
        window_end = min(date(y, m, calendar.monthrange(y, m)[1]), today)
        start_str, end_str = current.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d")
        log.info("Processing %s – %s", start_str, end_str)

        offset = 0
        window_rows = 0

        while True:
            batch, expected_total = fetch_batch(start_str, end_str, offset)
            if not batch:
                break

            rows: List[Dict[str, object]] = []
            for rec in batch:
                if TEST_LIMIT and total_rows >= TEST_LIMIT:
                    break
                rows.append(map_record(rec))
                total_rows += 1

            if rows:
                # insert_rows raises if *any* row violates constraints; catch & log
                try:
                    resp: QueryResponse = insert_rows(
                        ctx, LABKEY_SCHEMA, LABKEY_LIST, rows
                    )
                    window_rows += len(resp.rows_affected)
                    log.info("  +%d rows (total %d)", len(resp.rows_affected), total_rows)
                except Exception as exc:  # broad catch; LabKey returns rich exceptions
                    log.error("Insert failed at offset %d: %s", offset, exc)
                    raise

            if len(batch) < BATCH_SIZE or (TEST_LIMIT and total_rows >= TEST_LIMIT):
                break

            offset += len(batch)
            time.sleep(0.4)  # gentle pacing for both NIH and LabKey

        if expected_total and window_rows < expected_total:
            log.warning(
                "  Shortfall: expected %d rows, inserted %d", expected_total, window_rows
            )

        # advance to first day of next month
        current = date(y + (m // 12), (m % 12) + 1, 1)

    log.info("Sync complete — %d rows inserted", total_rows)


# ---------------------------- Entrypoint ----------------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Incrementally sync NIH RePORTER data into a LabKey list."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Backfill the last ten years (instead of 30 days).",
    )
    args = parser.parse_args()

    try:
        run_sync(force=args.force)
    except KeyboardInterrupt:
        log.warning("Interrupted by user; partial progress kept.")