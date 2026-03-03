#!/usr/bin/env python3
"""
Attendance Checker - Headless CLI version for GitHub Actions.

Reads credentials from environment variables:
  HSOA_USERNAME            - Login username
  HSOA_PASSWORD            - Login password
  GOOGLE_CREDENTIALS_JSON  - Google service account JSON (as string)
  GOOGLE_SPREADSHEET_ID    - Google Sheets spreadsheet ID
  GOOGLE_SHEET_NAME        - Sheet name (default: Sheet1)
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Thread

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class Config:
    username: str = ""
    password: str = ""
    output_csv_file: Path = Path("attendance_report.csv")
    google_credentials_json: str = ""
    google_spreadsheet_id: str = ""
    google_sheet_name: str = "Sheet1"
    login_url: str = "https://hsoa.ordolms.com/"
    user_management_url: str = "https://hsoa.ordolms.com/home/userManagement"
    headless_mode: bool = True
    max_workers: int = 1
    page_load_timeout_seconds: int = 15
    implicit_wait_seconds: int = 3
    short_wait_seconds: int = 5


MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# ============================================================
# GOOGLE SHEETS
# ============================================================

def get_google_sheets_service(cfg: Config):
    if not GOOGLE_SHEETS_AVAILABLE:
        log.warning("Google Sheets libraries not installed.")
        return None
    if not cfg.google_credentials_json:
        log.warning("GOOGLE_CREDENTIALS_JSON environment variable not set.")
        return None
    try:
        info = json.loads(cfg.google_credentials_json)
        SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
        service = build("sheets", "v4", credentials=credentials)
        return service
    except Exception as e:
        log.warning("Error connecting to Google Sheets: %s", e)
        return None


def upload_to_google_sheets(cfg: Config, results: list) -> bool:
    service = get_google_sheets_service(cfg)
    if not service:
        log.warning("Could not connect to Google Sheets.")
        return False
    try:
        header = ["Student ID", "Attendance List", "Days Attended", "Total Time", "Total Seconds"]
        rows = [header]
        for result in results:
            if result["attendance_data"]:
                attendance_list = " | ".join(
                    f"{item['date']}: {item['time']}" for item in result["attendance_data"]
                )
            else:
                attendance_list = "No attendance data"
            total_time_formatted = format_time_from_seconds(result["total_seconds"])
            rows.append([
                result["student_id"],
                attendance_list,
                len(result["attendance_data"]),
                total_time_formatted,
                result["total_seconds"],
            ])

        spreadsheet_id = cfg.google_spreadsheet_id
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{cfg.google_sheet_name}!A:E",
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{cfg.google_sheet_name}!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
        log.info("Uploaded %d records to Google Sheets.", len(rows) - 1)
        return True
    except Exception as e:
        log.warning("Error uploading to Google Sheets: %s", e)
        return False

# ============================================================
# SELENIUM HELPERS
# ============================================================

def js_click(driver: webdriver.Chrome, element):
    driver.execute_script("arguments[0].click();", element)


def safe_click(driver: webdriver.Chrome, element, use_js: bool = True):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.2)
    except Exception:
        pass
    if use_js:
        js_click(driver, element)
    else:
        try:
            element.click()
        except ElementClickInterceptedException:
            js_click(driver, element)


def hide_clock_overlay(driver: webdriver.Chrome):
    try:
        driver.execute_script("""
            var clock = document.getElementById('clock');
            if (clock) { clock.style.display = 'none'; }
            var overlays = document.querySelectorAll('[id="clock"], .clock-overlay');
            overlays.forEach(function(el) { el.style.display = 'none'; });
        """)
    except Exception:
        pass

# ============================================================
# CHROMEDRIVER SETUP
# ============================================================

def setup_chrome_driver(cfg: Config, worker_id: int = 0):
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-popup-blocking")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    if cfg.headless_mode:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(cfg.page_load_timeout_seconds)
        driver.implicitly_wait(cfg.implicit_wait_seconds)
        return driver
    except Exception as e:
        log.error("Error setting up ChromeDriver (worker %d): %s", worker_id, e)
        return None

# ============================================================
# SELENIUM ACTIONS
# ============================================================

def login_to_hsoa(driver: webdriver.Chrome, cfg: Config) -> bool:
    try:
        driver.get(cfg.login_url)
        time.sleep(2)
        if "home" in driver.current_url or "dashboard" in driver.current_url:
            return True
        username_field = WebDriverWait(driver, cfg.short_wait_seconds).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        username_field.clear()
        username_field.send_keys(cfg.username)
        password_field = driver.find_element(By.NAME, "password")
        password_field.clear()
        password_field.send_keys(cfg.password)
        submit_button = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        safe_click(driver, submit_button)
        time.sleep(3)
        return "login" not in driver.current_url.lower()
    except Exception as e:
        log.error("Login error: %s", e)
        return False


def select_date_range(driver: webdriver.Chrome, start_date: str, end_date: str) -> bool:
    try:
        start_month, start_day, start_year = map(int, start_date.split("/"))
        end_month, end_day, end_year = map(int, end_date.split("/"))

        hide_clock_overlay(driver)
        time.sleep(0.5)

        date_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'input[data-placeholder="Ex. 2020/06/07"]')
            )
        )
        safe_click(driver, date_input)
        time.sleep(1)

        def select_specific_date(month: int, day: int, year: int):
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "mat-calendar"))
            )
            time.sleep(0.5)
            hide_clock_overlay(driver)

            header_button = driver.find_element(
                By.CSS_SELECTOR, "button.mat-calendar-period-button"
            )
            current_text = header_button.text.upper()
            target_month_abbr = MONTH_ABBR[month]

            if target_month_abbr not in current_text or str(year) not in current_text:
                safe_click(driver, header_button)
                time.sleep(0.5)
                hide_clock_overlay(driver)

                try:
                    year_cell = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, f'td[aria-label="{year}"]')
                        )
                    )
                    safe_click(driver, year_cell)
                    time.sleep(0.5)
                except Exception:
                    for cell in driver.find_elements(
                        By.CSS_SELECTOR, "td.mat-calendar-body-cell"
                    ):
                        if str(year) in cell.text:
                            safe_click(driver, cell)
                            time.sleep(0.5)
                            break

                hide_clock_overlay(driver)
                month_name = MONTH_NAMES[month]
                try:
                    month_cell = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, f'td[aria-label="{month_name} {year}"]')
                        )
                    )
                    safe_click(driver, month_cell)
                    time.sleep(0.5)
                except Exception:
                    for cell in driver.find_elements(
                        By.CSS_SELECTOR, "td.mat-calendar-body-cell"
                    ):
                        if target_month_abbr in cell.text.upper():
                            safe_click(driver, cell)
                            time.sleep(0.5)
                            break

            hide_clock_overlay(driver)
            time.sleep(0.3)

            month_name = MONTH_NAMES[month]
            day_aria_label = f"{month_name} {day}, {year}"
            try:
                day_cell = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, f'td[aria-label="{day_aria_label}"]')
                    )
                )
                safe_click(driver, day_cell)
                time.sleep(0.5)
            except Exception:
                for cell in driver.find_elements(
                    By.CSS_SELECTOR, "td.mat-calendar-body-cell"
                ):
                    cell_text = cell.text.strip()
                    if cell_text == str(day):
                        if "mat-calendar-body-disabled" not in cell.get_attribute("class"):
                            safe_click(driver, cell)
                            break
                time.sleep(0.5)

        select_specific_date(start_month, start_day, start_year)

        if not (start_month == end_month and start_year == end_year and start_day == end_day):
            hide_clock_overlay(driver)
            if start_month != end_month or start_year != end_year:
                select_specific_date(end_month, end_day, end_year)
            else:
                month_name = MONTH_NAMES[end_month]
                day_aria_label = f"{month_name} {end_day}, {end_year}"
                try:
                    day_cell = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, f'td[aria-label="{day_aria_label}"]')
                        )
                    )
                    safe_click(driver, day_cell)
                    time.sleep(0.5)
                except Exception:
                    for cell in driver.find_elements(
                        By.CSS_SELECTOR, "td.mat-calendar-body-cell"
                    ):
                        if cell.text.strip() == str(end_day):
                            if "mat-calendar-body-disabled" not in cell.get_attribute("class"):
                                safe_click(driver, cell)
                                break
                    time.sleep(0.5)

        return True
    except Exception as e:
        log.warning("Date selection error: %s", e)
        return False


def change_items_per_page(driver: webdriver.Chrome) -> bool:
    try:
        hide_clock_overlay(driver)
        items_selector = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'mat-select[aria-label*="Items per page"]')
            )
        )
        safe_click(driver, items_selector)
        time.sleep(0.5)
        options = driver.find_elements(By.CSS_SELECTOR, "mat-option")
        for option in options:
            text = option.text.strip()
            if text == "100" or text.lower() == "all":
                safe_click(driver, option)
                time.sleep(0.8)
                return True
        max_option = None
        max_value = -1
        for option in options:
            text = option.text.strip()
            if text.isdigit():
                val = int(text)
                if val > max_value:
                    max_value = val
                    max_option = option
        if max_option is not None:
            safe_click(driver, max_option)
            time.sleep(0.8)
            return True
        return False
    except Exception:
        return False


_TIME_RE = re.compile(
    r"(?:(?P<hours>\d+)\s*h\.)?\s*"
    r"(?:(?P<minutes>\d+)\s*min\.)?\s*"
    r"(?:(?P<seconds>\d+)\s*sec\.)?",
    re.IGNORECASE,
)


def parse_time_string(time_str: str) -> int:
    m = _TIME_RE.search(str(time_str).strip())
    if not m:
        return 0
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return hours * 3600 + minutes * 60 + seconds


def format_time_from_seconds(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def extract_attendance_data(driver: webdriver.Chrome) -> tuple:
    attendance_data = []
    total_seconds = 0
    try:
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "td.cdk-column-date.mat-column-date")
                )
            )
        except TimeoutException:
            return [], 0

        rows = driver.find_elements(By.CSS_SELECTOR, "tr.mat-row.cdk-row[role='row']")
        for row in rows:
            try:
                date_cells = row.find_elements(
                    By.CSS_SELECTOR, "td.cdk-column-date.mat-column-date"
                )
                time_cells = row.find_elements(
                    By.CSS_SELECTOR, "td.cdk-column-totalTime.mat-column-totalTime"
                )
                if not date_cells or not time_cells:
                    continue
                date_text = date_cells[0].text.strip()
                time_text = time_cells[0].text.strip()
                if not date_text or not time_text:
                    continue
                if (
                    "h." not in time_text
                    and "min." not in time_text
                    and "sec." not in time_text
                ):
                    continue
                seconds = parse_time_string(time_text)
                total_seconds += seconds
                attendance_data.append(
                    {"date": date_text, "time": time_text, "seconds": seconds}
                )
            except Exception:
                continue
        return attendance_data, total_seconds
    except Exception:
        return [], 0


def process_student(
    driver: webdriver.Chrome,
    student_id: str,
    start_date: str,
    end_date: str,
    cfg: Config,
) -> dict:
    result = {
        "student_id": student_id,
        "attendance_data": [],
        "total_seconds": 0,
        "success": False,
    }
    try:
        driver.get(cfg.user_management_url)
        time.sleep(2)
        hide_clock_overlay(driver)

        search_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'input[data-placeholder*="Pedro"]')
            )
        )
        search_input.clear()
        search_input.send_keys(student_id)
        time.sleep(1.5)
        hide_clock_overlay(driver)

        settings_icon = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.XPATH, '//mat-icon[contains(text(), "settings")]')
            )
        )
        safe_click(driver, settings_icon)
        time.sleep(2)

        main_window = driver.current_window_handle
        new_window = next(
            (w for w in driver.window_handles if w != main_window), None
        )
        if not new_window:
            return result

        driver.switch_to.window(new_window)
        time.sleep(1.5)
        hide_clock_overlay(driver)

        try:
            attendance_tab = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//div[contains(text(), "Attendance")]')
                )
            )
            safe_click(driver, attendance_tab)
            time.sleep(2)
        except TimeoutException:
            driver.close()
            driver.switch_to.window(main_window)
            return result

        hide_clock_overlay(driver)

        if not select_date_range(driver, start_date, end_date):
            log.warning("Date selection may have failed for %s", student_id)

        hide_clock_overlay(driver)

        try:
            apply_buttons = driver.find_elements(
                By.XPATH,
                '//button[contains(text(), "Apply") or contains(text(), "Filter")]',
            )
            if apply_buttons:
                safe_click(driver, apply_buttons[0])
                time.sleep(1.5)
        except Exception:
            pass

        change_items_per_page(driver)
        attendance_data, total_seconds = extract_attendance_data(driver)
        result["attendance_data"] = attendance_data
        result["total_seconds"] = total_seconds
        result["success"] = True

        driver.close()
        driver.switch_to.window(main_window)
        time.sleep(0.6)
        return result

    except Exception as e:
        log.error("Error processing %s: %s", student_id, e)
        try:
            while len(driver.window_handles) > 1:
                driver.switch_to.window(driver.window_handles[-1])
                driver.close()
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass
        return result

# ============================================================
# CSV OUTPUT
# ============================================================

def ensure_csv_header(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Student ID", "Attendance List", "Days Attended", "Total Time", "Total Seconds"]
        )


def write_result_to_csv(path: Path, result: dict) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if result["attendance_data"]:
            attendance_list = " | ".join(
                f"{item['date']}: {item['time']}" for item in result["attendance_data"]
            )
        else:
            attendance_list = "No attendance data"
        days_attended = len(result["attendance_data"])
        total_time_formatted = format_time_from_seconds(result["total_seconds"])
        writer.writerow(
            [
                result["student_id"],
                attendance_list,
                days_attended,
                total_time_formatted,
                result["total_seconds"],
            ]
        )

# ============================================================
# WORKER
# ============================================================

def worker_process_students(
    worker_id: int,
    student_ids: list,
    start_date: str,
    end_date: str,
    cfg: Config,
    results_queue: Queue,
) -> None:
    log.info("[Worker %d] Starting...", worker_id)
    driver = setup_chrome_driver(cfg, worker_id)
    if not driver:
        log.error("[Worker %d] Failed to start browser.", worker_id)
        return
    try:
        if not login_to_hsoa(driver, cfg):
            log.warning("[Worker %d] Login may have failed, continuing...", worker_id)
        for student_id in student_ids:
            log.info("[Worker %d] Processing: %s", worker_id, student_id)
            result = process_student(driver, student_id, start_date, end_date, cfg)
            results_queue.put(result)
            days = len(result["attendance_data"])
            time_str = format_time_from_seconds(result["total_seconds"])
            log.info(
                "[Worker %d] Done: %s - %d days, %s",
                worker_id,
                student_id,
                days,
                time_str,
            )
    except Exception as e:
        log.error("[Worker %d] Error: %s", worker_id, e)
    finally:
        driver.quit()
        log.info("[Worker %d] Finished.", worker_id)

# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Check attendance records on HSOA LMS and export to CSV."
    )
    parser.add_argument(
        "--students",
        required=True,
        help="Comma-, space-, or newline-separated student IDs, or path to a file with one ID per line.",
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date in MM/DD/YYYY format.",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="End date in MM/DD/YYYY format.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel browser workers (default: 1).",
    )
    parser.add_argument(
        "--output",
        default="attendance_report.csv",
        help="Output CSV file path (default: attendance_report.csv).",
    )
    parser.add_argument(
        "--upload-sheets",
        action="store_true",
        help="Upload results to Google Sheets after processing.",
    )
    return parser.parse_args()


  def load_student_ids(students_arg: str) -> list:
    """Return list of student IDs from a comma/space/newline-separated string or file path."""
    # Only check if it's a file if the string is short enough to be a valid path
    # Linux max filename is 255 chars, max path is 4096 chars
    if len(students_arg) <= 4096:
        try:
            path = Path(students_arg)
            if path.is_file():
                ids = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
                return [sid for sid in ids if sid]
        except OSError:
            pass  # Not a valid path, treat as student ID string

    # Split on commas, spaces, or newlines
    ids = re.split(r'[,\s]+', students_arg)
    return [sid.strip() for sid in ids if sid.strip()]  
  
  def build_config(args) -> Config:
    username = os.environ.get("HSOA_USERNAME", "")
    password = os.environ.get("HSOA_PASSWORD", "")
    if not username or not password:
        log.error(
            "HSOA_USERNAME and HSOA_PASSWORD environment variables must be set."
        )
        sys.exit(1)

    return Config(
        username=username,
        password=password,
        output_csv_file=Path(args.output),
        google_credentials_json=os.environ.get("GOOGLE_CREDENTIALS_JSON", ""),
        google_spreadsheet_id=os.environ.get("GOOGLE_SPREADSHEET_ID", ""),
        google_sheet_name=os.environ.get("GOOGLE_SHEET_NAME", "Sheet1"),
        max_workers=args.workers,
    )


def distribute(items: list, n: int) -> list:
    """Split items into n roughly equal chunks."""
    chunks = [[] for _ in range(n)]
    for i, item in enumerate(items):
        chunks[i % n].append(item)
    return chunks


def main():
    args = parse_args()
    student_ids = load_student_ids(args.students)
    if not student_ids:
        log.error("No student IDs provided.")
        sys.exit(1)

    cfg = build_config(args)
    output_path = cfg.output_csv_file

    log.info(
        "Processing %d student(s) with %d worker(s).",
        len(student_ids),
        cfg.max_workers,
    )
    log.info("Date range: %s - %s", args.start_date, args.end_date)

    ensure_csv_header(output_path)
    results_queue: Queue = Queue()
    num_workers = min(cfg.max_workers, len(student_ids))
    chunks = distribute(student_ids, num_workers)

    threads = []
    for worker_id, chunk in enumerate(chunks):
        t = Thread(
            target=worker_process_students,
            args=(worker_id, chunk, args.start_date, args.end_date, cfg, results_queue),
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    all_results = []
    while not results_queue.empty():
        result = results_queue.get()
        all_results.append(result)
        write_result_to_csv(output_path, result)

    log.info("Results written to %s", output_path)

    if args.upload_sheets:
        if not cfg.google_spreadsheet_id:
            log.warning(
                "GOOGLE_SPREADSHEET_ID not set; skipping Google Sheets upload."
            )
        else:
            upload_to_google_sheets(cfg, all_results)

    success_count = sum(1 for r in all_results if r["success"])
    log.info(
        "Done. %d/%d students processed successfully.", success_count, len(student_ids)
    )


if __name__ == "__main__":
    main()
