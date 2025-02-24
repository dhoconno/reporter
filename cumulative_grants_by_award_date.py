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
    Includes award_notice_date, award_amount, and agency_ic_admin fields.
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
            "fields": ["award_notice_date", "award_amount", "agency_ic_admin"]  # Use agency_ic_admin for IC info
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
    Now includes IC breakdown data with proper IC extraction.
    """
    cache = NIHReporterCache()
    data_by_year_counts = {}
    data_by_year_amounts = {}
    # New dictionary to store IC data by year and IC (for cumulative data)
    ic_data_by_year = {}
    
    # Set of all ICs across all years - will be used to ensure consistency
    all_ics = set()
    
    # Current year ICs - to determine which are active
    current_ics = set()
    
    month_limit = cutoff_date.month  # Fetch data for months 1 to cutoff_date.month.
    for year in range(start_year, current_year + 1):
        if year not in ic_data_by_year:
            ic_data_by_year[year] = {}
            
        # Initialize dictionaries for cumulative data
        ic_counts_cumulative = {}
        ic_amounts_cumulative = {}
        
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
                
                # Extract IC information from agency_ic_admin
                ic_info = grant.get("agency_ic_admin", {})
                ic = ic_info.get("abbreviation", "Other")
                if not ic:
                    ic = "Other"
                
                # Add to set of all ICs
                all_ics.add(ic)
                
                # Track current year ICs separately
                if year >= current_year - 1:  # Current and previous year
                    current_ics.add(ic)
                
                # Update IC cumulative counts
                ic_counts_cumulative[ic] = ic_counts_cumulative.get(ic, 0) + 1
                # Update IC cumulative amounts
                ic_amounts_cumulative[ic] = ic_amounts_cumulative.get(ic, 0) + amount
                
                # Store cumulative data for this day of year
                ic_data_by_year[year][day_of_year] = {
                    "counts": dict(ic_counts_cumulative),  # Copy to avoid reference issues
                    "amounts": dict(ic_amounts_cumulative)  # Copy to avoid reference issues
                }
    
    return data_by_year_counts, data_by_year_amounts, ic_data_by_year, current_ics

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

def plot_cumulative_data(cum_data, ic_data, current_ics, current_year, tick_interval=7, colors=None, output_filename="nih_awards"):
    """
    Plot cumulative counts (YTD) by award notice date with interactive IC breakdown on click.
    """
    fig = go.Figure()
    
    # Store custom data for each point to enable click events
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        
        # Prepare custom data array with dates as day-of-year
        # This will be used to look up IC data when a point is clicked
        custom_data = []
        for i, date_str in enumerate(x):
            # Fix parsing by adding a fixed year (2000 is a leap year)
            date_obj = datetime.datetime.strptime(f"{date_str} 2000", "%b %d %Y")
            day_of_year = date_obj.timetuple().tm_yday
            custom_data.append([year, day_of_year])
        
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors.get(year, "lightgray") if colors else "lightgray"
            line_width = 2
            dash = "dash"
            
        fig.add_trace(go.Scatter(
            x=x, 
            y=y, 
            mode="lines", 
            name=str(year),
            line=dict(color=color, width=line_width, dash=dash),
            customdata=custom_data  # Include custom data for click events
        ))
    
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(tickmode="array", tickvals=tick_vals)
    fig.update_layout(
        title="Cumulative NIH Awards (YTD) by Award Notice Date",
        xaxis_title="Date (Month-Day)",
        yaxis_title="Cumulative Number of Awards",
        clickmode='event',
        margin=dict(t=100, r=20, b=70, l=20)
    )
    
    # Convert IC data to JSON for use in the callback
    import json
    ic_data_json = json.dumps(ic_data)
    current_ics_json = json.dumps(list(current_ics))
    
    # Create the JavaScript with manually inserted JSON variables
    # This avoids Python's string formatting issues
    fig_js = """
    <script>
    // Wait for the DOM to be fully loaded
    document.addEventListener('DOMContentLoaded', function() {
        // Store IC data in a global variable
        var icData = """ + ic_data_json + """;
        var currentICs = """ + current_ics_json + """;
        
        // Find the Plotly container - might have different class names in different browsers
        var setupClickHandler = function() {
            var plotlyContainer = document.querySelector('.js-plotly-plot') || document.querySelector('.plotly');
            if (!plotlyContainer) {
                console.error("Could not find Plotly container, will retry in 1 second");
                setTimeout(setupClickHandler, 1000);
                return;
            }
            
            // Set up click handler on the main plot container
            plotlyContainer.on('plotly_click', function(data) {
                var point = data.points[0];
                var customData = point.customdata;
                var year = customData[0];
                var dayOfYear = customData[1];
                var date = point.x;
                
                // Check if we have IC data for this point
                if (icData[year] && icData[year][dayOfYear]) {
                    // Get CUMULATIVE IC breakdown for this date
                    var rawData = icData[year][dayOfYear].counts;
                    
                    // Reorganize data - keep current ICs separate, group others as "Other"
                    var organizedData = {};
                    var otherTotal = 0;
                    
                    // Process each IC in the data
                    Object.keys(rawData).forEach(function(ic) {
                        if (currentICs.includes(ic)) {
                            // Known current IC - keep as is
                            organizedData[ic] = rawData[ic];
                        } else {
                            // Not a current IC - add to "Other"
                            otherTotal += rawData[ic];
                        }
                    });
                    
                    // Add the "Other" category if needed
                    if (otherTotal > 0) {
                        organizedData["Other"] = otherTotal;
                    }
                    
                    // Convert to arrays for plotting
                    var icNames = Object.keys(organizedData);
                    var icValues = Object.values(organizedData);
                    
                    // Sort by count descending
                    var combinedData = icNames.map(function(name, i) {
                        return {name: name, value: icValues[i]};
                    });
                    
                    combinedData.sort(function(a, b) {
                        return b.value - a.value;
                    });
                    
                    // Separate back into arrays
                    icNames = combinedData.map(function(item) { return item.name; });
                    icValues = combinedData.map(function(item) { return item.value; });
                    
                    // Create inset chart
                    var insetData = [{
                        type: 'bar',
                        x: icNames,
                        y: icValues,
                        marker: {
                            color: 'rgba(50, 171, 96, 0.7)'
                        }
                    }];
                    
                    var insetLayout = {
                        title: 'Cumulative IC Breakdown for ' + date + ', ' + year,
                        xaxis: {
                            title: 'Institute/Center',
                            tickangle: 90
                        },
                        yaxis: {
                            title: 'Number of Awards (YTD)'
                        },
                        margin: {
                            t: 40, r: 20, b: 120, l: 60
                        },
                        height: 500,
                        width: 800,
                        bargap: 0.1
                    };
                    
                    // Remove existing inset chart if any
                    var existingInset = document.getElementById('inset-chart');
                    if (existingInset) {
                        document.body.removeChild(existingInset);
                    }
                    
                    // Create new inset div
                    var insetDiv = document.createElement('div');
                    insetDiv.id = 'inset-chart';
                    insetDiv.style.position = 'fixed';
                    insetDiv.style.top = '10%';
                    insetDiv.style.left = '50%';
                    insetDiv.style.transform = 'translateX(-50%)';
                    insetDiv.style.backgroundColor = 'white';
                    insetDiv.style.border = '1px solid #ddd';
                    insetDiv.style.padding = '10px';
                    insetDiv.style.zIndex = '1000';
                    insetDiv.style.boxShadow = '0 0 10px rgba(0,0,0,0.2)';
                    
                    // Add close button
                    var closeBtn = document.createElement('button');
                    closeBtn.innerHTML = '×';
                    closeBtn.style.position = 'absolute';
                    closeBtn.style.top = '5px';
                    closeBtn.style.right = '5px';
                    closeBtn.style.border = 'none';
                    closeBtn.style.background = 'none';
                    closeBtn.style.fontSize = '24px';
                    closeBtn.style.cursor = 'pointer';
                    closeBtn.style.zIndex = '1001';
                    closeBtn.onclick = function() {
                        document.body.removeChild(insetDiv);
                    };
                    
                    insetDiv.appendChild(closeBtn);
                    document.body.appendChild(insetDiv);
                    
                    // Plot the inset chart
                    Plotly.newPlot('inset-chart', insetData, insetLayout);
                }
            });
        };
        
        // Start trying to set up the handler
        setupClickHandler();
    });
    </script>
    """
    
    # Save HTML with the custom JavaScript callback
    html_file = f"{output_filename}.html"
    html_content = fig.to_html(include_plotlyjs=True, full_html=True)
    html_content = html_content.replace('</body>', fig_js + '</body>')
    
    with open(html_file, 'w') as f:
        f.write(html_content)
    
    # Save static PNG (this won't have interactive features)
    png_file = f"{output_filename}.png"
    fig.write_image(png_file, width=1200, height=800)
    
    print(f"Count plots saved as {html_file} and {png_file}")

def plot_cumulative_amounts(cum_data, ic_data, current_ics, current_year, tick_interval=7, colors=None, output_filename="nih_award_amounts"):
    """
    Plot cumulative award amounts (YTD) by award notice date with interactive IC breakdown on click.
    """
    fig = go.Figure()
    
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        
        # Prepare custom data array with dates as day-of-year
        # This will be used to look up IC data when a point is clicked
        custom_data = []
        for i, date_str in enumerate(x):
            # Fix parsing by adding a fixed year (2000 is a leap year)
            date_obj = datetime.datetime.strptime(f"{date_str} 2000", "%b %d %Y")
            day_of_year = date_obj.timetuple().tm_yday
            custom_data.append([year, day_of_year])
        
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors.get(year, "lightgray") if colors else "lightgray"
            line_width = 2
            dash = "dash"
            
        fig.add_trace(go.Scatter(
            x=x, 
            y=y, 
            mode="lines", 
            name=str(year),
            line=dict(color=color, width=line_width, dash=dash),
            customdata=custom_data  # Include custom data for click events
        ))
    
    full_x = list(cum_data.values())[0][0]
    tick_vals = full_x[::tick_interval]
    fig.update_xaxes(tickmode="array", tickvals=tick_vals)
    fig.update_layout(
        title="Cumulative NIH Award Amounts (YTD) by Award Notice Date",
        xaxis_title="Date (Month-Day)",
        yaxis_title="Cumulative Award Amount ($)",
        clickmode='event',
        margin=dict(t=100, r=20, b=70, l=20)
    )
    
    # Convert IC data to JSON for use in the callback
    import json
    ic_data_json = json.dumps(ic_data)
    current_ics_json = json.dumps(list(current_ics))
    
    # Create the JavaScript with manually inserted JSON variables
    # This avoids Python's string formatting issues
    fig_js = """
    <script>
    // Wait for the DOM to be fully loaded
    document.addEventListener('DOMContentLoaded', function() {
        // Store IC data in a global variable
        var icData = """ + ic_data_json + """;
        var currentICs = """ + current_ics_json + """;
        
        // Find the Plotly container - might have different class names in different browsers
        var setupClickHandler = function() {
            var plotlyContainer = document.querySelector('.js-plotly-plot') || document.querySelector('.plotly');
            if (!plotlyContainer) {
                console.error("Could not find Plotly container, will retry in 1 second");
                setTimeout(setupClickHandler, 1000);
                return;
            }
            
            // Set up click handler on the main plot container
            plotlyContainer.on('plotly_click', function(data) {
                var point = data.points[0];
                var customData = point.customdata;
                var year = customData[0];
                var dayOfYear = customData[1];
                var date = point.x;
                
                // Check if we have IC data for this point
                if (icData[year] && icData[year][dayOfYear]) {
                    // Get CUMULATIVE IC breakdown for this date
                    var rawData = icData[year][dayOfYear].amounts;
                    
                    // Reorganize data - keep current ICs separate, group others as "Other"
                    var organizedData = {};
                    var otherTotal = 0;
                    
                    // Process each IC in the data
                    Object.keys(rawData).forEach(function(ic) {
                        if (currentICs.includes(ic)) {
                            // Known current IC - keep as is
                            organizedData[ic] = rawData[ic];
                        } else {
                            // Not a current IC - add to "Other"
                            otherTotal += rawData[ic];
                        }
                    });
                    
                    // Add the "Other" category if needed
                    if (otherTotal > 0) {
                        organizedData["Other"] = otherTotal;
                    }
                    
                    // Convert to arrays for plotting
                    var icNames = Object.keys(organizedData);
                    var icValues = Object.values(organizedData);
                    
                    // Sort by amount descending
                    var combinedData = icNames.map(function(name, i) {
                        return {name: name, value: icValues[i]};
                    });
                    
                    combinedData.sort(function(a, b) {
                        return b.value - a.value;
                    });
                    
                    // Separate back into arrays
                    icNames = combinedData.map(function(item) { return item.name; });
                    icValues = combinedData.map(function(item) { return item.value; });
                    
                    // Format amounts as millions of dollars
                    var formattedValues = icValues.map(function(val) {
                        return (val / 1000000).toFixed(2);
                    });
                    
                    // Create inset chart
                    var insetData = [{
                        type: 'bar',
                        x: icNames,
                        y: formattedValues,
                        marker: {
                            color: 'rgba(50, 171, 96, 0.7)'
                        }
                    }];
                    
                    var insetLayout = {
                        title: 'Cumulative IC Funding for ' + date + ', ' + year,
                        xaxis: {
                            title: 'Institute/Center',
                            tickangle: 90
                        },
                        yaxis: {
                            title: 'Award Amount ($ Millions) YTD'
                        },
                        margin: {
                            t: 40, r: 20, b: 120, l: 60
                        },
                        height: 500,
                        width: 800,
                        bargap: 0.1
                    };
                    
                    // Remove existing inset chart if any
                    var existingInset = document.getElementById('inset-chart');
                    if (existingInset) {
                        document.body.removeChild(existingInset);
                    }
                    
                    // Create new inset div
                    var insetDiv = document.createElement('div');
                    insetDiv.id = 'inset-chart';
                    insetDiv.style.position = 'fixed';
                    insetDiv.style.top = '10%';
                    insetDiv.style.left = '50%';
                    insetDiv.style.transform = 'translateX(-50%)';
                    insetDiv.style.backgroundColor = 'white';
                    insetDiv.style.border = '1px solid #ddd';
                    insetDiv.style.padding = '10px';
                    insetDiv.style.zIndex = '1000';
                    insetDiv.style.boxShadow = '0 0 10px rgba(0,0,0,0.2)';
                    
                    // Add close button
                    var closeBtn = document.createElement('button');
                    closeBtn.innerHTML = '×';
                    closeBtn.style.position = 'absolute';
                    closeBtn.style.top = '5px';
                    closeBtn.style.right = '5px';
                    closeBtn.style.border = 'none';
                    closeBtn.style.background = 'none';
                    closeBtn.style.fontSize = '24px';
                    closeBtn.style.cursor = 'pointer';
                    closeBtn.style.zIndex = '1001';
                    closeBtn.onclick = function() {
                        document.body.removeChild(insetDiv);
                    };
                    
                    insetDiv.appendChild(closeBtn);
                    document.body.appendChild(insetDiv);
                    
                    // Plot the inset chart
                    Plotly.newPlot('inset-chart', insetData, insetLayout);
                }
            });
        };
        
        // Start trying to set up the handler
        setupClickHandler();
    });
    </script>
    """
    
    # Save HTML with the custom JavaScript callback
    html_file = f"{output_filename}.html"
    html_content = fig.to_html(include_plotlyjs=True, full_html=True)
    html_content = html_content.replace('</body>', fig_js + '</body>')
    
    with open(html_file, 'w') as f:
        f.write(html_content)
    
    # Save static PNG (this won't have interactive features)
    png_file = f"{output_filename}.png"
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
    data_counts, data_amounts, ic_data, current_ics = fetch_all_grants_by_month(start_year, current_year, monday_cutoff)
    
    if not data_counts:
        print("No grant count data retrieved. Exiting.")
        return

    for year in sorted(data_counts.keys()):
        print(f"Year {year}: {len(data_counts[year])} awards processed (counts).")
    for year in sorted(data_amounts.keys()):
        print(f"Year {year}: {len(data_amounts[year])} awards processed (amounts).")
    
    print(f"Found {len(current_ics)} current ICs: {', '.join(sorted(current_ics))}")
    
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
    plot_cumulative_data(cum_counts, ic_data, current_ics, current_year, tick_interval=args.tick_interval,
                         colors=colors, output_filename="nih_awards")
    
    print("Plotting cumulative award amount results...")
    plot_cumulative_amounts(cum_amounts, ic_data, current_ics, current_year, tick_interval=args.tick_interval,
                            colors=colors, output_filename="nih_award_amounts")

if __name__ == "__main__":
    main()
