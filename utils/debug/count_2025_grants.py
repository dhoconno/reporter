import requests
import datetime

url = "https://api.reporter.nih.gov/v2/projects/search"

# Get current date in the required format
current_date = datetime.datetime.now().strftime("%Y-%m-%d")

payload = {
    "criteria": {
        "award_notice_date": {
            "from_date": "2025-01-01",
            "to_date": current_date
        }
    },
    "offset": 0,
    "limit": 100,  # Adjust as needed (max 500)
    "fields": ["project_num", "project_title", "contact_pi_name", "award_amount", "award_notice_date"]
}

headers = {
    "Content-Type": "application/json"
}

response = requests.post(url, headers=headers, json=payload)

if response.status_code == 200:
    data = response.json()
    print(f"Found {data['meta']['total']} grants awarded from Jan 1, 2025 to {current_date}")
    # Print first few results
    for grant in data['results'][:5]:
        print(f"{grant['project_num']}: {grant['project_title']} - {grant['contact_pi_name']}")
else:
    print(f"Error: {response.status_code}")
    print(response.text)