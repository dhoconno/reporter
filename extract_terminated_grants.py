import tabula
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime
import time
import json

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
    
    # Add download date column
    df['Download Date'] = pd.to_datetime(datetime.now().date())
    
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

def get_nih_reporter_data_batch(project_numbers):
    """
    Fetch grant details from NIH RePORTER API in batches with debug logging
    """
    base_url = "https://api.reporter.nih.gov/v2/projects/search"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    
    # Split project numbers into chunks of 1000
    chunk_size = 1000
    results = []
    
    for i in range(0, len(project_numbers), chunk_size):
        chunk = project_numbers[i:i + chunk_size]
        
        payload = {
            "criteria": {
                "project_nums": chunk
            },
            "include_fields": [
                "ProjectNum",
                "ProjectTitle",
                "AbstractText",
                "ProjectStartDate",
                "ProjectEndDate",
                "TotalCost",
                "AwardAmount",
                "PrincipalInvestigators",
                "Organization"
            ]
        }
        
        try:
            print(f"\nMaking API request for {len(chunk)} grants...")
            print(f"Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(base_url, headers=headers, json=payload)
            print(f"Response status code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"Results found: {len(data.get('results', []))}")
                print(f"Sample result: {json.dumps(data['results'][0], indent=2) if data.get('results') else 'No results'}")
                
                if data['results']:
                    results.extend(data['results'])
            else:
                print(f"Error response: {response.text}")
                
            time.sleep(1)  # Rate limiting between batches
            
        except Exception as e:
            print(f"Error fetching batch: {str(e)}")
            print(f"Full error details: {e.__class__.__name__}")
    
    print(f"\nTotal results retrieved: {len(results)}")
    return results

def enrich_dataframe_with_reporter_data(df):
    """
    Add NIH RePORTER data to the DataFrame using batch processing with debug output
    """
    # Get all unique FAINs
    project_numbers = df['FAIN'].dropna().unique().tolist()
    print(f"\nAttempting to fetch data for {len(project_numbers)} unique grants...")
    print(f"First 5 grant numbers: {project_numbers[:5]}")
    
    # Get all results in batches
    results = get_nih_reporter_data_batch(project_numbers)
    
    # Debug output for results processing
    print("\nProcessing results into DataFrame...")
    reporter_data = []
    for result in results:
        project_number = result.get('ProjectNum', '')
        if project_number:
            reporter_data.append({
                'FAIN': project_number,
                'Abstract': result.get('AbstractText', ''),
                'Project_Start_Date': result.get('ProjectStartDate', ''),
                'Project_End_Date': result.get('ProjectEndDate', ''),
                'Total_Project_Cost': result.get('TotalCost', ''),
                'PI_Names': ', '.join([pi.get('FullName', '') for pi in result.get('PrincipalInvestigators', [])])
            })
    
    reporter_df = pd.DataFrame(reporter_data)
    print(f"\nCreated DataFrame with {len(reporter_df)} rows")
    
    if not reporter_df.empty:
        merged_df = pd.merge(df, reporter_df, on='FAIN', how='left')
        print(f"Merged DataFrame has {len(merged_df)} rows with {merged_df['Abstract'].notna().sum()} abstracts")
        return merged_df
    return df

# Test with the first 3 grants
def test_reporter_api():
    test_df = pd.DataFrame({
        'FAIN': [
            "1C06OD030152-01",
            "1C06OD034040-01", 
            "1C06OD034042-01"
        ]
    })
    
    print("\nTesting API with 3 sample grants...")
    enriched_df = enrich_dataframe_with_reporter_data(test_df)
    print("\nTest results:")
    print(enriched_df.to_string())
    
if __name__ == "__main__":
    test_reporter_api()

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

        # Enrich with NIH RePORTER data
        print("\nFetching additional data from NIH RePORTER...")
        df = enrich_dataframe_with_reporter_data(df)
        
        # Display basic information about the data
        print("\nDataFrame Info:")
        print(df.info())
        
        # Display first few rows
        print("\nFirst few rows of data:")
        print(df.head(50))
        
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