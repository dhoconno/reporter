#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""NIH RePORTER Reporter - Cumulative Award Plot Script

This script fetches and analyzes "Notice of Closed Meeting(s)" (or grant award data)
from the Federal Register (or RePORTER API), generates a cumulative plot comparing
the current year with previous years, and exports a full CSV along with both an HTML
and PNG version of the plot.
"""

import datetime
import time
import json
import re
from pathlib import Path
import numpy as np
import plotly.graph_objects as go
import colorsys
import requests
from bs4 import BeautifulSoup
import pandas as pd
import os

# Use a local cache directory
cache_base_dir = "fr_cache"
os.makedirs(cache_base_dir, exist_ok=True)
print("Local cache directory created:", cache_base_dir)

# Federal Register API endpoint (or RePORTER API endpoint if adapted)
FR_API_URL = "https://www.federalregister.gov/api/v1/documents"

class FederalRegisterCache:
    def __init__(self, cache_dir=cache_base_dir):
        """Initialize cache in the specified directory."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        # Create subdirectory for content caching
        self.content_cache_dir = self.cache_dir / "content"
        self.content_cache_dir.mkdir(exist_ok=True)
        # Create separate directories for different content types
        self.xml_cache_dir = self.content_cache_dir / "xml"
        self.raw_cache_dir = self.content_cache_dir / "raw"
        self.html_cache_dir = self.content_cache_dir / "html"
        self.xml_cache_dir.mkdir(exist_ok=True)
        self.raw_cache_dir.mkdir(exist_ok=True)
        self.html_cache_dir.mkdir(exist_ok=True)

    def get_cache_path(self, year, month):
        """Get the path for a specific year-month cache file."""
        return self.cache_dir / f"fr_docs_{year}_{month:02d}.json"
    
    def get_document_id(self, url):
        """Extract document ID from a URL."""
        if not url:
            return None
        match = re.search(r'/(\d{4}-\d{5,6})', url)
        return match.group(1) if match else None
    
    def get_content_cache_path(self, url, content_type):
        """Get the cache path for document content based on type."""
        doc_id = self.get_document_id(url)
        if not doc_id:
            return None
        if content_type == 'xml':
            return self.xml_cache_dir / f"{doc_id}.xml"
        elif content_type == 'raw':
            return self.raw_cache_dir / f"{doc_id}.txt"
        elif content_type == 'html':
            return self.html_cache_dir / f"{doc_id}.html"
        return None

    def get_cached_content(self, url, content_type):
        cache_path = self.get_content_cache_path(url, content_type)
        if not cache_path or not cache_path.exists():
            return None
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"Error reading cached content for {url}: {e}")
            return None
            
    def save_content_to_cache(self, url, content, content_type):
        cache_path = self.get_content_cache_path(url, content_type)
        if not cache_path:
            return False
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving content cache for {url}: {e}")
            return False

    def get_cached_data(self, year, month):
        today = datetime.date.today()
        if year == today.year and month == today.month:
            return None
        cache_path = self.get_cache_path(year, month)
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
                if not all(key in data for key in ['fetch_date', 'documents']):
                    return None
                fetch_date = datetime.datetime.strptime(data['fetch_date'], "%Y-%m-%d").date()
                if (today - fetch_date).days > 7:
                    return None
                return data['documents']
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Cache error for {year}-{month:02d}: {e}")
            return None

    def save_to_cache(self, year, month, documents):
        cache_path = self.get_cache_path(year, month)
        data = {
            'fetch_date': datetime.date.today().strftime("%Y-%m-%d"),
            'documents': documents
        }
        try:
            with open(cache_path, 'w') as f:
                json.dump(data, f)
            print(f"Cache saved: {cache_path}")
        except Exception as e:
            print(f"Error saving cache for {year}-{month:02d}: {e}")

def create_ic_plots_dir():
    """Create directory for institute-specific plots if it doesn't exist."""
    ic_plots_dir = Path("ic_plots")
    ic_plots_dir.mkdir(exist_ok=True)
    print(f"Ensuring IC plots directory exists: {ic_plots_dir}")
    return ic_plots_dir

