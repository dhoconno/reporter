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
    Fetch grant details from NIH RePORTER API in batches with XML response format
    """
    base_url = "https://api.reporter.nih.gov/v2/projects/search"
    headers = {
        "accept": "application/xml",  # Request XML instead of JSON
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
                "Organization",
                "ProjectTerms"
            ]
        }
        
        try:
            print(f"\nMaking API request for {len(chunk)} grants...")
            print(f"Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(base_url, headers=headers, json=payload)
            print(f"Response status code: {response.status_code}")
            
            if response.status_code == 200:
                # Save raw XML for inspection (for the first batch only)
                if i == 0:
                    with open("reporter_sample_response.xml", "w", encoding="utf-8") as f:
                        f.write(response.text)
                    print(f"Sample XML response saved to reporter_sample_response.xml")
                
                # Parse XML to extract required information
                try:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(response.text)
                    
                    # Extract results from the XML
                    result_elements = root.findall(".//result")
                    print(f"Results found in XML: {len(result_elements)}")
                    
                    # Process each result
                    for result_elem in result_elements:
                        result_data = {
                            "project_num": result_elem.find("project_num").text if result_elem.find("project_num") is not None else "",
                            "organization": {},
                            "principal_investigators": [],
                            "terms": result_elem.find("terms").text if result_elem.find("terms") is not None else ""
                        }
                        
                        # Extract organization data
                        org_elem = result_elem.find("organization")
                        if org_elem is not None:
                            result_data["organization"] = {
                                "org_city": org_elem.find("org_city").text if org_elem.find("org_city") is not None else "",
                                "org_state": org_elem.find("org_state").text if org_elem.find("org_state") is not None else "",
                                "org_country": org_elem.find("org_country").text if org_elem.find("org_country") is not None else ""
                            }
                        
                        # Extract PI data
                        pi_elems = result_elem.findall(".//principal_investigators/principal_investigator")
                        for pi_elem in pi_elems:
                            if pi_elem.find("full_name") is not None:
                                result_data["principal_investigators"].append({
                                    "full_name": pi_elem.find("full_name").text
                                })
                        
                        results.append(result_data)
                        
                    # Print sample result for inspection (first result only)
                    if results:
                        print("Sample parsed result structure:")
                        print(json.dumps(results[0], indent=2))
                        
                except Exception as xml_err:
                    print(f"Error parsing XML: {str(xml_err)}")
                    # Save problematic XML for debugging
                    with open("error_xml_response.xml", "w", encoding="utf-8") as f:
                        f.write(response.text)
                    print("Problematic XML saved to error_xml_response.xml")
            else:
                print(f"Error response: {response.text}")
                
            time.sleep(1)
            
        except Exception as e:
            print(f"Error fetching batch: {str(e)}")
            print(f"Full error details: {e.__class__.__name__}")
    
    return results

def enrich_dataframe_with_reporter_data(df):
    """
    Add NIH RePORTER data to the DataFrame using batch processing with debug output
    """
    project_numbers = df['Award Number'].dropna().unique().tolist()
    print(f"\nAttempting to fetch data for {len(project_numbers)} unique grants...")
    
    results = get_nih_reporter_data_batch(project_numbers)
    
    print("\nProcessing results into DataFrame...")
    reporter_data = []
    
    # First, print a complete sample result to inspect the structure
    if results:
        print("\nComplete sample result for field mapping:")
        print(json.dumps(results[0], indent=2))
    
    for result in results:
        project_number = result.get('project_num', '')
        org = result.get('organization', {})
        pis = result.get('principal_investigators', [])
        
        # Get PI names as semicolon-separated string
        pi_names = [pi.get('full_name', '') for pi in pis if pi.get('full_name')]
        pi_names_str = '; '.join(pi_names)
        
        # Get terms as raw string
        terms = result.get('terms', '')
        
        if project_number:
            reporter_data.append({
                'Award Number': project_number,
                'Organization_City': org.get('org_city', ''),
                'Organization_State': org.get('org_state', ''),
                'Organization_Country': org.get('org_country', ''),
                'PI_Names': pi_names_str,
                'Project_Terms': terms
            })
    
    reporter_df = pd.DataFrame(reporter_data)
    
    if not reporter_df.empty:
        print("\nColumns in reporter_df:", reporter_df.columns.tolist())
        print("\nColumns in original df:", df.columns.tolist())
        
        # Use 'Award Number' for joining
        merged_df = pd.merge(df, reporter_df, on='Award Number', how='left')
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