#!/usr/bin/env python3
"""
This script extracts NIH RePORTER grant data and plots cumulative counts and award amounts (YTD)
using data through today's date (even though RePORTER updates only weekly – see README for details).
Monthly queries are run (with caching) and if any month reaches the API limit (15,000 results) a warning is issued.
At the end, a CSV is generated (compressed with zstd) listing each grant's award date and grant number.
"""

import argparse
import datetime
import time
import json
from pathlib import Path
import requests
import numpy as np
import plotly.graph_objects as go
import colorsys

API_URL = "https://api.reporter.nih.gov/v2/projects/search"


class NIHReporterCache:
    def __init__(self, cache_dir="cache"):
        """Initialize cache in the specified directory."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def get_cache_path(self, year, month):
        """Get the cache file path for a specific year and month."""
        return self.cache_dir / f"grants_{year}_{month:02d}.json"

    def get_cached_data(self, year, month):
        """
        Retrieve cached data for a specific year and month.
        Bypass cache for the current and immediately previous month.
        """
        today = datetime.date.today()
        if (year == today.year and month in [today.month, today.month - 1]) or (
            today.month == 1 and year == today.year - 1 and month == 12
        ):
            return None

        cache_path = self.get_cache_path(year, month)
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
                if not all(key in data for key in ["fetch_date", "grants"]):
                    return None
                fetch_date = datetime.datetime.strptime(data["fetch_date"], "%Y-%m-%d").date()
                if (today - fetch_date).days > 7:
                    return None
                return data["grants"]
        except (json.JSONDecodeError, KeyError):
            return None

    def save_to_cache(self, year, month, grants):
        """Save grant data to cache along with the current fetch date."""
        cache_path = self.get_cache_path(year, month)
        data = {
            "fetch_date": datetime.date.today().strftime("%Y-%m-%d"),
            "grants": grants,
        }
        with open(cache_path, "w") as f:
            json.dump(data, f)


def get_pastel_color(i, total):
    """Generate a pastel color using HLS conversion."""
    hue = i / total
    lightness = 0.8
    saturation = 0.5
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"


def fetch_grants_by_award_date(start_date):
    """
    Query the NIH RePORTER API for projects with award_notice_date between start_date and the first day
    of the next month.
    """
    if start_date.month == 12:
        next_month = start_date.replace(year=start_date.year + 1, month=1, day=1)
    else:
        next_month = start_date.replace(month=start_date.month + 1, day=1)

    results = []
    offset = 0
    limit = 500

    while True:
        query = {
            "criteria": {
                "award_notice_date": {
                    "from_date": start_date.strftime("%Y-%m-%d"),
                    "to_date": next_month.strftime("%Y-%m-%d"),
                }
            },
            "offset": offset,
            "limit": limit,
            "sort_field": "award_notice_date",  # Explicitly sort by date
            "sort_order": "desc",  # Get newest first
            "fields": [
                "award_notice_date",
                "award_amount",
                "agency_ic_admin",
                "fiscal_year",
                "project_num",
                "contact_pi_name",
                "project_title",
                "organization_name",
            ],
        }
        print(f"Query payload (award date): {query}")
        try:
            response = requests.post(API_URL, json=query)
            print(f"Response status: {response.status_code}")
            response.raise_for_status()
        except Exception as e:
            print(f"Error fetching data for {start_date} to {next_month}: {e}")
            break

        data = response.json()
        batch = data.get("results", [])
        total = data.get("meta", {}).get("total", 0)

        # Debug logging to show date ranges
        if batch:
            try:
                first_date = datetime.datetime.strptime(batch[0].get("award_notice_date", ""), "%Y-%m-%dT%H:%M:%SZ").date()
                last_date = datetime.datetime.strptime(batch[-1].get("award_notice_date", ""), "%Y-%m-%dT%H:%M:%SZ").date()
                print(f"Batch grants sorted newest to oldest: {first_date} to {last_date}")
            except Exception:
                pass

        # Warn if the total exceeds the API offset limit.
        if offset == 0 and total >= 15000:
            print(
                f"WARNING: Query for {start_date} returned {total} awards. This may exceed the API limit (15,000)."
            )
        results.extend(batch)
        offset += limit
        if offset >= min(total, 15000):
            break
        time.sleep(0.1)
    return results


def fetch_grants_with_cache(start_date, cache, force_refresh=False):
    """Fetch grant data for a given month using award_notice_date criteria, with caching."""
    if force_refresh:
        cached_data = None
    else:
        cached_data = cache.get_cached_data(start_date.year, start_date.month)
    if cached_data is not None:
        return cached_data, "hit"
    grants = fetch_grants_by_award_date(start_date)
    cache.save_to_cache(start_date.year, start_date.month, grants)
    return grants, "miss"


def fetch_all_grants_by_month(start_year, current_year, cutoff_date, force_refresh=False, include_all_recent=False):
    """
    For each year from start_year to current_year, fetch monthly grant data (using award_notice_date)
    up to cutoff_date.month. Only awards with a date on or before cutoff_date are kept.
    If force_refresh is True, current year data will bypass the cache.
    """
    cache = NIHReporterCache()
    data_by_year_counts = {}
    data_by_year_amounts = {}
    ic_data_by_year = {}
    all_award_date_grants = {}
    monthly_warnings = {}
    current_ics = set()

    month_limit = cutoff_date.month
    
    # Use a dictionary to track grants by project_num to avoid duplicates
    seen_grants = {}
    
    # Process each month
    for year in range(start_year, current_year + 1):
        ic_data_by_year.setdefault(year, {})
        all_award_date_grants[year] = []
        for month in range(1, month_limit + 1):
            start_date = datetime.date(year, month, 1)
            
            # Only force refresh for the current year
            should_force_refresh = force_refresh and year == current_year
            
            print(f"Fetching grants for {year}-{month:02d}...", end=" ")
            grants, cache_status = fetch_grants_with_cache(start_date, cache, should_force_refresh)
            print(f"Fetched {len(grants)} grants ({cache_status})")
            
            valid_grants = [g for g in grants if g.get("award_notice_date")]
            valid_grants.sort(
                key=lambda grant: datetime.datetime.strptime(
                    grant.get("award_notice_date"), "%Y-%m-%dT%H:%M:%SZ"
                )
            )
            for grant in valid_grants:
                # Get project number to track duplicates
                project_num = grant.get("project_num", "")
                if project_num:
                    seen_grants[project_num] = grant
                
                award_date_str = grant.get("award_notice_date")
                try:
                    dt = datetime.datetime.strptime(award_date_str, "%Y-%m-%dT%H:%M:%SZ").date()
                except Exception as e:
                    print(f"Warning: Could not parse award_notice_date '{award_date_str}': {e}")
                    continue
                
                # Add to all_award_date_grants - THIS LINE WAS MISSING
                all_award_date_grants[year].append(grant)
                
                day_of_year = dt.timetuple().tm_yday
                data_by_year_counts.setdefault(dt.year, []).append(day_of_year)
                try:
                    amount = float(grant.get("award_amount", 0))
                except Exception:
                    amount = 0
                data_by_year_amounts.setdefault(dt.year, []).append((day_of_year, amount))
                ic_info = grant.get("agency_ic_admin", {})
                ic = ic_info.get("abbreviation", "Other") or "Other"
                current_ics.add(ic)
                # Update IC cumulative data for this day.
                if day_of_year in ic_data_by_year[year]:
                    current_counts = ic_data_by_year[year][day_of_year]["counts"]
                    current_amounts = ic_data_by_year[year][day_of_year]["amounts"]
                else:
                    current_counts = {}
                    current_amounts = {}
                current_counts[ic] = current_counts.get(ic, 0) + 1
                current_amounts[ic] = current_amounts.get(ic, 0) + amount
                ic_data_by_year[year][day_of_year] = {"counts": current_counts, "amounts": current_amounts}

    # After collecting all grants, sort them properly by award date within each year
    print("Sorting grants by award date...")
    for year in all_award_date_grants.keys():
        all_award_date_grants[year].sort(
            key=lambda grant: grant.get("award_notice_date", "")
        )
    
    # After sorting the raw grant data, now prepare the day-based data structures
    # Clear existing data structures to rebuild them from sorted grants
    data_by_year_counts = {}
    data_by_year_amounts = {}
    ic_data_by_year = {}
    
    # Process sorted grants into daily counts and amounts
    for year, grants in all_award_date_grants.items():
        ic_data_by_year.setdefault(year, {})
        
        for grant in grants:
            award_date_str = grant.get("award_notice_date")
            try:
                dt = datetime.datetime.strptime(award_date_str, "%Y-%m-%dT%H:%M:%SZ").date()
            except Exception as e:
                print(f"Warning: Could not parse award_notice_date '{award_date_str}': {e}")
                continue
                
            # Only include awards on or before the cutoff_date, unless include_all_recent is True
            if not include_all_recent and (dt.month, dt.day) > (cutoff_date.month, cutoff_date.day):
                continue
            
            # Now process the properly sorted grants
            day_of_year = dt.timetuple().tm_yday
            data_by_year_counts.setdefault(dt.year, []).append(day_of_year)
            try:
                amount = float(grant.get("award_amount", 0))
            except Exception:
                amount = 0
            data_by_year_amounts.setdefault(dt.year, []).append((day_of_year, amount))
            
            # Update IC data
            ic_info = grant.get("agency_ic_admin", {})
            ic = ic_info.get("abbreviation", "Other") or "Other"
            current_ics.add(ic)
            
            if day_of_year in ic_data_by_year[year]:
                current_counts = ic_data_by_year[year][day_of_year]["counts"]
                current_amounts = ic_data_by_year[year][day_of_year]["amounts"]
            else:
                current_counts = {}
                current_amounts = {}
            current_counts[ic] = current_counts.get(ic, 0) + 1
            current_amounts[ic] = current_amounts.get(ic, 0) + amount
            ic_data_by_year[year][day_of_year] = {"counts": current_counts, "amounts": current_amounts}
    
    validation_info = {"monthly_warnings": monthly_warnings}
    return data_by_year_counts, data_by_year_amounts, ic_data_by_year, current_ics, validation_info, all_award_date_grants

def create_cumulative_counts(data_counts, cutoff_day):
    """
    Create cumulative counts data for plotting.
    
    Args:
        data_counts: Dict mapping years to lists of days of year
        cutoff_day: Cutoff day of year (to ensure consistent x-axis)
    
    Returns:
        Dict mapping years to (date_array, cumulative_counts) tuples
    """
    print(f"Creating cumulative counts with cutoff day {cutoff_day}")
    result = {}
    
    # Check if any year's data exceeds the cutoff day
    max_day = cutoff_day
    for year, days in data_counts.items():
        if not days:
            continue
        year_max = max(days)
        if year_max > cutoff_day:
            print(f"WARNING: Year {year} has data up to day {year_max}, which exceeds cutoff {cutoff_day}")
            max_day = max(max_day, year_max)
    
    if max_day > cutoff_day:
        print(f"Using effective cutoff of {max_day} to avoid truncating data")
    else:
        max_day = cutoff_day
    
    # Generate date strings for x-axis - USE CURRENT YEAR INSTEAD OF HARDCODED 2023
    current_year = datetime.date.today().year
    date_strs = []
    for day in range(1, max_day + 1):
        # Use current year to get correct day of year conversions
        date = datetime.date(current_year, 1, 1) + datetime.timedelta(days=day-1)
        date_str = date.strftime("%b %d")
        date_strs.append(date_str)
    
    # Create cumulative counts for each year
    for year, days in data_counts.items():
        if not days:
            result[year] = (date_strs, [])
            continue
        
        # Count grants for each day
        counts = [0] * (max_day + 1)  # +1 because days are 1-indexed
        for day in days:
            if day <= max_day:  # Only count days up to the cutoff
                counts[day] += 1
        
        # Create cumulative sum, skipping day 0
        cum_counts = list(np.cumsum(counts[1:]))
        
        result[year] = (date_strs, cum_counts)
    
    return result


def create_cumulative_amounts(year_awards, cutoff):
    """
    Build cumulative award amount arrays (up to the cutoff day) for each year.
    Returns a dict mapping each year to a tuple (dates_array, cumulative_amounts).
    """
    # Find the maximum day across all years to ensure we don't truncate data
    max_day = cutoff
    for year, entries in year_awards.items():
        if entries:
            year_max = max(d for d, _ in entries)
            max_day = max(max_day, year_max)
            if year_max > cutoff:
                print(f"WARNING: Year {year} has data up to day {year_max}, which exceeds cutoff {cutoff}")
    
    effective_cutoff = max_day
    if effective_cutoff > cutoff:
        print(f"Using effective cutoff of {effective_cutoff} to avoid truncating data")
    
    dates_array = [
        (datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).strftime("%b %d")
        for i in range(effective_cutoff)
    ]
    cum_data = {}
    for year, entries in year_awards.items():
        amounts = np.zeros(effective_cutoff)
        for d, amt in entries:
            if 1 <= d <= effective_cutoff:
                amounts[d - 1] += amt
            else:
                print(f"WARNING: Day {d} for year {year} is outside range 1-{effective_cutoff}")
        cum_data[year] = (dates_array, np.cumsum(amounts))
    return cum_data

def plot_cumulative_data(cum_data, ic_data, current_ics, current_year, tick_interval=7, colors=None, output_filename="nih_awards", validation_info=None):
    """
    Plot cumulative NIH awards (YTD) by award notice date.
    The X-axis is set to show tick labels every `tick_interval` days (weekly by default).
    """
    fig = go.Figure()
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        custom_data = []
        for date_str in x:
            date_obj = datetime.datetime.strptime(f"{date_str} 2000", "%b %d %Y")
            day_of_year = date_obj.timetuple().tm_yday
            custom_data.append([year, day_of_year])
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors[year]  # use pastel color from the passed dictionary
            line_width = 2
            dash = "dash"
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                name=str(year),
                line=dict(color=color, width=line_width, dash=dash),
                customdata=custom_data,
            )
        )
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(
        tickmode="array", 
        tickvals=tick_vals,
        range=[0, len(full_x)-1]  # Force display of the entire x-axis range
    )
    
    # Add annotation for most recent Sunday
    today = datetime.date.today()
    # Find the most recent Sunday (go back until we find a Sunday)
    most_recent_sunday = today
    while most_recent_sunday.weekday() != 6:  # 6 = Sunday
        most_recent_sunday = most_recent_sunday - datetime.timedelta(days=1)
    
    # Convert to day of year
    sunday_day_of_year = most_recent_sunday.timetuple().tm_yday
    
    # Find the corresponding x-axis position (index in the date array)
    sunday_x_index = sunday_day_of_year - 1  # Adjust for zero-indexing
    
    if sunday_x_index < len(full_x):
        # Get the highest y-value across all years for positioning the arrow
        max_y_value = 0
        for year in cum_data:
            _, y_values = cum_data[year]
            if len(y_values) > sunday_x_index:
                max_y_value = max(max_y_value, y_values[sunday_x_index])
        
        # Add the annotation
        fig.add_annotation(
            x=full_x[sunday_x_index],
            y=max_y_value * 1.05,  # Place arrow slightly above the highest line
            text="Latest<br>RePORTER<br>update",
            showarrow=True,
            arrowhead=1,
            arrowsize=1.5,
            arrowwidth=2,
            arrowcolor="#000000",
            ax=0,
            ay=-40,
            font=dict(size=12, color="#000000"),
            bgcolor="#FFFFFF",
            bordercolor="#000000",
            borderwidth=1,
            borderpad=4,
            opacity=0.9
        )
    
    fig.update_layout(
        title="Cumulative NIH Awards (YTD) by Award Notice Date",
        xaxis_title="Date (Month-Day)",
        yaxis_title="Cumulative Number of Awards",
        clickmode="event",
        margin=dict(t=100, r=20, b=70, l=20),
    )
    html_file = f"{output_filename}.html"
    fig.write_html(html_file, full_html=True, include_plotlyjs="cdn")
    png_file = f"{output_filename}.png"
    fig.write_image(png_file, width=1200, height=800, scale=2)
    print(f"Count plots saved as {html_file} and {png_file}")

def plot_cumulative_amounts(cum_data, ic_data, current_ics, current_year, tick_interval=7, colors=None, output_filename="nih_award_amounts", validation_info=None):
    """
    Plot cumulative NIH award amounts (YTD) by award notice date.
    The X-axis shows tick labels every `tick_interval` days (weekly by default).
    """
    fig = go.Figure()
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        custom_data = []
        for date_str in x:
            date_obj = datetime.datetime.strptime(f"{date_str} 2000", "%b %d %Y")
            day_of_year = date_obj.timetuple().tm_yday
            custom_data.append([year, day_of_year])
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors[year]
            line_width = 2
            dash = "dash"
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                name=str(year),
                line=dict(color=color, width=line_width, dash=dash),
                customdata=custom_data,
            )
        )
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(
        tickmode="array", 
        tickvals=tick_vals,
        range=[0, len(full_x)-1]  # Force display of the entire x-axis range
    )
    
    # Add annotation for most recent Sunday
    today = datetime.date.today()
    # Find the most recent Sunday (go back until we find a Sunday)
    most_recent_sunday = today
    while most_recent_sunday.weekday() != 6:  # 6 = Sunday
        most_recent_sunday = most_recent_sunday - datetime.timedelta(days=1)
    
    # Convert to day of year
    sunday_day_of_year = most_recent_sunday.timetuple().tm_yday
    
    # Find the corresponding x-axis position (index in the date array)
    sunday_x_index = sunday_day_of_year - 1  # Adjust for zero-indexing
    
    if sunday_x_index < len(full_x):
        # Get the highest y-value across all years for positioning the arrow
        max_y_value = 0
        for year in cum_data:
            _, y_values = cum_data[year]
            if len(y_values) > sunday_x_index:
                max_y_value = max(max_y_value, y_values[sunday_x_index])
        
        # Add the annotation
        fig.add_annotation(
            x=full_x[sunday_x_index],
            y=max_y_value * 1.05,  # Place arrow slightly above the highest line
            text="Latest<br>RePORTER<br>update",
            showarrow=True,
            arrowhead=1,
            arrowsize=1.5,
            arrowwidth=2,
            arrowcolor="#000000",
            ax=0,
            ay=-40,
            font=dict(size=12, color="#000000"),
            bgcolor="#FFFFFF",
            bordercolor="#000000",
            borderwidth=1,
            borderpad=4,
            opacity=0.9
        )
    
    fig.update_layout(
        title="Cumulative NIH Award Amounts (YTD) by Award Notice Date",
        xaxis_title="Date (Month-Day)",
        yaxis_title="Cumulative Award Amount ($)",
        clickmode="event",
        margin=dict(t=100, r=20, b=70, l=20),
    )
    html_file = f"{output_filename}.html"
    fig.write_html(html_file, full_html=True, include_plotlyjs="cdn")
    png_file = f"{output_filename}.png"
    fig.write_image(png_file, width=1200, height=800, scale=2)
    print(f"Award amount plots saved as {html_file} and {png_file}")

def save_grants_list(all_award_date_grants, output_filename="nih_awards_all"):
    """
    Create a CSV containing a list of all grants (one row per grant) with two columns:
    award_date and grant_number. Then compress the CSV using zstd.
    """
    import pandas as pd
    import zstandard as zstd

    records = []
    for year, grants in all_award_date_grants.items():
        for grant in grants:
            award_date = grant.get("award_notice_date", "")
            grant_number = grant.get("project_num", "")
            # Format award_date to YYYY-MM-DD if possible.
            if award_date:
                try:
                    dt = datetime.datetime.strptime(award_date, "%Y-%m-%dT%H:%M:%SZ")
                    award_date = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
            records.append({"award_date": award_date, "grant_number": grant_number})
    
    # Sort the records by award_date
    df = pd.DataFrame(records)
    df.sort_values(by="award_date", inplace=True)
    
    csv_file = f"{output_filename}.csv"
    df.to_csv(csv_file, index=False)
    compressed_file = f"{csv_file}.zst"
    with open(csv_file, "rb") as f_in:
        data = f_in.read()
    cctx = zstd.ZstdCompressor(level=19)
    compressed = cctx.compress(data)
    with open(compressed_file, "wb") as f_out:
        f_out.write(compressed)
    print(f"Grants list saved and compressed as {compressed_file}")

def check_api_freshness():
    """Query the API for the most recent legitimate grant to check data freshness."""
    today = datetime.date.today()
    query = {
        "criteria": {},
        "limit": 100,  # Get more results to filter through
        "sort_field": "award_notice_date",
        "sort_order": "desc",
        "fields": ["award_notice_date", "project_num", "project_title", "agency_ic_admin"]
    }
    
    try:
        print("Checking API freshness...")
        response = requests.post(API_URL, json=query)
        response.raise_for_status()
        data = response.json()
        
        if data.get("results") and len(data["results"]) > 0:
            valid_grants = []
            
            # Filter out grants with impossible future dates
            for grant in data["results"]:
                if not grant.get("award_notice_date"):
                    continue
                    
                try:
                    dt = datetime.datetime.strptime(grant["award_notice_date"], "%Y-%m-%dT%H:%M:%SZ").date()
                    # Skip grants with dates in the future
                    if dt > today:
                        continue
                    valid_grants.append((dt, grant))
                except Exception as e:
                    print(f"Error parsing date: {e}")
                    continue
            
            if valid_grants:
                # Sort by date descending (most recent first)
                valid_grants.sort(reverse=True, key=lambda x: x[0])
                latest_date, latest_grant = valid_grants[0]
                
                ic = latest_grant.get("agency_ic_admin", {}).get("abbreviation", "Unknown")
                grant_num = latest_grant.get("project_num", "Unknown")
                title = latest_grant.get("project_title", "Unknown")
                
                print(f"Most recent grant in API: {latest_date} (IC: {ic}, Grant: {grant_num})")
                print(f"Title: {title}")
                return latest_date
            
        print("Could not determine API freshness - no valid results found")
        return None
        
    except Exception as e:
        print(f"Error checking API freshness: {e}")
        return None


def fetch_most_recent_grants(days_back=14):
    """
    Directly fetch the most recent grants without using month-based queries.
    This ensures we get the absolute latest grants regardless of month boundaries.
    """
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=days_back)
    
    results = []
    offset = 0
    limit = 500
    
    print(f"Fetching most recent grants (last {days_back} days)...")
    
    while True:
        query = {
            "criteria": {
                "award_notice_date": {
                    "from_date": start_date.strftime("%Y-%m-%d"),
                    "to_date": today.strftime("%Y-%m-%d"),
                }
            },
            "offset": offset,
            "limit": limit,
            "sort_field": "award_notice_date",
            "sort_order": "desc",
            "fields": [
                "award_notice_date",
                "award_amount",
                "agency_ic_admin",
                "fiscal_year",
                "project_num",
                "contact_pi_name",
                "project_title",
                "organization_name",
            ],
        }
        
        try:
            response = requests.post(API_URL, json=query)
            response.raise_for_status()
            data = response.json()
            batch = data.get("results", [])
            total = data.get("meta", {}).get("total", 0)
            
            if offset == 0:
                print(f"Found {total} grants in the last {days_back} days")
                
            results.extend(batch)
            offset += limit
            if offset >= min(total, 15000):
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"Error fetching recent grants: {e}")
            break
            
    return results


def validate_award_counts(data_counts, cum_data, current_year):
    """
    Validate that the number of awards in the cumulative plot matches the raw data count.
    This ensures that all awards are properly represented in the plot.
    
    Args:
        data_counts: Dict mapping years to lists of days of year for each award
        cum_data: Dict mapping years to (date_array, cumulative_counts) tuples
        current_year: The current year being analyzed
        
    Returns:
        bool: Whether the validation passed
    """
    if current_year not in data_counts or current_year not in cum_data:
        print(f"ERROR: Cannot validate data for year {current_year} - missing raw or cumulative data")
        return False
    
    # Count awards in raw data
    raw_count = len(data_counts[current_year])
    
    # Get final cumulative value from the plot data
    _, cum_values = cum_data[current_year]
    if len(cum_values) == 0:
        plot_count = 0
    else:
        plot_count = cum_values[-1]
    
    # Validate that counts match
    if raw_count == plot_count:
        print(f"✓ VALIDATION PASSED: Raw count ({raw_count}) matches plot count ({plot_count}) for year {current_year}")
        return True
    else:
        print(f"❌ VALIDATION FAILED: Raw count ({raw_count}) does NOT match plot count ({plot_count}) for year {current_year}")
        print(f"   Difference: {raw_count - plot_count} awards are missing from the plot")
        
        # Try to diagnose the issue
        days = sorted(data_counts[current_year])
        max_day = max(days) if days else 0
        cutoff_day = len(cum_values)
        
        if max_day > cutoff_day:
            print(f"   Possible cause: {max_day - cutoff_day} days were truncated due to cutoff_day ({cutoff_day})")
            print(f"   Highest day in data: {max_day}, cutoff used in plot: {cutoff_day}")
            
            # Count awards after the cutoff
            awards_after_cutoff = [d for d in days if d > cutoff_day]
            print(f"   Awards after cutoff day: {len(awards_after_cutoff)}")
        
        return False


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract NIH RePORTER grant data (last 10 years, by day) and plot cumulative counts and award amounts (YTD) "
            "using data through today's date. A compressed list of grants is also saved."
        )
    )
    parser.add_argument("--force-refresh", action="store_true",
                        help="Force refresh the current year's data, ignoring cache")
    parser.add_argument("--check-freshness", action="store_true",
                        help="Check API data freshness and exit")
    parser.add_argument("--include-all-recent", action="store_true",
                        help="Include all recent grants even if they're beyond today's date")
    args = parser.parse_args()
    
    if args.check_freshness:
        check_api_freshness()
        return
        
    # Use today's date for plotting
    today = datetime.date.today()
    current_year = today.year
    
    print(f"Today is {today} (day of year: {today.timetuple().tm_yday})")
    
    # Check API freshness
    api_freshness = check_api_freshness()
    if api_freshness:
        print(f"API freshness date: {api_freshness}")
        days_behind = (today - api_freshness).days
        if days_behind > 0:
            print(f"WARNING: API data appears to be {days_behind} days behind")
            # Use API freshness day of year as the cutoff to match available data
            cutoff_day = api_freshness.timetuple().tm_yday
        else:
            # API is current, use today's day of year
            cutoff_day = today.timetuple().tm_yday
    else:
        cutoff_day = today.timetuple().tm_yday
    
    print(f"Using data up to {today.strftime('%b %d, %Y')} (cutoff day: {cutoff_day}).")
    
    if args.force_refresh:
        print(f"Force refreshing data for the current year ({current_year})...")

    start_year = current_year - 9
    print(f"Fetching grant data from {start_year} to {current_year} for awards up to {today.month:02d}-{today.day:02d}...")
    data_counts, data_amounts, ic_data, current_ics, validation_info, all_award_date_grants = fetch_all_grants_by_month(
        start_year, current_year, today, args.force_refresh, args.include_all_recent
    )

    if not data_counts:
        print("No grant count data retrieved. Exiting.")
        return

    # Add debug output to check day ranges for the current year
    if current_year in data_counts:
        days = sorted(data_counts[current_year])
        print(f"Day range for {current_year}: {min(days)} to {max(days)}")
        print(f"Date range: {datetime.date(current_year, 1, 1) + datetime.timedelta(days=min(days)-1)} to "
              f"{datetime.date(current_year, 1, 1) + datetime.timedelta(days=max(days)-1)}")

    for year in sorted(data_counts.keys()):
        print(f"Year {year}: {len(data_counts[year])} awards processed (counts).")
    for year in sorted(data_amounts.keys()):
        print(f"Year {year}: {len(data_amounts[year])} awards processed (amounts).")

    print(f"Found {len(current_ics)} current ICs: {', '.join(sorted(current_ics))}")

    # After fetching all data, ensure proper sorting by date
    print("Sorting grant data by award date...")
    
    # Create consolidated lists of grants for each year, with proper dates
    sorted_data_counts = {}
    sorted_data_amounts = {}
    
    for year in data_counts.keys():
        # Create a list of (date_obj, day_of_year) pairs
        dated_entries = []
        for day_of_year in data_counts[year]:
            date_obj = datetime.date(year, 1, 1) + datetime.timedelta(days=day_of_year-1)
            dated_entries.append((date_obj, day_of_year))
        
        # Sort by actual date
        dated_entries.sort(key=lambda x: x[0])
        
        # Extract the sorted days of year
        sorted_data_counts[year] = [day for _, day in dated_entries]
    
    # Do the same for amount data
    for year in data_amounts.keys():
        dated_entries = []
        for day_of_year, amount in data_amounts[year]:
            date_obj = datetime.date(year, 1, 1) + datetime.timedelta(days=day_of_year-1)
            dated_entries.append((date_obj, day_of_year, amount))
        
        dated_entries.sort(key=lambda x: x[0])
        sorted_data_amounts[year] = [(day, amt) for _, day, amt in dated_entries]
    
    # Use the sorted data for plotting
    print("Creating cumulative data with properly sorted grants...")
    cum_counts = create_cumulative_counts(sorted_data_counts, cutoff_day)
    cum_amounts = create_cumulative_amounts(sorted_data_amounts, cutoff_day)

    # Validate that all awards are properly represented in the plot
    print("\nValidating award counts...")
    validation_passed = validate_award_counts(data_counts, cum_counts, current_year)
    if not validation_passed:
        print("ERROR: Validation failed - award counts in plot don't match raw data counts")
        print("Try running with a higher cutoff day or with --include-all-recent")
        # Uncomment to make the script exit on validation failure if needed
        # import sys
        # sys.exit(1)

    # Generate pastel colors for non-current years.
    non_current_years = [y for y in data_counts.keys() if y != current_year]
    colors = {}
    total = len(non_current_years)
    for i, year in enumerate(sorted(non_current_years)):
        colors[year] = get_pastel_color(i, total if total > 0 else 1)
    colors[current_year] = "#FF0000"

    print("Plotting cumulative count results...")
    plot_cumulative_data(
        cum_counts,
        ic_data,
        current_ics,
        current_year,
        tick_interval=7,
        colors=colors,
        output_filename="nih_awards",
        validation_info=validation_info,
    )

    print("Plotting cumulative award amount results...")
    plot_cumulative_amounts(
        cum_amounts,
        ic_data,
        current_ics,
        current_year,
        tick_interval=7,
        colors=colors,
        output_filename="nih_award_amounts",
        validation_info=validation_info,
    )

    print("Saving grants list (award_date and grant_number) and compressing...")
    save_grants_list(all_award_date_grants, output_filename="nih_awards_all")

    # Debug: Print counts of grants each day for March 2025
    if current_year in data_counts:
        march_days = {}
        for day in data_counts[current_year]:
            date_obj = datetime.date(current_year, 1, 1) + datetime.timedelta(days=day-1)
            if date_obj.month == 3:
                march_days[date_obj.day] = march_days.get(date_obj.day, 0) + 1
        
        print("\nGrants by day in March 2025:")
        for day, count in sorted(march_days.items()):
            print(f"March {day}: {count} grants")


if __name__ == "__main__":
    main()