def plot_ic_data(meetings_by_year, current_year, cutoff_day):
    """Generate separate plots for each institute in the federal register data."""
    print("\nGenerating per-institute FR plots...")
    ic_plots_dir = Path("ic_plots")
    ic_plots_dir.mkdir(exist_ok=True)
    
    # Get all institutes across all years
    all_institutes = set()
    for year, meetings in meetings_by_year.items():
        for meeting in meetings:
            institute = meeting.get("institute")
            if institute and institute != "Unknown":
                all_institutes.add(institute)
    
    print(f"Found {len(all_institutes)} institutes for FR plotting")
    
    # Create colors for previous years
    non_current_years = [y for y in meetings_by_year.keys() if y != current_year]
    colors = {}
    total = len(non_current_years)
    for i, year in enumerate(sorted(non_current_years)):
        colors[year] = get_pastel_color(i, total if total > 0 else 1)
    colors[current_year] = "#FF0000"
    
    # For each institute, create a separate plot
    for institute in sorted(all_institutes):
        print(f"Creating plot for {institute}...")
        
        # Filter meetings for this institute
        institute_meetings_by_year = {}
        
        for year, meetings in meetings_by_year.items():
            institute_meetings = [m for m in meetings if m.get("institute") == institute]
            if institute_meetings:
                institute_meetings_by_year[year] = institute_meetings
        
        # Only create plot if we have data
        if institute_meetings_by_year:
            cum_data = create_cumulative_counts(institute_meetings_by_year, cutoff_day)
            output_filename = f"ic_plots/nih_fr_meetings_{institute}"
            plot_cumulative_data(cum_data, current_year, colors=colors, 
                                output_filename=output_filename)

def get_pastel_color(i, total):
    """Generate a pastel color using HLS conversion."""
    hue = i / total
    lightness = 0.8
    saturation = 0.5
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"

def fetch_fr_documents(start_date, end_date):
    """Query the API for documents containing 'Notice of Closed Meeting'."""
    results = []
    per_page = 1000
    page = 1
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    title_patterns = ["Notice of Closed Meeting", "Notice of Closed Meetings"]
    fields = [
        "title", "publication_date", "agencies", "html_url",
        "raw_text_url", "full_text_xml_url", "abstract",
        "document_number", "citation"
    ]
    while True:
        params = {
            "conditions[agencies][]": "national-institutes-of-health",
            "conditions[term]": "Notice of Closed Meeting",
            "conditions[publication_date][gte]": start_date_str,
            "conditions[publication_date][lte]": end_date_str,
            "per_page": per_page,
            "page": page,
            "order": "oldest",
            "fields[]": fields
        }
        print(f"Fetching page {page}...")
        try:
            response = requests.get(FR_API_URL, params=params)
            response.raise_for_status()
            data = response.json()
            filtered_results = []
            for doc in data.get("results", []):
                title = doc.get("title", "")
                if any(pattern in title for pattern in title_patterns):
                    filtered_results.append(doc)
            results.extend(filtered_results)
            if len(data.get("results", [])) < per_page or not data.get("results"):
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"Error fetching data: {e}")
            break
    return results

def fetch_monthly_documents_with_cache(year, month, cache):
    start_date = datetime.date(year, month, 1)
    if month == 12:
        end_date = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        end_date = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
    cached_data = cache.get_cached_data(year, month)
    if cached_data is not None:
        print(f"Using cached data for {year}-{month:02d}")
        return cached_data, 'hit'
    documents = fetch_fr_documents(start_date, end_date)
    cache.save_to_cache(year, month, documents)
    return documents, 'miss'

