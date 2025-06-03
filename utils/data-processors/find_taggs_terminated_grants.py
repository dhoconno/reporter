import tabula
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime
import time
import json
import os
import xml.etree.ElementTree as ET
import zstandard as zstd

def standardize_headers(df):
    """Map raw column names from the PDF to standardized names."""
    mapping = {
        "awarding office": "Awarding Office",
        "fain": "FAIN",
        "project #": "Award Number",
        "award number": "Award Number",
        "recipient name": "Recipient Name",
        "action date": "Action Date (Date Terminated)",
        "date terminated": "Action Date (Date Terminated)",
        "termination date": "Action Date (Date Terminated)",
        "obligated": "Total Amount Obligated",
        "expended": "Total Amount Expended",
        "payment amount": "Total Payment Amount (As of Termination)",
        "unliquidated": "Unliquidated Obligations (As of Termination)",
        "award title": "Award Title",
        "presidential action": "Presidential Action",
        "for cause": "For Cause (Put X if applicable)",
    }

    new_cols = []
    for col in df.columns:
        normalized = str(col).strip().lower()
        new_name = None
        for key, val in mapping.items():
            if key in normalized:
                new_name = val
                break
        new_cols.append(new_name if new_name else col)
    df.columns = new_cols
    return df

def download_pdf(url, output_path):
    """Download PDF file from URL"""
    response = requests.get(url)
    if response.status_code == 200:
        with open(output_path, 'wb') as f:
            f.write(response.content)
        return True
    return False

def clean_dataframe(df):
    """Clean and standardize the DataFrame"""
    # Remove any row (except the first) where the first column is "Awarding Office"
    if df.shape[0] > 1:  # Make sure there's more than one row
        first_col_name = df.columns[0]
        # Keep the first row and filter out any other rows with "Awarding Office" in first column
        header_mask = df[first_col_name] == "Awarding Office"
        if header_mask.sum() > 0:
            # Create a mask that keeps the first row (header) and excludes duplicate headers
            row_indices = df.index.tolist()
            rows_to_keep = [i for i, (idx, is_header) in enumerate(zip(row_indices, header_mask)) 
                           if not is_header or i == 0]
            df = df.iloc[rows_to_keep]
    
    # Clean column names - remove newlines and extra spaces
    df.columns = [' '.join(str(col).replace('\n', ' ').split()) for col in df.columns]

    # Remove columns with 'nan' as the header name
    df = df.loc[:, [col for col in df.columns if str(col).lower() != 'nan']]
    
    # Remove empty columns at the end 
    df = df.dropna(axis=1, how='all')
    
    # Clean string values in the DataFrame
    for col in df.columns:
        if pd.api.types.is_object_dtype(df[col]):
            # Remove extra spaces and newlines
            df[col] = df[col].apply(lambda x: ' '.join(str(x).split()) if isinstance(x, str) else x)
            
            # Clean currency values and convert to numeric
            if col in ['Total Amount Obligated', 'Total Amount Expended', 
                      'Total Payment Amount (As of Termination)', 
                      'Unliquidated Obligations (As of Termination)']:
                df[col] = df[col].apply(lambda x: str(x).replace('$', '').replace(',', '').strip() if isinstance(x, str) else x)
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Convert text to title case for names and titles
            if col in ['Recipient Name', 'Award Title']:
                df[col] = df[col].apply(lambda x: str(x).title() if isinstance(x, str) else x)
    
    # Convert action date to datetime after cleaning the data
    date_col = 'Action Date (Date Terminated)'
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
    
    # Add download date column at the beginning of the DataFrame
    df.insert(0, 'Download Date', pd.to_datetime(datetime.now().date()))
    
    return df

