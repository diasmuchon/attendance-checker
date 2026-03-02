# HSOA Attendance Checker

A headless, GitHub Actions-compatible script that logs into the HSOA LMS, extracts attendance data for a list of students over a date range, writes results to CSV, and optionally uploads them to Google Sheets.

## Features

- Headless Chrome via `webdriver-manager` (no manual ChromeDriver setup)
- Command-line interface with `argparse`
- Parallel processing with configurable worker count
- CSV output and optional Google Sheets upload
- Runs on GitHub Actions via `workflow_dispatch`

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/<your-org>/attendance-checker.git
cd attendance-checker
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Export the following environment variables before running locally:

| Variable | Description |
|---|---|
| `HSOA_USERNAME` | LMS login username |
| `HSOA_PASSWORD` | LMS login password |
| `GOOGLE_CREDENTIALS_JSON` | Google service account JSON (as a string) |
| `GOOGLE_SPREADSHEET_ID` | Target Google Sheets spreadsheet ID |
| `GOOGLE_SHEET_NAME` | Sheet tab name (default: `Sheet1`) |

```bash
export HSOA_USERNAME="your_username"
export HSOA_PASSWORD="your_password"
# Optional – only needed for Google Sheets upload
export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
export GOOGLE_SPREADSHEET_ID="your_spreadsheet_id"
export GOOGLE_SHEET_NAME="Sheet1"
```

---

## Running Locally

```bash
python attendance_checker.py \
  --students "student1,student2,student3" \
  --start-date "01/01/2025" \
  --end-date "03/31/2025" \
  --workers 2 \
  --output attendance_report.csv
```

### CLI Arguments

| Argument | Required | Description |
|---|---|---|
| `--students` | Yes | Comma-separated student IDs **or** path to a file with one ID per line |
| `--start-date` | Yes | Start date in `MM/DD/YYYY` format |
| `--end-date` | Yes | End date in `MM/DD/YYYY` format |
| `--workers` | No | Number of parallel browser workers (default: `1`) |
| `--output` | No | Output CSV file path (default: `attendance_report.csv`) |
| `--upload-sheets` | No | Flag to upload results to Google Sheets |

### Using a student ID file

Create a `students.txt` file with one ID per line:

```
student001
student002
student003
```

Then pass the file path:

```bash
python attendance_checker.py --students students.txt --start-date "01/01/2025" --end-date "03/31/2025"
```

---

## GitHub Actions

### Configure Secrets

Go to **Settings → Secrets and variables → Actions** in your repository and add:

| Secret | Description |
|---|---|
| `HSOA_USERNAME` | LMS login username |
| `HSOA_PASSWORD` | LMS login password |
| `GOOGLE_CREDENTIALS_JSON` | Google service account JSON string (optional) |
| `GOOGLE_SPREADSHEET_ID` | Google Sheets spreadsheet ID (optional) |
| `GOOGLE_SHEET_NAME` | Sheet tab name (optional, defaults to `Sheet1`) |

### Trigger the Workflow

1. Go to **Actions → Check Attendance** in your repository.
2. Click **Run workflow**.
3. Fill in the inputs:
   - **Student IDs** – comma-separated list (or leave blank to use a `students.txt` file committed to the repo)
   - **Start date** – e.g. `01/01/2025`
   - **End date** – e.g. `03/31/2025`
   - **Workers** – number of parallel browsers (default: `1`)
   - **Upload to Google Sheets** – check to upload results
4. Click **Run workflow**.

After the run completes, download the `attendance-report` artifact from the workflow run summary to get the CSV file.

---

## Output Format

The output CSV contains the following columns:

| Column | Description |
|---|---|
| Student ID | The student identifier |
| Attendance List | All attendance entries (`date: time` separated by `\|`) |
| Days Attended | Count of attendance records |
| Total Time | Formatted total time (`HH:MM:SS`) |
| Total Seconds | Raw total time in seconds |
