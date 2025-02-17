#!/usr/bin/env python3
import argparse
import datetime
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
        Always bypass cache for the current month.
        """
        today = datetime.date.today()
        # Always update cache for the current month.
        if year == today.year and month == today.month:
            return None

        cache_path = self.get_cache_path(year, month)
        if not cache_path.exists():
            return None
            
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
                if not all(key in data for key in ['fetch_date', 'grants']):
                    return None
                fetch_date = datetime.datetime.strptime(data['fetch_date'], "%Y-%m-%d").date()
                if (today - fetch_date).days > 7:
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
    hue = i / total
    lightness = 0.8
    saturation = 0.5
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"

def fetch_grants(start_date):
    """
    Query the NIH RePORTER API for projects with award_notice_date between start_date and end_date.
    Includes both award_notice_date and award_amount fields.
    """
    # Set end_date as the first day of the next month.
    next_month = start_date.replace(day=1)
    if start_date.month == 12:
        next_month = next_month.replace(year=start_date.year + 1, month=1)
    else:
        next_month = next_month.replace(month=start_date.month + 1)
    
    results = []
    offset = 0
    limit = 500
    
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
            "fields": ["award_notice_date", "award_amount"]
        }
        
        print(f"Query payload: {query}")
        try:
            response = requests.post(API_URL, json=query)
            print(f"Response status: {response.status_code}")
            print(f"Response headers: {response.headers}")
            print(f"Response content: {response.text[:500]}...")
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
        if offset >= min(total, 15000):
            break
        time.sleep(0.1)
    return results

def fetch_grants_with_cache(start_date, cache):
    """Fetch grant data using cache if available."""
    cached_data = cache.get_cached_data(start_date.year, start_date.month)
    if cached_data is not None:
        return cached_data, 'hit'
    grants = fetch_grants(start_date)
    cache.save_to_cache(start_date.year, start_date.month, grants)
    return grants, 'miss'

def fetch_all_grants_by_month(start_year, current_year, cutoff_date):
    """
    For each year from start_year to current_year, fetch grants from January up to cutoff_date.month.
    Only awards with a date on or before cutoff_date (e.g. the most recent Monday) are kept.
    Returns two dictionaries:
      - data_by_year_counts: year -> list of day-of-year integers.
      - data_by_year_amounts: year -> list of tuples (day-of-year, award_amount).
    """
    cache = NIHReporterCache()
    data_by_year_counts = {}
    data_by_year_amounts = {}
    
    month_limit = cutoff_date.month  # Fetch data for months 1 to cutoff_date.month.
    for year in range(start_year, current_year + 1):
        for month in range(1, month_limit + 1):
            start_date = datetime.date(year, month, 1)
            print(f"Fetching grants for {year}-{month:02d}...", end=' ')
            grants, cache_status = fetch_grants_with_cache(start_date, cache)
            print(f"Fetched {len(grants)} grants ({cache_status}).")
            
            for grant in grants:
                award_date_str = grant.get("award_notice_date")
                if not award_date_str:
                    continue
                try:
                    dt = datetime.datetime.strptime(award_date_str, "%Y-%m-%dT%H:%M:%SZ").date()
                except Exception as e:
                    print(f"Warning: Could not parse award_notice_date '{award_date_str}': {e}")
                    continue
                # Exclude awards after the cutoff (current week's Monday)
                if (dt.month, dt.day) > (cutoff_date.month, cutoff_date.day):
                    continue
                day_of_year = dt.timetuple().tm_yday
                data_by_year_counts.setdefault(dt.year, []).append(day_of_year)
                try:
                    amount = float(grant.get("award_amount", 0))
                except Exception:
                    amount = 0
                data_by_year_amounts.setdefault(dt.year, []).append((day_of_year, amount))
    
    return data_by_year_counts, data_by_year_amounts

def create_cumulative_counts(year_days, cutoff):
    """
    Build cumulative counts arrays (up to the cutoff day) for each year.
    Returns a dict mapping each year to (dates_array, cumulative_counts).
    """
    dates_array = [(datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).strftime("%b %d")
                   for i in range(cutoff)]
    cum_data = {}
    for year, days in year_days.items():
        counts = np.zeros(cutoff)
        for d in days:
            if 1 <= d <= cutoff:
                counts[d - 1] += 1
        cum_data[year] = (dates_array, np.cumsum(counts))
    return cum_data

def create_cumulative_amounts(year_awards, cutoff):
    """
    Build cumulative award amount arrays (up to the cutoff day) for each year.
    Returns a dict mapping each year to (dates_array, cumulative_amounts).
    """
    dates_array = [(datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).strftime("%b %d")
                   for i in range(cutoff)]
    cum_data = {}
    for year, entries in year_awards.items():
        amounts = np.zeros(cutoff)
        for d, amt in entries:
            if 1 <= d <= cutoff:
                amounts[d - 1] += amt
        cum_data[year] = (dates_array, np.cumsum(amounts))
    return cum_data

def plot_cumulative_data(cum_data, current_year, tick_interval=7, colors=None, output_filename="nih_awards"):
    """
    Plot cumulative counts (YTD) by award notice date.
    Saves interactive HTML and static PNG files.
    """
    fig = go.Figure()
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors.get(year, "lightgray") if colors else "lightgray"
            line_width = 2
            dash = "dash"
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=str(year),
                                 line=dict(color=color, width=line_width, dash=dash)))
    
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(tickmode="array", tickvals=tick_vals)
    fig.update_layout(title="Cumulative NIH Awards (YTD) by Award Notice Date",
                      xaxis_title="Date (Month-Day)",
                      yaxis_title="Cumulative Number of Awards")
    
    html_file = f"{output_filename}.html"
    png_file = f"{output_filename}.png"
    fig.write_html(html_file)
    fig.write_image(png_file, width=1200, height=800)
    print(f"Count plots saved as {html_file} and {png_file}")

def plot_cumulative_amounts(cum_data, current_year, tick_interval=7, colors=None, output_filename="nih_award_amounts"):
    """
    Plot cumulative award amounts (YTD) by award notice date.
    Saves interactive HTML and static PNG files.
    """
    fig = go.Figure()
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors.get(year, "lightgray") if colors else "lightgray"
            line_width = 2
            dash = "dash"
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=str(year),
                                 line=dict(color=color, width=line_width, dash=dash)))
    
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(tickmode="array", tickvals=tick_vals)
    fig.update_layout(title="Cumulative NIH Award Amounts (YTD) by Award Notice Date",
                      xaxis_title="Date (Month-Day)",
                      yaxis_title="Cumulative Award Amount ($)")
    
    html_file = f"{output_filename}.html"
    png_file = f"{output_filename}.png"
    fig.write_html(html_file)
    fig.write_image(png_file, width=1200, height=800)
    print(f"Award amount plots saved as {html_file} and {png_file}")

def main():
    parser = argparse.ArgumentParser(
        description=("Extract NIH RePORTER grant data (last 10 years, by day) and plot "
                     "cumulative counts and award amounts (YTD) up to the current week's Monday.")
    )
    parser.add_argument("--tick_interval", type=int, default=7,
                        help="Interval (in days) for x-axis tick labels. Default is 7.")
    args = parser.parse_args()

    today = datetime.date.today()
    # Compute the most recent Monday.
    monday_cutoff = today - datetime.timedelta(days=today.weekday())
    cutoff_day = monday_cutoff.timetuple().tm_yday
    current_year = today.year
    print(f"Using data up to {monday_cutoff.strftime('%b %d, %Y')} (most recent Monday).")
    
    start_year = current_year - 9
    print(f"Fetching grant data from {start_year} to {current_year} for awards up to {monday_cutoff.month:02d}-{monday_cutoff.day:02d}...")
    data_counts, data_amounts = fetch_all_grants_by_month(start_year, current_year, monday_cutoff)
    
    if not data_counts:
        print("No grant count data retrieved. Exiting.")
        return

    for year in sorted(data_counts.keys()):
        print(f"Year {year}: {len(data_counts[year])} awards processed (counts).")
    for year in sorted(data_amounts.keys()):
        print(f"Year {year}: {len(data_amounts[year])} awards processed (amounts).")
    
    cum_counts = create_cumulative_counts(data_counts, cutoff_day)
    cum_amounts = create_cumulative_amounts(data_amounts, cutoff_day)
    
    # Generate pastel colors for non-current years.
    non_current_years = [y for y in data_counts.keys() if y != current_year]
    colors = {}
    total = len(non_current_years)
    for i, year in enumerate(sorted(non_current_years)):
        colors[year] = get_pastel_color(i, total if total > 0 else 1)
    colors[current_year] = "#FF0000"
    
    print("Plotting cumulative count results...")
    plot_cumulative_data(cum_counts, current_year, tick_interval=args.tick_interval,
                         colors=colors, output_filename="nih_awards")
    
    print("Plotting cumulative award amount results...")
    plot_cumulative_amounts(cum_amounts, current_year, tick_interval=args.tick_interval,
                            colors=colors, output_filename="nih_award_amounts")

if __name__ == "__main__":
    main()