def extract_data_from_pdf(pdf_path):
    """Extract tables from PDF into a pandas DataFrame"""
    print(f"Attempting to extract tables from {pdf_path}")
    
    # Check if PDF exists and has content
    if not os.path.exists(pdf_path):
        print(f"ERROR: PDF file not found at {pdf_path}")
        return pd.DataFrame()
        
    if os.path.getsize(pdf_path) == 0:
        print(f"ERROR: PDF file is empty at {pdf_path}")
        return pd.DataFrame()
        
    try:
        # First, try to extract tables with area and stream mode for better header parsing
        tables = tabula.read_pdf(
            pdf_path,
            pages='all',
            lattice=True,
            multiple_tables=True,
            pandas_options={'header': None}
        )
        
        print(f"Extracted {len(tables)} tables from PDF")
        
        if not tables or len(tables) == 0:
            print("ERROR: No tables were extracted from the PDF")
            return pd.DataFrame()
            
        # Standard column order used in the final CSV
        expected_headers = [
            'Awarding Office',
            'FAIN',
            'Award Number',
            'Recipient Name',
            'Action Date (Date Terminated)',
            'Total Amount Obligated',
            'Total Amount Expended',
            'Total Payment Amount (As of Termination)',
            'Unliquidated Obligations (As of Termination)',
            'Award Title',
            'Presidential Action',
            'For Cause (Put X if applicable)'
        ]

        processed_tables = []
        
        for i, table in enumerate(tables):
            print(f"Processing table {i+1} with shape: {table.shape}")
            
            # Skip empty tables
            if table.empty:
                continue
                
            # Check if this is a header row or data row
            first_row = table.iloc[0].astype(str).tolist()
            first_cell = first_row[0] if len(first_row) > 0 else ""
            
            # If first table with merged headers, skip it
            if i == 0 and "Awarding" in first_cell and "FAIN" in first_cell:
                print(f"Skipping header table {i+1}")
                continue
                
            # If the first row looks like headers, drop it before standardising
            if any('award' in str(x).lower() for x in first_row):
                table = table.drop(index=0).reset_index(drop=True)

            table = standardize_headers(table)

            # Ensure all expected columns exist, filling missing ones with NA
            for col in expected_headers:
                if col not in table.columns:
                    table[col] = None

            # Reorder columns and keep any extras at the end
            extra_cols = [c for c in table.columns if c not in expected_headers]
            table = table[expected_headers + extra_cols]

            processed_tables.append(table)
        
        # Concatenate all processed tables
        if not processed_tables:
            print("ERROR: No valid data tables found after processing")
            return pd.DataFrame()
            
        df = pd.concat(processed_tables, ignore_index=True)
        print(f"Combined DataFrame shape: {df.shape}")
        
        # Remove rows that contain header text again
        header_text = "Awarding Office"
        df = df[~df['Awarding Office'].astype(str).str.contains(header_text)]
        
        # Debug column names
        print(f"Column names after extraction: {df.columns.tolist()}")
        
        # Merge continuation rows
        df = merge_continuation_rows(df)
        
        # Debug column names after merging rows
        print(f"Column names after merging rows: {df.columns.tolist()}")
        
        # Remove rows that still have header-like content
        df = df[~df.apply(lambda x: x.astype(str).str.contains('FAIN', case=False)).any(axis=1)]
        
        # Remove completely empty columns and rows
        df = df.dropna(axis=1, how='all')
        df = df.dropna(how='all')
        
        # Clean the DataFrame
        df = clean_dataframe(df)
        
        print(f"Final DataFrame shape: {df.shape}")
        print(f"Final column names: {df.columns.tolist()}")
        
        return df
    except Exception as e:
        print(f"ERROR extracting data from PDF: {str(e)}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()

def merge_continuation_rows(df):
    """
    Merge rows that are continuations of previous rows.
    A row is considered a continuation if it's missing key identifiers.
    """
    # Create a new dataframe to build our merged results
    result = []
    current_entry = None
    
    # Key columns that should have values in each main entry
    key_columns = ['Award Number', 'FAIN', 'Awarding Office', 'Recipient Name']
    # All columns that should be single-valued per entry
    important_columns = key_columns + ['Action Date (Date Terminated)', 'Award Title']
    
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        
        # Count how many key columns have values
        keys_with_values = sum(1 for col in key_columns 
                              if col in row_dict and not pd.isna(row_dict.get(col)) 
                              and str(row_dict.get(col, '')).strip() != '')
        
        # If at least two key columns have values, consider it a main entry
        # This is more robust than just checking Award Number
        is_main_entry = keys_with_values >= 2
        
        if is_main_entry:
            # If we were building a previous entry, save it
            if current_entry is not None:
                result.append(current_entry)
            
            # Start a new entry
            current_entry = row_dict
        else:
            # This is a continuation row - merge with current entry
            if current_entry is not None:
                for col in df.columns:
                    # Get the value of the continuation cell
                    cell_value = row_dict.get(col)
                    
                    # Skip empty cells in continuation rows
                    if pd.isna(cell_value) or str(cell_value).strip() == '':
                        continue
                    
                    # Process based on column type
                    if col in important_columns:
                        # For important columns, always append with space if not empty
                        if col in current_entry and isinstance(current_entry[col], str) and isinstance(cell_value, str):
                            if current_entry[col].strip() != '':
                                current_entry[col] = f"{current_entry[col]} {cell_value}"
                            else:
                                current_entry[col] = cell_value
                        elif col not in current_entry or pd.isna(current_entry[col]) or str(current_entry[col]).strip() == '':
                            current_entry[col] = cell_value
                    else:
                        # For other columns, use the same logic as before
                        if col in current_entry and isinstance(current_entry[col], str) and isinstance(cell_value, str):
                            current_entry[col] = f"{current_entry[col]} {cell_value}"
                        elif col not in current_entry or pd.isna(current_entry[col]) or str(current_entry[col]).strip() == '':
                            current_entry[col] = cell_value
    
    # Don't forget to add the last entry
    if current_entry is not None:
        result.append(current_entry)
    
    # Create a new DataFrame from the merged entries
    return pd.DataFrame(result)

def get_nih_reporter_data_individual(project_numbers,
                                     cache_file="cache/taggs/terminated_grants_reporter_cache.json.zst",
                                     not_found_cache_file="cache/taggs/not_found_cache.json.zst",
                                     limit=None):
    """
    Fetch grant details from NIH RePORTER API one by one with caching
    
    Args:
        project_numbers: List of grant project numbers
        cache_file: Path to the cache file
        not_found_cache_file: Path to cache file for project numbers not found in RePORTER
        limit: Maximum number of grants to process (None for all)
    
    Returns:
        List of results with required fields
    """
    base_url = "https://api.reporter.nih.gov/v2/projects/search"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    
    # Create cache directory if it doesn't exist
    cache_dir = os.path.dirname(cache_file)
    if not os.path.exists(cache_dir) and cache_dir:
        os.makedirs(cache_dir)
        print(f"Created cache directory: {cache_dir}")
    
    # Load regular cache if it exists
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                compressed = f.read()
            dctx = zstd.ZstdDecompressor()
            cache = json.loads(dctx.decompress(compressed).decode('utf-8'))
            print(f"Loaded {len(cache)} cached records from {cache_file}")
        except Exception as e:
            print(f"Error loading cache: {str(e)}")
            # Continue with empty cache if there's an error
    
    # Load not-found cache if it exists
    not_found_cache = []
    if os.path.exists(not_found_cache_file):
        try:
            with open(not_found_cache_file, 'rb') as f:
                compressed = f.read()
            dctx = zstd.ZstdDecompressor()
            not_found_cache = json.loads(dctx.decompress(compressed).decode('utf-8'))
            print(f"Loaded {len(not_found_cache)} not-found records from {not_found_cache_file}")
        except Exception as e:
            print(f"Error loading not-found cache: {str(e)}")
            # Continue with empty not-found cache if there's an error
    
    results = []
    
    # Limit the number of grants if specified
    if limit:
        project_numbers = project_numbers[:limit]
        print(f"Processing only the first {limit} grants for testing")
    
    total = len(project_numbers)
    not_found_count = 0
    cached_count = 0
    api_count = 0
    
    for i, project_num in enumerate(project_numbers):
        print(f"Processing grant {i+1}/{total}: {project_num}")
        
        # Check if in not-found cache
        if project_num in not_found_cache:
            print(f"  Skipping {project_num} (known to not exist in RePORTER)")
            not_found_count += 1
            continue
            
        # Check regular cache
        if project_num in cache:
            print(f"  Using cached data for {project_num}")
            results.append(cache[project_num])
            cached_count += 1
            continue
        
        payload = {
            "criteria": {
                "project_nums": [project_num]
            }
            # No include_fields to get ALL available fields
        }
        
        try:
            print(f"  Querying API for {project_num}...")
            api_count += 1
            response = requests.post(base_url, headers=headers, json=payload)
            
            if response.status_code == 200:
                data = response.json()
                
                # Process API response
                if data.get('results') and len(data['results']) > 0:
                    # Store the complete result
                    full_result = data['results'][0]
                    
                    # Create structured result with only the fields we need
                    result_data = {
                        "project_num": project_num,
                        "organization": {},
                        "principal_investigators": [],
                        "terms": "",
                        "agency_ic_fundings": []
                    }
                    
                    # Extract organization data
                    if 'organization' in full_result:
                        org = full_result['organization']
                        result_data["organization"] = {
                            "org_city": org.get('org_city', ''),
                            "org_state": org.get('org_state', ''),
                            "org_country": org.get('org_country', '')
                        }
                    
                    # Extract PI data
                    if 'principal_investigators' in full_result:
                        for pi in full_result['principal_investigators']:
                            if 'full_name' in pi:
                                result_data["principal_investigators"].append({
                                    "full_name": pi['full_name']
                                })
                    
                    # Extract terms
                    result_data["terms"] = full_result.get('terms', '')
                    
                    # Extract funding institutes
                    result_data["agency_ic_fundings"] = full_result.get('agency_ic_fundings', [])
                    
                    # Save complete result to cache
                    cache[project_num] = full_result
                    
                    # Use processed data for current operation
                    processed_result = {
                        "project_num": project_num,
                        "organization": result_data["organization"],
                        "principal_investigators": result_data["principal_investigators"],
                        "terms": result_data["terms"],
                        "agency_ic_fundings": result_data["agency_ic_fundings"]
                    }
                    results.append(processed_result)
                    
                    print(f"  Data found for {project_num}")
                else:
                    print(f"  No data found for {project_num} in RePORTER")
                    # Add to not-found cache
                    if project_num not in not_found_cache:
                        not_found_cache.append(project_num)
                        # Save not-found cache
                        with open(not_found_cache_file, 'wb') as f:
                            data = json.dumps(not_found_cache).encode('utf-8')
                            cctx = zstd.ZstdCompressor(level=3)
                            f.write(cctx.compress(data))
            else:
                print(f"  API error: {response.status_code} - {response.text}")
            
            # Save cache after each successful API call
            with open(cache_file, 'wb') as f:
                data = json.dumps(cache, indent=2).encode('utf-8')
                cctx = zstd.ZstdCompressor(level=3)
                f.write(cctx.compress(data))
            
            # Be nice to the API
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  Error processing {project_num}: {str(e)}")
    
    # Print summary statistics
    print("\nProcessing summary:")
    print(f"  Total grants checked: {total}")
    print(f"  Found in cache: {cached_count}")
    print(f"  Known not in RePORTER: {not_found_count}")
    print(f"  API calls made: {api_count}")
    print(f"  Results returned: {len(results)}")
    
    return results

def parse_project_terms(terms_string):
    """
    Parse project terms from format <term1><term2><term3> to a semicolon-separated string
    
    Args:
        terms_string: String containing terms in format <term1><term2>...
    
    Returns:
        Semicolon-separated string of terms
    """
    if not terms_string or not isinstance(terms_string, str):
        return ""
    
    # Remove the angle brackets and split into individual terms
    # The format is typically <term1><term2><term3>...
    terms_list = []
    
    # First, check if there are angle brackets
    if '<' in terms_string and '>' in terms_string:
        # Split by closing bracket and then remove opening brackets
        raw_terms = terms_string.split('>')
        for term in raw_terms:
            clean_term = term.replace('<', '').strip()
            if clean_term:  # Only add non-empty terms
                terms_list.append(clean_term)
    else:
        # If no angle brackets, just use the raw string
        terms_list = [terms_string.strip()]
    
    # Join with semicolons
    return '; '.join(terms_list)

def clean_project_number(project_num):
    """
    Clean project numbers to ensure compatibility with NIH RePORTER API.
    Removes spaces, handles formats like '5 K01 AG065440-05' properly.
    
    Args:
        project_num: The original grant/project number
    
    Returns:
        Cleaned project number suitable for NIH RePORTER lookups
    """
    if not project_num or not isinstance(project_num, str):
        return project_num
    
    # Remove all spaces
    cleaned = project_num.strip().replace(' ', '')
    
    # Handle special cases for NIH grants
    # NIH grants often follow format: <activity code><institute code><serial number>-<year/suffix>
    # We want to preserve this structure for the API lookup
    return cleaned

def enrich_dataframe_with_reporter_data(df, limit=None):
    """
    Add NIH RePORTER data to the DataFrame using individual processing with caching
    
    Args:
        df: DataFrame with grant data
        limit: Maximum number of grants to process (None for all)
    
    Returns:
        DataFrame with added RePORTER data
    """
    # Create a new column with cleaned project numbers
    df['Clean_Award_Number'] = df['Award Number'].apply(clean_project_number)
    
    # Use the clean project numbers for API lookups
    project_numbers = df['Clean_Award_Number'].dropna().unique().tolist()
    print(f"\nFound {len(project_numbers)} unique grant numbers")
    
    # Use both caches (regular and not-found)
    results = get_nih_reporter_data_individual(
        project_numbers,
        cache_file="cache/taggs/terminated_grants_reporter_cache.json.zst",
        not_found_cache_file="cache/taggs/not_found_cache.json.zst",
        limit=limit
    )
    
    print("\nProcessing results into DataFrame...")
    reporter_data = []
    
    for result in results:
        project_number = result.get('project_num', '')
        
        # Extract organization data
        org = result.get('organization', {})
        
        # Extract PI data
        pis = result.get('principal_investigators', [])
        pi_names = [pi.get('full_name', '') for pi in pis if pi.get('full_name')]
        pi_names_str = '; '.join(pi_names)
        
        # Extract terms
        raw_terms = result.get('terms', '')
        parsed_terms = parse_project_terms(raw_terms)
        
        # Extract funding institutes
        funding_institutes = []
        if isinstance(result, dict):
            # If result is from full API response
            if 'agency_ic_fundings' in result:
                fundings = result.get('agency_ic_fundings', [])
                for funding in fundings:
                    if isinstance(funding, dict):
                        name = funding.get('name', '')
                        abbreviation = funding.get('abbreviation', '')
                        if name and abbreviation:
                            funding_institutes.append(f"{abbreviation} - {name}")
            # If we're using a previously cached full result
            elif isinstance(result.get('agency_ic_fundings'), list):
                for funding in result['agency_ic_fundings']:
                    name = funding.get('name', '')
                    abbreviation = funding.get('abbreviation', '')
                    if name and abbreviation:
                        funding_institutes.append(f"{abbreviation} - {name}")
        
        funding_institutes_str = '; '.join(funding_institutes)
        
        if project_number:
            reporter_data.append({
                'Clean_Award_Number': project_number,
                'Organization_City': org.get('org_city', ''),
                'Organization_State': org.get('org_state', ''),
                'Organization_Country': org.get('org_country', ''),
                'PI_Names': pi_names_str,
                'Funding_Institutes': funding_institutes_str,
                'Project_Terms': parsed_terms
            })
    
    reporter_df = pd.DataFrame(reporter_data)
    
    if not reporter_df.empty:
        print(f"\nAdding RePORTER data for {len(reporter_df)} grants")
        print("Columns in reporter_df:", reporter_df.columns.tolist())
        
        # Use 'Clean_Award_Number' for joining instead of 'Award Number'
        merged_df = pd.merge(df, reporter_df, on='Clean_Award_Number', how='left')
        
        # Remove the temporary column used for joining
        merged_df = merged_df.drop(columns=['Clean_Award_Number'])
        
        # Count how many records were enriched
        enriched_count = merged_df[~merged_df['Organization_City'].isna()].shape[0]
        print(f"Successfully enriched {enriched_count} out of {df.shape[0]} records")
        
        return merged_df
    
    return df

def main():
    # Set up paths
    pdf_url = "https://taggs.hhs.gov/Content/Data/HHS_Grants_Terminated.pdf"
    base_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
    pdf_path = base_dir / "data" / "external" / "HHS_Grants_Terminated.pdf"
 
    # Download PDF
    print("Downloading PDF...")
    if download_pdf(pdf_url, pdf_path):
        print(f"PDF downloaded successfully to {pdf_path}")
        
        # Extract data
        print("Extracting data from PDF...")
        df = extract_data_from_pdf(pdf_path)
        
        # Check if DataFrame is valid
        if df.empty:
            print("ERROR: Failed to extract data from PDF. Exiting.")
            return
            
        # Check if required columns exist
        required_columns = ['Award Number']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            print(f"ERROR: Required columns are missing: {missing_columns}")
            print(f"Available columns: {df.columns.tolist()}")
            # Try to find alternative column names that might match
            for missing_col in missing_columns:
                # Check for similar column names
                similar_cols = [col for col in df.columns if missing_col.lower() in col.lower()]
                if similar_cols:
                    print(f"Found potential matches for '{missing_col}': {similar_cols}")
            return
        
        # Basic data cleaning
        df = df.dropna(how='all')  # Remove empty rows
        original_count = len(df)
        print(f"\nOriginal record count: {original_count}")

        # Enrich with NIH RePORTER data
        print("\nFetching additional data from NIH RePORTER...")
        enriched_df = enrich_dataframe_with_reporter_data(df)
        
        # FILTER: Only keep records that have RePORTER data
        filtered_df = enriched_df[~enriched_df['Organization_City'].isna()]
        filtered_count = len(filtered_df)
        
        print(f"\nFiltering results to only include RePORTER entries:")
        print(f"  - Original records: {original_count}")
        print(f"  - Records with RePORTER data: {filtered_count}")
        print(f"  - Records removed: {original_count - filtered_count}")
        
        # Display basic information about the filtered data
        print("\nFiltered DataFrame Info:")
        print(filtered_df.info())
        
        # Display first few rows
        print("\nFirst few rows of filtered data:")
        print(filtered_df.head(5))
        
        # Save the dataset of all HHS grants to the data folder
        results_dir = Path("data/processed/taggs")
        if not results_dir.exists():
            results_dir.mkdir(parents=True, exist_ok=True)
            
        full_csv_path = f"{results_dir}/hhs_grants_terminated.csv"
        enriched_df.to_csv(full_csv_path, 
                  index=False,
                  float_format='%.2f',
                  quoting=1,
                  encoding='utf-8',
                  lineterminator='\n')
        print(f"\nFull dataset saved to {full_csv_path}")
        
        # Also save a copy of the CSV file to pages/assets/csv
        assets_dir = Path("pages/assets/csv")
        if not assets_dir.exists():
            assets_dir.mkdir(parents=True, exist_ok=True)
        assets_csv_path = f"{assets_dir}/hhs_grants_terminated.csv"
        enriched_df.to_csv(assets_csv_path, 
                  index=False,
                  float_format='%.2f',
                  quoting=1,
                  encoding='utf-8',
                  lineterminator='\n')
        print(f"\nCSV copy saved to {assets_csv_path}")
        
    else:
        print("Failed to download PDF")

if __name__ == "__main__":
    main()