def extract_meeting_data(document, cache=None):
    """Extract meeting information from a document."""
    meetings = []
    text_content = ""
    xml_url = document.get("full_text_xml_url")
    if cache and xml_url:
        cached_xml = cache.get_cached_content(xml_url, 'xml')
        if cached_xml:
            text_content = re.sub(r'<[^>]+>', ' ', cached_xml)
            text_content = re.sub(r'\s+', ' ', text_content).strip()
    if not text_content and xml_url:
        try:
            response = requests.get(xml_url)
            if response.status_code == 200:
                xml_content = response.text
                text_content = re.sub(r'<[^>]+>', ' ', xml_content)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                if cache:
                    cache.save_content_to_cache(xml_url, xml_content, 'xml')
            else:
                print(f"XML fetch failed: {response.status_code}")
        except Exception as e:
            print(f"XML fetch error: {e}")
    raw_text_url = document.get("raw_text_url")
    if not text_content and raw_text_url:
        if cache:
            cached_raw = cache.get_cached_content(raw_text_url, 'raw')
            if cached_raw:
                text_content = cached_raw
        if not text_content:
            try:
                response = requests.get(raw_text_url)
                if response.status_code == 200:
                    text_content = response.text or ""
                    if cache and text_content:
                        cache.save_content_to_cache(raw_text_url, text_content, 'raw')
                else:
                    print(f"Raw text fetch failed: {response.status_code}")
            except Exception as e:
                print(f"Raw text fetch error: {e}")
    html_url = document.get("html_url")
    if not text_content and html_url:
        if cache:
            cached_html = cache.get_cached_content(html_url, 'html')
            if cached_html:
                soup = BeautifulSoup(cached_html, 'html.parser')
                content_area = soup.find('div', class_='body_column')
                if content_area:
                    text_content = content_area.get_text() or ""
        if not text_content:
            try:
                response = requests.get(html_url)
                if response.status_code == 200:
                    html_content = response.text
                    if cache:
                        cache.save_content_to_cache(html_url, html_content, 'html')
                    soup = BeautifulSoup(html_content, 'html.parser')
                    content_area = soup.find('div', class_='body_column')
                    if content_area:
                        text_content = content_area.get_text() or ""
                else:
                    print(f"HTML fetch failed: {response.status_code}")
            except Exception as e:
                print(f"HTML fetch error: {e}")
    if not text_content:
        text_content = document.get("abstract", "") or ""
        if text_content:
            print(f"Using abstract ({len(text_content)} characters)")
    if not text_content:
        print(f"Warning: No text for document {document.get('title', 'Unknown')}")
        return []
    preview_length = min(300, len(text_content))
    print(f"Text preview: {text_content[:preview_length]}...")
    pub_date = document.get("publication_date") or "Unknown"
    doc_title = document.get("title", "Unknown Title")
    institute = "NIH"
    institute_pattern = r'^(National Institute[^;]+);'
    institute_match = re.search(institute_pattern, doc_title)
    if institute_match:
        full_institute_name = institute_match.group(1).strip()
        acronym_pattern = r'\(([A-Z]+)\)'
        acronym_match = re.search(acronym_pattern, full_institute_name)
        if acronym_match:
            institute = acronym_match.group(1)
        else:
            words = [word for word in full_institute_name.split() if word[0].isupper()]
            if words:
                institute = ''.join(word[0] for word in words)
    agencies = document.get("agencies", [])
    for agency in agencies:
        if isinstance(agency, dict) and "acronym" in agency and agency["acronym"] != "NIH":
            institute = agency["acronym"]
            break
    name_pattern = r'Name\s+of\s+Committee:[ \t]*(.*?)(?:Date|$)'
    date_pattern = r'Date:[ \t]*(.*?)(?:Time|$)'
    meeting_block_pattern = r'Name\s+of\s+Committee:.*?Date:.*?Time:.*?(?:Address:|Agenda:)'
    meeting_blocks = re.findall(meeting_block_pattern, text_content, re.DOTALL)
    print(f"Found {len(meeting_blocks)} meeting blocks")
    for block in meeting_blocks:
        committee_match = re.search(name_pattern, block, re.DOTALL)
        date_match = re.search(date_pattern, block, re.DOTALL)
        if committee_match and date_match:
            committee_name = re.sub(r'\s+', ' ', committee_match.group(1).strip())
            meeting_date = re.sub(r'\s+', ' ', date_match.group(1).strip())
            meetings.append({
                "committee": committee_name,
                "date": meeting_date,
                "institute": institute,
                "publication_date": pub_date,
                "document_url": document.get("html_url", ""),
                "document_title": doc_title
            })
    if not meetings and ("Notice of Closed Meeting" in doc_title or "Notice of Closed Meetings" in doc_title):
        meetings.append({
            "committee": f"{institute} Closed Meeting (See document for details)",
            "date": "See document for details",
            "institute": institute,
            "publication_date": pub_date,
            "document_url": document.get("html_url", ""),
            "document_title": doc_title,
            "notes": "No specific meeting details extracted"
        })
    return meetings

