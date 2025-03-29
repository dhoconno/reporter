import requests
import json
from pprint import pprint

def get_nih_reporter_data(project_num):
    """
    Fetch detailed grant information from NIH RePORTER API for a specific award number
    """
    base_url = "https://api.reporter.nih.gov/v2/projects/search"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    
    # Create the payload with the project number
    payload = {
        "criteria": {
            "project_nums": [project_num]
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
            "ProjectTerms",
            "ApplId",
            "AgencyICs",
            "FundingMechs",
            "Foa",
            "CoreProjectNums",
            "Terms",
            "AwardNotices"
        ],
        "offset": 0,
        "limit": 1,
        "sort_field": "project_start_date",
        "sort_order": "desc"
    }
    
    try:
        print(f"Fetching data for project: {project_num}")
        response = requests.post(base_url, headers=headers, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('results'):
                print(f"Successfully fetched data for {project_num}")
                return data
            else:
                print("No results found")
                return {"message": "No results found for this project number"}
        else:
            print(f"Error: {response.status_code}")
            print(f"Response: {response.text}")
            return {"error": response.text}
    
    except Exception as e:
        print(f"Exception occurred: {str(e)}")
        return {"exception": str(e)}

def main():
    # Target award number
    award_number = "1C06OD030152-01"
    
    # Get the data
    result = get_nih_reporter_data(award_number)
    
    # Print the full result
    print("\nFull API Response:")
    print(json.dumps(result, indent=2))
    
    # Save the result to a JSON file
    with open(f"{award_number}_reporter_data.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nData saved to {award_number}_reporter_data.json")

if __name__ == "__main__":
    main()