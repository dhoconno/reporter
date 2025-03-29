import tabula
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime

def download_pdf(url, output_path):
    """Download PDF file from URL"""
    response = requests.get(url)
    if response.status_code == 200:
        with open(output_path, 'wb') as f:
            f.write(response.content)
        return True
    return False

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
    # Remove rows where "Awarding Office" equals "Awarding Office"
    df = df[df["Awarding Office"] != "Awarding Office"]
    # Remove completely empty columns
    df = df.dropna(axis=1, how='all')
    # Clean the DataFrame
    df = clean_dataframe(df)
    return df

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
    # Read all tables from the PDF
    tables = tabula.read_pdf(
        pdf_path,
        pages='all',
        multiple_tables=True,
        pandas_options={'header': None}
    )
    
    # Combine all tables into one DataFrame
    df = pd.concat(tables, ignore_index=True)
    
    # The first row typically contains headers
    headers = df.iloc[0]
    df = df[1:]  # Remove the header row
    df.columns = headers  # Set the headers
    
    # Reset index after removing header row
    df = df.reset_index(drop=True)
    
    # Remove columns that contain only NaN values
    df = df.dropna(axis=1, how='all')
    
    # Clean the DataFrame
    df = clean_dataframe(df)
    
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
        
        # Display basic information about the data
        print("\nDataFrame Info:")
        print(df.info())
        
        # Display first few rows
        print("\nFirst few rows of data:")
        print(df.head())
        
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