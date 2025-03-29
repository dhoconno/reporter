import tabula
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime
import time
import json
import os
import xml.etree.ElementTree as ET

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
    tables = tabula.read_pdf(
        pdf_path,
        pages='all',
        multiple_tables=True,
        pandas_options={'header': None}
    )
    df = pd.concat(tables, ignore_index=True)
    # Use the first row as headers, then remove it from the DataFrame
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    # Remove rows where "Awarding Office" equals "Awarding Office" or any column contains "FAIN"
    df = df[~df.apply(lambda x: x.astype(str).str.contains('FAIN', case=False)).any(axis=1)]
    # Remove completely empty columns
    df = df.dropna(axis=1, how='all')
    # Clean the DataFrame
    df = clean_dataframe(df)
    return df

def get_nih_reporter_data_individual(project_numbers, cache_file="terminated_cache/terminated_grants_reporter_cache.json", limit=None):
    """
    Fetch grant details from NIH RePORTER API one by one with caching
    
    Args:
        project_numbers: List of grant project numbers
        cache_file: Path to the cache file
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
    
    # Load cache if it exists
    cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            print(f"Loaded {len(cache)} cached records from {cache_file}")
        except Exception as e:
            print(f"Error loading cache: {str(e)}")
            # Continue with empty cache if there's an error
    
    results = []
    
    # Limit the number of grants if specified
    if limit:
        project_numbers = project_numbers[:limit]
        print(f"Processing only the first {limit} grants for testing")
    
    total = len(project_numbers)
    
    for i, project_num in enumerate(project_numbers):
        print(f"Processing grant {i+1}/{total}: {project_num}")
        
        # Check cache first
        if project_num in cache:
            print(f"  Using cached data for {project_num}")
            results.append(cache[project_num])
            continue
        
        payload = {
            "criteria": {
                "project_nums": [project_num]
            }
            # No include_fields to get ALL available fields
        }
        
        try:
            print(f"  Querying API for {project_num}...")
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
                    print(f"  No data found for {project_num}")
            else:
                print(f"  API error: {response.status_code} - {response.text}")
            
            # Save cache after each successful API call
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2)
            
            # Be nice to the API
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  Error processing {project_num}: {str(e)}")
    
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

def enrich_dataframe_with_reporter_data(df, limit=None):
    """
    Add NIH RePORTER data to the DataFrame using individual processing with caching
    
    Args:
        df: DataFrame with grant data
        limit: Maximum number of grants to process (None for all)
    
    Returns:
        DataFrame with added RePORTER data
    """
    project_numbers = df['Award Number'].dropna().unique().tolist()
    print(f"\nFound {len(project_numbers)} unique grant numbers")
    
    results = get_nih_reporter_data_individual(project_numbers, limit=limit)
    
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
                'Award Number': project_number,
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
        
        # Use 'Award Number' for joining
        merged_df = pd.merge(df, reporter_df, on='Award Number', how='left')
        
        # Count how many records were enriched
        enriched_count = merged_df[~merged_df['Organization_City'].isna()].shape[0]
        print(f"Successfully enriched {enriched_count} out of {df.shape[0]} records")
        
        return merged_df
    
    return df

def main():
    # Set up paths
    pdf_url = "https://taggs.hhs.gov/Content/Data/HHS_Grants_Terminated.pdf"
    pdf_path = Path("HHS_Grants_Terminated.pdf")
    
    # Download PDF
    print("Downloading PDF...")
    if download_pdf(pdf_url, pdf_path):
        print(f"PDF downloaded successfully to {pdf_path}")
        
        # Extract data
        print("Extracting data from PDF...")
        df = extract_data_from_pdf(pdf_path)
        
        # Basic data cleaning
        df = df.dropna(how='all')  # Remove empty rows

        # Enrich with NIH RePORTER data - start with just 10 entries
        print("\nFetching additional data from NIH RePORTER...")
        # df = enrich_dataframe_with_reporter_data(df, limit=10) #testing with 10 entries
        # Uncomment the following line to process all entries
        df = enrich_dataframe_with_reporter_data(df)
        
        # Display basic information about the data
        print("\nDataFrame Info:")
        print(df.info())
        
        # Display first few rows
        print("\nFirst few rows of data:")
        print(df.head(10))
        
        # Save to CSV with specific formatting
        csv_path = pdf_path.with_suffix('.csv')
        df.to_csv(csv_path, 
                  index=False,
                  float_format='%.2f',  # Format numbers with 2 decimal places
                  quoting=1,           # Quote only when necessary
                  encoding='utf-8',
                  lineterminator='\n')  # Ensure consistent line endings
        print(f"\nData saved to {csv_path}")
        
    else:
        print("Failed to download PDF")

if __name__ == "__main__":
    main()