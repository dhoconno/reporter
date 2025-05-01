#!/usr/bin/env python3
"""
ihireporter_sync.py — *wide-table incremental sync with integrity checks*
Fix: high false‑duplicate count.

Root cause: many records have `subproject_id=null`. Using NULL in composite PK causes every such row
(to the same `appl_id`, `fiscal_year`) to collide. We now:
• Cast `subproject_id` to string and default to "0" when missing.
• Primary key is **(appl_id, fiscal_year)** only (NIH guarantees one record per appl_id per FY).
• Extra logging on duplicate collisions prints the conflicting IDs the first few times.
"""

from __future__ import annotations
import argparse, calendar, json, logging, re, sqlite3, time
from datetime import date, timedelta
from typing import List, Tuple
import requests

API_URL    = "https://api.reporter.nih.gov/v2/projects/search"
DB_PATH    = "data/processed/reporter/nih_reporter.db"
BATCH_SIZE = 500
TEST_LIMIT = None          # set to small int for quick tests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    appl_id INTEGER NOT NULL,
    fiscal_year INTEGER NOT NULL,
    subproject_id TEXT NOT NULL,
    project_serial_num TEXT,
    core_project_num TEXT,
    application_type_code TEXT,
    activity_code TEXT,
    suffix_code TEXT,
    project_num TEXT,
    project_title TEXT,
    study_section_name TEXT,
    study_section_code TEXT,
    project_start_date TEXT,
    project_end_date TEXT,
    award_notice_date TEXT,
    opportunity_number TEXT,
    award_amount REAL,
    cfda_code TEXT,
    funding_mechanism TEXT,
    direct_cost REAL,
    indirect_cost REAL,
    budget_start_date TEXT,
    budget_end_date TEXT,
    project_detail_url TEXT,
    org_name TEXT,
    org_city TEXT,
    org_state TEXT,
    org_zipcode TEXT,
    org_country TEXT,
    org_department TEXT,
    org_duns TEXT,
    org_uei  TEXT,
    external_org_id TEXT,
    cong_dist TEXT,
    rcdc_terms TEXT,
    project_terms TEXT,
    pi_names TEXT,
    officer_names TEXT,
    covid_response TEXT,
    date_added TEXT,
    PRIMARY KEY (appl_id, fiscal_year)  -- unique per FY by NIH definition
);
CREATE INDEX IF NOT EXISTS idx_projects_fy ON projects(fiscal_year);
"""

COLS = (
    "appl_id,fiscal_year,subproject_id,project_serial_num,core_project_num,application_type_code,activity_code,suffix_code,"  # noqa
    "project_num,project_title,study_section_name,study_section_code,project_start_date,project_end_date,award_notice_date,"
    "opportunity_number,award_amount,cfda_code,funding_mechanism,direct_cost,indirect_cost,budget_start_date,budget_end_date,"
    "project_detail_url,org_name,org_city,org_state,org_zipcode,org_country,org_department,org_duns,org_uei,external_org_id,"
    "cong_dist,rcdc_terms,project_terms,pi_names,officer_names,covid_response,date_added"
)
PH = ",".join(["?"]*len(COLS.split(',')))

# ---------- DB helpers ---------- #

def init_db(conn: sqlite3.Connection, force: bool):
    if force:
        log.info("Dropping table for full rebuild …")
        conn.execute("DROP TABLE IF EXISTS projects;")
    conn.executescript(CREATE_SQL)
    conn.commit()

# ---------- API ---------- #

def fetch_batch(from_date: str, to_date: str, offset: int):
    payload = {
        "criteria": {"award_notice_date": {"from_date": from_date, "to_date": to_date}},
        "limit": BATCH_SIZE,
        "offset": offset,
    }
    r = requests.post(API_URL, json=payload, timeout=30)
    r.raise_for_status()
    js = r.json()
    return js.get("results", []), js.get("meta", {}).get("total", 0)

# ---------- insert ---------- #

def compress(seq):
    return ";".join(x for x in seq if x) if seq else None

def insert_batch(conn: sqlite3.Connection, batch: List[dict], ins: int, dups: int):
    cur = conn.cursor()
    for rec in batch:
        if TEST_LIMIT and ins >= TEST_LIMIT:
            break
        org = rec.get("organization", {}) or {}
        row = (
            rec.get("appl_id"), rec.get("fiscal_year"), str(rec.get("subproject_id") or "0"),
            rec.get("project_serial_num"), rec.get("core_project_num"), rec.get("application_type_code"), rec.get("activity_code"), rec.get("suffix_code"),
            rec.get("project_num"), rec.get("project_title"), rec.get("study_section_name"), rec.get("study_section"),
            rec.get("project_start_date"), rec.get("project_end_date"), rec.get("award_notice_date"), rec.get("opportunity_number"), rec.get("award_amount"),
            rec.get("cfda_code"), rec.get("funding_mechanism"), rec.get("direct_cost_amt"), rec.get("indirect_cost_amt"), rec.get("budget_start"), rec.get("budget_end"), rec.get("project_detail_url"),
            org.get("org_name"), org.get("org_city") or org.get("city"), org.get("org_state"), org.get("org_zipcode"), org.get("org_country"), org.get("dept_type") or org.get("org_department"),
            compress(org.get("org_duns")), compress(org.get("org_ueis")), org.get("external_org_id"), rec.get("cong_dist"),
            compress(re.findall(r"<([^>]+)>", rec.get("terms") or "")),
            compress([t.strip() for t in (rec.get("pref_terms") or "").split(';') if t.strip()]),
            compress([pi.get("full_name") for pi in (rec.get("principal_investigators") or [])]),
            compress([po.get("full_name") for po in (rec.get("program_officers") or [])]),
            json.dumps(rec.get("covid_response") or {}), rec.get("date_added")
        )
        cur.execute(f"INSERT OR IGNORE INTO projects ({COLS}) VALUES ({PH})", row)
        if cur.rowcount:
            ins += 1
        else:
            dups += 1
            if dups <= 5:
                log.debug("Duplicate skipped: appl_id=%s fiscal_year=%s", rec.get("appl_id"), rec.get("fiscal_year"))
    conn.commit()
    return ins, dups

# ---------- main ---------- #

def run_sync(force: bool):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn, force)
    today = date.today()
    start = date(today.year-10,1,1) if force else today - timedelta(days=30)
    log.info("Sync from %s (force=%s)", start, force)

    ins = dups = 0
    current = start.replace(day=1)
    while current <= today and (TEST_LIMIT is None or ins < TEST_LIMIT):
        y,m = current.year, current.month
        win_end = min(date(y,m,calendar.monthrange(y,m)[1]), today)
        s, e = current.strftime('%Y-%m-%d'), win_end.strftime('%Y-%m-%d')
        log.info("Window %s … %s", s, e)
        offset=0; win_added=0; total=None
        while True:
            batch,total = fetch_batch(s,e,offset)
            if not batch: break
            before = ins
            ins, dups = insert_batch(conn,batch,ins,dups)
            win_added += ins-before
            log.info("   offset %d → added %d dup %d", offset, ins-before, dups)
            if len(batch)<BATCH_SIZE or (TEST_LIMIT and ins>=TEST_LIMIT): break
            offset += len(batch)
            time.sleep(0.4)
        if total and win_added<total:
            log.warning("   window shortfall: expected %d got %d", total, win_added)
        current = date(y + (m//12), (m%12)+1, 1)
    log.info("Done. inserted=%d dup=%d", ins, dups)
    conn.close()

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--force',action='store_true',help='drop table & full backfill')
    args=parser.parse_args()
    run_sync(force=args.force)
