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
