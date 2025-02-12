#!/usr/bin/env python3
import argparse
import datetime
import calendar
import os
import time
import json
from pathlib import Path
import numpy as np
import plotly.graph_objects as go
import colorsys
import requests

API_URL = "https://api.reporter.nih.gov/v2/projects/search"

class NIHReporterCache:
    def __init__(self, cache_dir="cache"):
        """Initialize cache in the specified directory."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
    
    def get_cache_path(self, year, month):
        """Get the path for a specific year-month cache file."""
        return self.cache_dir / f"grants_{year}_{month:02d}.json"
    
    def get_cached_data(self, year, month):
        """
        Retrieve cached data for a specific year and month.
        Returns None if no cache exists or if cache is invalid.
        """
        cache_path = self.get_cache_path(year, month)
        if not cache_path.exists():
            return None
            
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
                # Validate cache has required fields
                if not all(key in data for key in ['fetch_date', 'grants']):
                    return None
                # Parse the fetch date
                fetch_date = datetime.datetime.strptime(data['fetch_date'], 
                                                      "%Y-%m-%d").date()
                # Cache is valid for one week
                if (datetime.date.today() - fetch_date).days > 7:
                    return None
                return data['grants']
        except (json.JSONDecodeError, KeyError):
            return None
    
    def save_to_cache(self, year, month, grants):
        """Save grant data to cache with current fetch date."""
        cache_path = self.get_cache_path(year, month)
        data = {
            'fetch_date': datetime.date.today().strftime("%Y-%m-%d"),
            'grants': grants
        }
        with open(cache_path, 'w') as f:
            json.dump(data, f)

def get_pastel_color(i, total):
    """Generate a pastel color using HLS conversion."""
    hue = i / total  # Hue in [0, 1)
    lightness = 0.8
    saturation = 0.5
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"

def fetch_grants(start_date):
    """
    Query the NIH RePORTER API for projects with award_notice_date between start_date and end_date.
    Uses a limit of 500 (the maximum allowed) and paginates until all records are retrieved.
    Returns a list of project records.
    """
    # Get the first day of the next month for the end date
    next_month = start_date.replace(day=1)
    if start_date.month == 12:
        next_month = next_month.replace(year=start_date.year + 1, month=1)
    else:
        next_month = next_month.replace(month=start_date.month + 1)
    
    results = []
    offset = 0
    limit = 500  # API maximum supported limit
    
    while True:
        query = {
            "criteria": {
                "award_notice_date": {
                    "from_date": start_date.strftime("%Y-%m-%d"),
                    "to_date": next_month.strftime("%Y-%m-%d")
                }
            },
            "offset": offset,
            "limit": limit,
            "fields": ["award_notice_date"]
        }
        
        print(f"Query payload: {query}")  # Debug print
        
        try:
            response = requests.post(API_URL, json=query)
            print(f"Response status: {response.status_code}")  # Debug print
            print(f"Response headers: {response.headers}")  # Debug print
            print(f"Response content: {response.text[:500]}...")  # Debug print
            response.raise_for_status()
        except Exception as e:
            print(f"Error fetching data for {start_date} to {next_month}: {e}")
            break
            
        data = response.json()
        batch = data.get("results", [])
        total = data.get("meta", {}).get("total", 0)
        
        if offset == 0 and total > 15000:
            print(f"WARNING: Query for {start_date} returned {total} awards. This exceeds the maximum supported offset of 15000.")
        results.extend(batch)
        offset += limit
        if offset >= min(total, 15000):  # Don't try to fetch beyond 15000
            break
        time.sleep(0.1)
    return results

def fetch_grants_with_cache(start_date, cache):
    """
    Modified fetch_grants function that uses cache.
    Returns (grants, cache_status) where cache_status is 'hit' or 'miss'.
    """
    # Check cache first
    cached_data = cache.get_cached_data(start_date.year, start_date.month)
    if cached_data is not None:
        return cached_data, 'hit'
    
    # If not in cache, fetch from API
    grants = fetch_grants(start_date)
    
    # Save to cache
    cache.save_to_cache(start_date.year, start_date.month, grants)
    return grants, 'miss'

def fetch_all_grants_by_month(start_year, current_year, today):
    """
    For each year from start_year to current_year, query the API for grants awarded 
    from January 1st through today's month/day.
    Returns a dictionary mapping each award year to a list of day-of-year integers.
    """
    cache = NIHReporterCache()
    data_by_year = {}
    
    # Calculate total months
    total_months = sum(
        min(today.month, 12) 
        for year in range(start_year, current_year + 1)
    )

    current_month_index = 0
    for year in range(start_year, current_year + 1):
        month_limit = min(today.month, 12)
        
        for month in range(1, month_limit + 1):
            current_month_index += 1
            start_date = datetime.date(year, month, 1)
            print(f"[{current_month_index}/{total_months}] Fetching grants for {year}-{month:02d}...", end=' ')
            
            grants, cache_status = fetch_grants_with_cache(start_date, cache)
            count = len(grants)
            print(f"Fetched {count} grants ({cache_status}).")
            
            # Process the grants
            for grant in grants:
                award_date_str = grant.get("award_notice_date")
                if not award_date_str:
                    continue
                try:
                    dt = datetime.datetime.strptime(award_date_str, "%Y-%m-%dT%H:%M:%SZ").date()
                except Exception as e:
                    print(f"Warning: Could not parse award_notice_date '{award_date_str}': {e}")
                    continue
                if (dt.month, dt.day) > (today.month, today.day):
                    continue
                data_by_year.setdefault(dt.year, []).append(dt.timetuple().tm_yday)

    return data_by_year

def create_cumulative_counts(year_days, cutoff):
    """
    For each year, create an array for days 1 to cutoff and compute the cumulative count of awards.
    Uses a dummy reference year (2000) for x-axis labels.
    Returns a dict mapping each year to a tuple (dates_array, cumulative_counts).
    """
    dates_array = [
        (datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).strftime("%b %d")
        for i in range(cutoff)
    ]
    cum_data = {}
    for year, days in year_days.items():
        counts = np.zeros(cutoff)
        for d in days:
            if 1 <= d <= cutoff:
                counts[d - 1] += 1
        cum_counts = np.cumsum(counts)
        cum_data[year] = (dates_array, cum_counts)
    return cum_data

def plot_cumulative_data(cum_data, current_year, tick_interval=7, colors=None, output_filename="nih_awards"):
    """
    Plot cumulative awards (YTD) by award notice date using Plotly.
    Saves both interactive HTML and static PNG versions.
    """
    fig = go.Figure()

    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        if year == current_year:
            color = "#FF0000"  # Bright red
            line_width = 3
            dash = "solid"
        else:
            color = colors.get(year, "lightgray") if colors else "lightgray"
            line_width = 2
            dash = "dash"
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines",
                name=str(year),
                line=dict(color=color, width=line_width, dash=dash)
            )
        )

    # Display only every tick_interval-th label on the x-axis
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(tickmode="array", tickvals=tick_vals)

    fig.update_layout(
        title="Cumulative NIH Awards (YTD) by Award Notice Date",
        xaxis_title="Date (Month-Day)",
        yaxis_title="Cumulative Number of Awards"
    )

    # Save both formats with fixed filenames
    html_file = f"{output_filename}.html"
    png_file = f"{output_filename}.png"
    
    fig.write_html(html_file)
    fig.write_image(png_file, width=1200, height=800)
    
    print(f"Plots saved as {html_file} and {png_file}")

def main():
    parser = argparse.ArgumentParser(
        description=("Extract NIH RePORTER grant data (last 10 years, by day) using the API "
                     "and plot cumulative awards (YTD) by award notice date.")
    )
    parser.add_argument("--tick_interval", type=int, default=7,
                        help="Interval (in days) for x-axis tick labels. Default is 7.")
    args = parser.parse_args()

    today = datetime.date.today()
    cutoff = today.timetuple().tm_yday
    current_year = today.year

    # Use the last 10 years (current year plus the previous 9)
    start_year = current_year - 9

    print(f"Fetching grant data from {start_year} to {current_year} for days up to {today.month:02d}-{today.day:02d}...")
    data_by_year = fetch_all_grants_by_month(start_year, current_year, today)

    if not data_by_year:
        print("No grant data retrieved. Exiting.")
        return

    for year in sorted(data_by_year.keys()):
        print(f"Year {year}: {len(data_by_year[year])} awards processed.")

    cum_data = create_cumulative_counts(data_by_year, cutoff)

    # Generate pastel colors for non-current years
    non_current_years = [y for y in data_by_year.keys() if y != current_year]
    colors = {}
    total = len(non_current_years)
    for i, year in enumerate(non_current_years):
        colors[year] = get_pastel_color(i, total if total > 0 else 1)
    colors[current_year] = "#FF0000"  # Current year in bright red

    output_basename = "nih_awards"  # Fixed base filename
    
    print("Plotting the results...")
    plot_cumulative_data(cum_data, current_year, tick_interval=args.tick_interval,
                        colors=colors, output_filename=output_basename)

if __name__ == "__main__":
    main()