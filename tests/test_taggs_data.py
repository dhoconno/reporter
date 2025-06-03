import pandas as pd
from pathlib import Path

CSV_PATH = Path("data/processed/taggs/hhs_grants_terminated.csv")


def test_taggs_csv_columns():
    df = pd.read_csv(CSV_PATH)
    expected_columns = [
        "Download Date",
        "Awarding Office",
        "FAIN",
        "Award Number",
        "Recipient Name",
        "Action Date (Date Terminated)",
        "Total Amount Obligated",
        "Total Amount Expended",
        "Total Payment Amount (As of Termination)",
        "Unliquidated Obligations (As of Termination)",
        "Award Title",
        "Presidential Action",
        "For Cause (Put X if applicable)",
        "Extra_0",
        "Extra_1",
        "Organization_City",
        "Organization_State",
        "Organization_Country",
        "PI_Names",
        "Funding_Institutes",
        "Project_Terms",
    ]
    assert df.columns.tolist() == expected_columns


def test_taggs_csv_minimum_rows():
    df = pd.read_csv(CSV_PATH)
    assert len(df) >= 500


def test_taggs_csv_sample_row_exists():
    df = pd.read_csv(CSV_PATH)
    mask = (
        df["Award Number"] == "90CA1896"
    ) & df["Recipient Name"].str.contains(
        "American Bar Association", case=False, na=False
    )
    assert mask.any()