def fetch_all_meetings(start_year, current_year, cutoff_date):
    cache = FederalRegisterCache()
    meetings_by_year = {}
    month_limit = cutoff_date.month
    
    for year in range(start_year, current_year + 1):
        year_meetings = []
        for month in range(1, month_limit + 1):
            print(f"Processing {year}-{month:02d}...")
            documents, cache_status = fetch_monthly_documents_with_cache(year, month, cache)
            print(f"{len(documents)} documents ({cache_status}).")
            doc_meeting_count = 0
            
            for document in documents:
                pub_date_str = document.get("publication_date")
                if not pub_date_str:
                    continue
                try:
                    pub_date = datetime.datetime.strptime(pub_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                    
                # Only filter out dates after today's date (not Monday)
                if (pub_date.month > cutoff_date.month) or \
                   (pub_date.month == cutoff_date.month and pub_date.day > cutoff_date.day):
                    continue
                    
                document_meetings = extract_meeting_data(document, cache)
                doc_meeting_count += len(document_meetings)
                for meeting in document_meetings:
                    meeting["day_of_year"] = pub_date.timetuple().tm_yday
                    year_meetings.append(meeting)
                    
            print(f"Extracted {doc_meeting_count} meetings.")
            
        meetings_by_year[year] = year_meetings
        print(f"{year}: {len(year_meetings)} meetings total.")
        
    return meetings_by_year

def create_cumulative_counts(meetings_by_year, cutoff):
    """
    Build cumulative counts arrays (up to the cutoff day) for each year.
    Ensures arrays extend through the full date range.
    """
    # Create date array for full year (through cutoff day)
    dates_array = [(datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).strftime("%b %d")
                   for i in range(1, cutoff + 1)]
    
    cum_data = {}
    for year, meetings in meetings_by_year.items():
        # Initialize counts array for all days up to cutoff
        counts = np.zeros(cutoff)
        
        # Fill in counts for each meeting's publication day
        for meeting in meetings:
            day = meeting.get("day_of_year", 0)
            if 1 <= day <= cutoff:
                counts[day - 1] += 1
                
        cum_data[year] = (dates_array, np.cumsum(counts))
    
    return cum_data

def plot_cumulative_data(cum_data, current_year, tick_interval=7, colors=None, output_filename="nih_fr_meetings"):
    fig = go.Figure()
    
    today = datetime.date.today()
    cutoff_day = today.timetuple().tm_yday
    
    # Create a full dates array through today's date
    full_dates = [(datetime.date(2000, 1, 1) + datetime.timedelta(days=i)).strftime("%b %d")
                  for i in range(1, cutoff_day + 1)]
    
    for year in sorted(cum_data.keys()):
        x, y = cum_data[year]
        
        # If data doesn't extend to the current date, extend it
        if len(x) < len(full_dates):
            # Extend x to include all dates up to today
            # Extend y by repeating the last value
            extended_x = full_dates
            last_value = y[-1] if len(y) > 0 else 0
            extended_y = list(y) + [last_value] * (len(full_dates) - len(y))
        else:
            extended_x = x
            extended_y = y
            
        if year == current_year:
            color = "#FF0000"
            line_width = 3
            dash = "solid"
        else:
            color = colors.get(year, "lightgray") if colors else "lightgray"
            line_width = 2
            dash = "dash"
            
        fig.add_trace(go.Scatter(
            x=extended_x,
            y=extended_y,
            mode="lines",
            name=str(year),
            line=dict(color=color, width=line_width, dash=dash)
        ))
    
    # Use tick marks at specified intervals
    tick_vals = full_dates[::tick_interval]
    
    fig.update_xaxes(
        tickmode="array", 
        tickvals=tick_vals,
        range=[0, len(full_dates) - 1]  # Explicit range from first day to today
    )
    
    today_str = today.strftime("%b %d")
    
    fig.update_layout(
        title=f"Cumulative NIH Closed Meetings Announced (YTD)",
        xaxis_title="Date (Month-Day)",
        yaxis_title="Cumulative Meetings",
        margin=dict(t=100, r=20, b=70, l=20)
    )
    
    # Export HTML with plotly.js included
    html_file = f"{output_filename}.html"
    fig.write_html(html_file, include_plotlyjs=True, full_html=True)
    
    # Export PNG with standardized size and high resolution
    png_file = f"{output_filename}.png"
    try:
        fig.write_image(png_file, width=1200, height=800, scale=2)  # Match NIH Reporter script size
    except Exception as e:
        print(f"Error writing PNG: {e}")
    
    print(f"Plot saved as {html_file} and {png_file}")
    return fig

def export_meetings_to_csv(meetings_by_year, filename=None, compress=True):
    """Export all meetings across all years to a CSV file and optionally compress with zstd."""
    import zstandard as zstd
    
    all_meetings = []
    for year in meetings_by_year:
        all_meetings.extend(meetings_by_year[year])
    if not all_meetings:
        print("No meetings found to export.")
        return
    if not filename:
        filename = "nih_fr_meetings_all.csv"
    
    df = pd.DataFrame(all_meetings)
    
    # Save the CSV
    df.to_csv(filename, index=False)
    print(f"Exported {len(all_meetings)} meetings to {filename}")
    
    # Compress with zstd if requested
    if compress:
        compressed_file = f"{filename}.zst"
        print(f"Compressing to {compressed_file}...")
        
        with open(filename, 'rb') as f_in:
            data = f_in.read()
            
        cctx = zstd.ZstdCompressor(level=19)  # Maximum compression
        compressed = cctx.compress(data)
        
        with open(compressed_file, 'wb') as f_out:
            f_out.write(compressed)
        
        print(f"Data compressed to {compressed_file}")
        
        # Remove the uncompressed CSV file to save space
        import os
        os.remove(filename)
        print(f"Removed uncompressed CSV file {filename}")

def display_meetings_table(meetings_by_year, current_year, n=10):
    if current_year not in meetings_by_year or not meetings_by_year[current_year]:
        print("No meetings for current year.")
        return None
    meetings = meetings_by_year[current_year]
    meetings.sort(key=lambda x: (x.get("publication_date", ""), x.get("institute", ""), x.get("committee", "")))
    col_order = ['publication_date', 'institute', 'committee', 'date', 'document_title', 'document_url', 'day_of_year']
    df = pd.DataFrame(meetings)
    col_order = [col for col in col_order if col in df.columns] + [col for col in df.columns if col not in col_order]
    df = df[col_order]
    print(f"{len(df)} meetings for {current_year}. Showing first {min(n, len(df))} entries.")
    return df.head(n)

def run_analysis(start_year=2016, end_year=None, use_cached=True, small_test=False):
    """
    Run the full analysis pipeline.
    """
    if not end_year:
        end_year = datetime.date.today().year
    
    # Use today's date as cutoff rather than the most recent Monday
    today = datetime.date.today()
    cutoff_date = today  # Use today instead of monday_cutoff
    cutoff_day = cutoff_date.timetuple().tm_yday
    current_year = today.year
    
    print(f"Using data up to {cutoff_date.strftime('%b %d, %Y')} (current day).")
    
    if small_test:
        start_year = max(2023, start_year)
        end_year = current_year
        
    cache = FederalRegisterCache()
    if not use_cached:
        import shutil
        shutil.rmtree(cache.cache_dir, ignore_errors=True)
        cache = FederalRegisterCache()
        
    meetings_by_year = fetch_all_meetings(start_year, end_year, cutoff_date)
    
    # Rest of function remains the same
    if not meetings_by_year:
        print("No meeting data retrieved.")
        return None
    
    for year in sorted(meetings_by_year.keys()):
        print(f"{year}: {len(meetings_by_year[year])} meetings.")
        
    cum_data = create_cumulative_counts(meetings_by_year, cutoff_day)
    non_current_years = [y for y in meetings_by_year.keys() if y != current_year]
    colors = {}
    total = len(non_current_years)
    for i, year in enumerate(sorted(non_current_years)):
        colors[year] = get_pastel_color(i, total if total > 0 else 1)
    colors[current_year] = "#FF0000"
    fig = plot_cumulative_data(cum_data, current_year, tick_interval=7, colors=colors, output_filename="nih_fr_meetings")

    df = display_meetings_table(meetings_by_year, current_year, n=15)
    export_meetings_to_csv(meetings_by_year, filename="nih_fr_meetings_all.csv")

    # Generate per-IC plots
    plot_ic_data(meetings_by_year, current_year, cutoff_day)
    
    df = display_meetings_table(meetings_by_year, current_year, n=15)
    export_meetings_to_csv(meetings_by_year, filename="nih_fr_meetings_all.csv")

    try:
        xml_cache_count = len(list(cache.xml_cache_dir.glob("*.xml")))
        raw_cache_count = len(list(cache.raw_cache_dir.glob("*.txt")))
        html_cache_count = len(list(cache.html_cache_dir.glob("*.html")))
        total_cached = xml_cache_count + raw_cache_count + html_cache_count
        print(f"Cache contains {total_cached} files.")
    except Exception as e:
        print(f"Cache error: {e}")
    return {
        "meetings_by_year": meetings_by_year,
        "cum_data": cum_data,
        "figure": fig,
        "current_year_data": df,
        "cache": cache
    }

if __name__ == '__main__':
    run_analysis(start_year=2016, use_cached=True, small_test=False)
