import os
import subprocess
import time
import json
import shutil
import re
import csv
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor


"""BigQuery Reporting Automation

Enterprise-friendly reporting automation for BigQuery.

- Schedules SQL report jobs from jobs.json
- Runs queries into temp tables
- Exports results to GCS (wildcard shards)
- Downloads and merges CSV shards locally
- Cleans up temp resources (BQ + GCS + local)

Designed for enterprise environments:
- No heavy Python dependencies (stdlib-only)
- Works with portable Google Cloud CLI (ZIP/versioned archive)
- Supports proxy + trusted CA setup (e.g., Zscaler TLS inspection)
"""

# ================== USER CONFIG ==================

PROJECT = "YOUR_PROJECT_ID"
DATASET = "YOUR_DATASET"
LOCATION = "YOUR_REGION"  # e.g. "europe-west2"

BUCKET = "YOUR_GCS_BUCKET/YOUR_OPTIONAL_PREFIX"

SAFE_TEMP_DIR = r"C:\\path\\to\\bq_temp_downloads"
FINAL_OUTPUT_BASE = r"C:\\path\\to\\final_output"

BQ = r"C:\\path\\to\\google-cloud-sdk\\bin\\bq.cmd"
GSUTIL = BQ.replace("bq.cmd", "gsutil.cmd")
GCLOUD = BQ.replace("bq.cmd", "gcloud.cmd")

# Defaults (can be overridden by jobs.json config in V1 format)
MAX_PARALLEL = 12
RETRY_COUNT = 2

STALE_CLEANUP_HOURS = 24
MOVE_RETRY_COUNT = 10
MOVE_RETRY_DELAY = 3

DASHBOARD_REFRESH_SECONDS = 5
QUERY_TIMEOUT_SECONDS = 60 * 60        # 1 hour
EXTRACT_TIMEOUT_SECONDS = 30 * 60      # 30 minutes

# V1: default download timeout (override in jobs.json config)
DOWNLOAD_TIMEOUT_SECONDS = 15 * 60     # 15 minutes default (V1)

# V1: idle cleanup scheduler frequency
CLEANUP_EVERY_MINUTES = 180

# ==================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUERIES_DIR = os.path.join(BASE_DIR, "queries")
LOG_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_FILE = os.path.join(BASE_DIR, "jobs.json")

os.makedirs(SAFE_TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ================== THREAD-SAFE LIVE STATUS ==================

STATUS_LOCK = threading.Lock()
JOB_STATUS = {}
PRINT_LOCK = threading.Lock()

# V1: track active running jobs so cleanup runs only in idle time
ACTIVE_JOBS_LOCK = threading.Lock()
ACTIVE_JOBS_COUNT = 0

# V1: prevent duplicate triggers inside scheduler
LAST_RUN_MAP_LOCK = threading.Lock()
LAST_RUN_MAP = {}  # key: (date_str, sql) -> datetime


def inc_active_jobs():
    global ACTIVE_JOBS_COUNT
    with ACTIVE_JOBS_LOCK:
        ACTIVE_JOBS_COUNT += 1


def dec_active_jobs():
    global ACTIVE_JOBS_COUNT
    with ACTIVE_JOBS_LOCK:
        ACTIVE_JOBS_COUNT = max(0, ACTIVE_JOBS_COUNT - 1)


def get_active_jobs_count():
    with ACTIVE_JOBS_LOCK:
        return ACTIVE_JOBS_COUNT


def set_status(sql_name, status, detail=""):
    with STATUS_LOCK:
        existing = JOB_STATUS.get(sql_name, {})
        JOB_STATUS[sql_name] = {
            "status": status,
            "detail": detail,
            "updated": datetime.now().strftime("%H:%M:%S"),
            "start": existing.get("start", datetime.now().strftime("%H:%M:%S"))
        }


def get_status_snapshot():
    with STATUS_LOCK:
        return dict(JOB_STATUS)


# ================== UI / LOGGING ==================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg, console=True):
    line = f"[{now_str()}] {msg}"

    if console:
        with PRINT_LOCK:
            print(line, flush=True)

    log_file = os.path.join(LOG_DIR, f"bq_automation_{datetime.now().strftime('%Y%m%d')}.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def banner():
    os.system("title BigQuery Reporting Automation")

    print("\n" + "=" * 90)
    print("🚀 BIGQUERY AUTOMATION PIPELINE (V1 ENTERPRISE)")
    print("=" * 90)
    print(f"📁 Base folder       : {BASE_DIR}")
    print(f"📁 Queries folder    : {QUERIES_DIR}")
    print(f"📁 Logs folder       : {LOG_DIR}")
    print(f"📁 Output base       : {FINAL_OUTPUT_BASE}")
    print(f"☁️ Project           : {PROJECT}")
    print(f"🧪 Dataset           : {DATASET}")
    print(f"🌍 Location          : {LOCATION}")
    print(f"⏳ Download timeout  : {int(DOWNLOAD_TIMEOUT_SECONDS/60)} minutes (default; configurable in jobs.json)")
    print("=" * 90 + "\n")


def section(title):
    with PRINT_LOCK:
        print("\n" + "-" * 90)
        print(title)
        print("-" * 90)


def dashboard_running(stop_event):
    while not stop_event.is_set():
        try:
            os.system("cls")
            snapshot = get_status_snapshot()

            print("=" * 115)
            print("🚀 BIGQUERY AUTOMATION - LIVE EXECUTION STATUS (V1)")
            print("=" * 115)
            print(f"Last refresh : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Max parallel : {MAX_PARALLEL} | Retry count: {RETRY_COUNT} | Active jobs: {get_active_jobs_count()}")
            print("-" * 115)

            if not snapshot:
                print("No active jobs currently.")
            else:
                for sql_name in sorted(snapshot.keys()):
                    info = snapshot[sql_name]
                    print(f"{sql_name}")
                    print(f"   Status  : {info.get('status', '')}")
                    print(f"   Detail  : {info.get('detail', '')}")
                    print(f"   Updated : {info.get('updated', '')}")
                    print("-" * 115)

            print("Press Ctrl+C to stop automation.")
        except Exception:
            pass

        stop_event.wait(DASHBOARD_REFRESH_SECONDS)


# ================== SUMMARY ==================

def get_summary_file():
    return os.path.join(LOG_DIR, f"summary_{datetime.now().strftime('%d.%m.%y')}.csv")


def write_summary(sql_name, status, start_time=None, end_time=None, duration_sec=None, output_file=None, error=None):
    summary_file = get_summary_file()
    file_exists = os.path.exists(summary_file)

    with open(summary_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "event_time",
                "sql_file",
                "status",
                "start_time",
                "end_time",
                "duration_seconds",
                "output_file",
                "error"
            ])

        writer.writerow([
            now_str(),
            sql_name,
            status,
            start_time or "",
            end_time or "",
            duration_sec if duration_sec is not None else "",
            output_file or "",
            error or ""
        ])


# ================== HELPERS ==================

def validate_paths():
    missing = []

    if not os.path.exists(BQ):
        missing.append(f"bq.cmd not found: {BQ}")

    if not os.path.exists(GSUTIL):
        missing.append(f"gsutil.cmd not found: {GSUTIL}")

    if not os.path.exists(GCLOUD):
        missing.append(f"gcloud.cmd not found: {GCLOUD}")

    if not os.path.exists(QUERIES_DIR):
        missing.append(f"queries folder not found: {QUERIES_DIR}")

    if not os.path.exists(CONFIG_FILE):
        missing.append(f"jobs.json not found: {CONFIG_FILE}")

    if missing:
        for m in missing:
            log(f"❌ {m}")
        raise Exception("Path validation failed")

    log("✅ Path validation completed")


def safe_name(name):
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned


def run_list(cmd, step_name, timeout_seconds=None, show_success_output=False):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired:
        raise Exception(f"{step_name} timed out after {timeout_seconds} seconds")

    if result.returncode != 0:
        if result.stdout:
            log(f"{step_name} STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            log(f"{step_name} STDERR:\n{result.stderr.strip()}")
        raise Exception(f"{step_name} failed")

    if show_success_output:
        if result.stdout:
            log(f"{step_name} STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            log(f"{step_name} STDERR:\n{result.stderr.strip()}")

    return result.stdout


def cleanup_resource(cmd, resource_name, sql_name=None):
    try:
        if sql_name:
            set_status(sql_name, "CLEANUP", resource_name)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            log(f"🧹 Deleted: {resource_name}", console=False)
            if sql_name:
                set_status(sql_name, "CLEANUP DONE", resource_name)
        else:
            log(f"⚠️ Cleanup failed for {resource_name}", console=False)
            if result.stdout:
                log(result.stdout.strip(), console=False)
            if result.stderr:
                log(result.stderr.strip(), console=False)
    except Exception as e:
        log(f"⚠️ Cleanup exception for {resource_name} → {e}", console=False)


def format_duration(seconds):
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)

    if hrs:
        return f"{hrs}h {mins}m {secs}s"
    if mins:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def ensure_output_dir(job):
    """
    V1: Per-job output folder support.
    If output_folder exists in job → use it, else fallback to FINAL_OUTPUT_BASE.
    Always create dated subfolder dd.mm.yy
    """
    output_root = job.get("output_folder") or FINAL_OUTPUT_BASE
    date_str = datetime.now().strftime("%d.%m.%y")
    output_dir = os.path.join(output_root, date_str)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir, date_str


# ================== AUTH VALIDATION ==================

def validate_auth():
    section("🔐 AUTHENTICATION CHECK")

    test_query = "SELECT 1 AS auth_test"

    cmd = [
        BQ,
        "query",
        f"--project_id={PROJECT}",
        f"--location={LOCATION}",
        "--nouse_legacy_sql",
        test_query
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        log("✅ Google Cloud authentication is active")
        return

    log("⚠️ Authentication check failed")
    log("🌐 Opening gcloud auth login...")

    login_result = subprocess.run([GCLOUD, "auth", "login"])

    if login_result.returncode != 0:
        raise Exception("gcloud auth login failed")

    log("🔁 Re-validating authentication...")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        if result.stdout:
            log(f"Auth STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            log(f"Auth STDERR:\n{result.stderr.strip()}")
        raise Exception("Authentication still failed after gcloud auth login")

    log("✅ Authentication successful after login")


# ================== JOB CONFIG VALIDATION (V1 + backward compatible) ==================

def load_jobs():
    """
    V1 jobs.json recommended format:
    {
      "config": {...},
      "jobs": [ ... ]
    }

    Backward compatible:
    [ { "sql": ..., "time": ... }, ... ]
    """
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    global MAX_PARALLEL, RETRY_COUNT, DOWNLOAD_TIMEOUT_SECONDS, CLEANUP_EVERY_MINUTES

    if isinstance(data, dict):
        config = data.get("config", {}) or {}
        jobs = data.get("jobs", []) or []

        # apply config overrides (if present)
        MAX_PARALLEL = int(config.get("max_parallel", MAX_PARALLEL))
        RETRY_COUNT = int(config.get("retry_count", RETRY_COUNT))

        dtm = config.get("download_timeout_minutes", None)
        if dtm is not None:
            DOWNLOAD_TIMEOUT_SECONDS = int(dtm) * 60

        cem = config.get("cleanup_every_minutes", None)
        if cem is not None:
            CLEANUP_EVERY_MINUTES = int(cem)

        return jobs

    # old format list
    return data


def precheck_jobs():
    section("📋 JOBS.JSON PRE-CHECK")

    jobs = load_jobs()

    if not isinstance(jobs, list):
        raise Exception("jobs.json must contain either a JSON list OR {config, jobs}")

    missing_files = []
    invalid_jobs = []

    for idx, job in enumerate(jobs, start=1):
        sql_name = job.get("sql")
        run_time = job.get("time")

        if not sql_name or not run_time:
            invalid_jobs.append(f"Job #{idx}: missing sql/time")
            continue

        sql_path = os.path.join(QUERIES_DIR, sql_name)

        if not os.path.exists(sql_path):
            missing_files.append(sql_name)

    log(f"📌 Total jobs loaded: {len(jobs)}")
    times = sorted(set(job.get("time", "") for job in jobs if job.get("time")))
    log(f"⏰ Scheduled times: {', '.join(times)}")

    if invalid_jobs:
        for item in invalid_jobs:
            log(f"❌ Invalid job: {item}")
        raise Exception("Invalid jobs found in jobs.json")

    if missing_files:
        for file in missing_files:
            log(f"❌ Missing SQL file: {file}")
        raise Exception("One or more SQL files are missing")

    log("✅ All SQL files found")

    return jobs


def show_next_runs(jobs):
    section("🕒 TODAY'S SCHEDULE")

    for job in sorted(jobs, key=lambda x: x.get("time", "")):
        # Use below lines only if you need to see output folder path
        # out = job.get("output_folder", "")
        # out_msg = f" | output_folder: {out}" if out else ""
        # print(f"   {job.get('time')}  →  {job.get('sql')}{out_msg}", flush=True)

        # Default: hide output folder path
        print(f"   {job.get('time')}  →  {job.get('sql')}", flush=True)

    print("-" * 90)


# ================== STALE CLEANUP ==================

def cleanup_stale_local_temp_files():
    # No section() inside idle cleanup to avoid clutter.
    cutoff = time.time() - (STALE_CLEANUP_HOURS * 3600)
    deleted = 0

    try:
        for file in os.listdir(SAFE_TEMP_DIR):
            path = os.path.join(SAFE_TEMP_DIR, file)

            if not os.path.isfile(path):
                continue

            # keep your original intention: only csv temp
            if not file.lower().endswith(".csv"):
                continue

            if os.path.getmtime(path) < cutoff:
                try:
                    os.remove(path)
                    deleted += 1
                    log(f"🧹 Deleted stale local temp file: {path}", console=False)
                except Exception as e:
                    log(f"⚠️ Could not delete local temp file {path} → {e}", console=False)
    finally:
        log(f"✅ Local temp cleanup completed. Deleted files: {deleted}", console=False)


def cleanup_stale_bq_temp_tables():
    cmd = [
        BQ,
        "ls",
        f"--project_id={PROJECT}",
        f"--location={LOCATION}",
        "--format=prettyjson",
        f"{PROJECT}:{DATASET}"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        log("⚠️ Could not list BigQuery tables for cleanup", console=False)
        return

    try:
        tables = json.loads(result.stdout or "[]")
    except Exception as e:
        log(f"⚠️ Could not parse BigQuery table list → {e}", console=False)
        return

    cutoff = datetime.now() - timedelta(hours=STALE_CLEANUP_HOURS)
    deleted = 0

    for tbl in tables:
        table_ref = tbl.get("tableReference", {})
        table_id = table_ref.get("tableId", "")

        if not table_id.startswith("TEMP_AUTO_"):
            continue

        created_ms = int(tbl.get("creationTime", "0"))
        created_dt = datetime.fromtimestamp(created_ms / 1000)

        if created_dt < cutoff:
            full_table = f"{PROJECT}:{DATASET}.{table_id}"
            cleanup_resource([BQ, "rm", "-f", full_table], full_table)
            deleted += 1

    log(f"✅ BigQuery temp table cleanup completed. Deleted tables: {deleted}", console=False)


def cleanup_stale_gcs_temp_files():
    # V1 note: extracts may produce sharded files, but all end with .csv
    gcs_pattern = f"gs://{BUCKET}/TEMP_AUTO_*.csv"

    result = subprocess.run(
        [GSUTIL, "ls", "-l", gcs_pattern],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        log("ℹ️ No stale TEMP_AUTO_ GCS files found or listing not available", console=False)
        return

    cutoff = datetime.now() - timedelta(hours=STALE_CLEANUP_HOURS)
    deleted = 0

    for line in result.stdout.splitlines():
        line = line.strip()

        if not line or line.startswith("TOTAL:"):
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        # Typical: <size> <date> <time> <url>
        date_part = parts[1]
        time_part = parts[2]
        gcs_file = parts[-1]

        if "TEMP_AUTO_" not in gcs_file:
            continue

        try:
            file_dt = datetime.strptime(f"{date_part} {time_part[:8]}", "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        if file_dt < cutoff:
            cleanup_resource([GSUTIL, "rm", gcs_file], gcs_file)
            deleted += 1

    log(f"✅ GCS temp cleanup completed. Deleted files: {deleted}", console=False)


# ================== V1: IDLE CLEANUP THREAD ==================

def idle_cleanup_runner(stop_event):
    """
    V1: Run stale cleanup ONLY when system is idle (no active jobs),
    and run periodically (CLEANUP_EVERY_MINUTES).
    This prevents startup cleanup delays that caused scheduled jobs to be skipped.
    """
    # small initial delay so startup banner/auth happens smoothly
    time.sleep(10)

    while not stop_event.is_set():
        # Wait in small intervals so stop_event can exit quickly
        for _ in range(CLEANUP_EVERY_MINUTES * 60):
            if stop_event.is_set():
                return
            time.sleep(1)

        # Only cleanup when idle
        if get_active_jobs_count() > 0:
            continue

        try:
            log("🧹 Idle time detected. Running stale cleanup...", console=True)
            cleanup_stale_local_temp_files()
            cleanup_stale_bq_temp_tables()
            cleanup_stale_gcs_temp_files()
            log("✅ Idle cleanup finished.", console=True)
        except Exception as e:
            log(f"⚠️ Idle cleanup failed → {e}", console=True)


# ================== SAFE MOVE ==================

def move_to_final_with_retry(temp_file, final_file, sql_name=None):
    for attempt in range(1, MOVE_RETRY_COUNT + 1):
        try:
            if sql_name:
                set_status(sql_name, "MOVING TO OUTPUT", final_file)

            if os.path.exists(final_file):
                os.remove(final_file)

            shutil.move(temp_file, final_file)
            log(f"✅ File saved: {final_file}", console=False)
            return final_file

        except PermissionError as e:
            if sql_name:
                set_status(sql_name, "OUTPUT FILE LOCKED", f"Retry {attempt}/{MOVE_RETRY_COUNT}")
            log(f"⚠️ Final file locked. Retry {attempt}/{MOVE_RETRY_COUNT} → {e}", console=False)
            time.sleep(MOVE_RETRY_DELAY)

        except OSError as e:
            if getattr(e, "winerror", None) == 32:
                if sql_name:
                    set_status(sql_name, "OUTPUT FILE LOCKED", f"Retry {attempt}/{MOVE_RETRY_COUNT}")
                log(f"⚠️ Final file used by another process. Retry {attempt}/{MOVE_RETRY_COUNT} → {e}", console=False)
                time.sleep(MOVE_RETRY_DELAY)
            else:
                raise

    fallback = final_file.replace(".csv", f"_{datetime.now().strftime('%H%M%S')}.csv")
    shutil.move(temp_file, fallback)

    if sql_name:
        set_status(sql_name, "SAVED AS FALLBACK", fallback)

    log(f"⚠️ Final file remained locked. Saved as fallback: {fallback}", console=False)
    return fallback


# ================== V1 DOWNLOAD: WILDCARD + TIMEOUT + MERGE ==================

def list_gcs_parts(gcs_wildcard):
    """
    Return list of actual gs:// objects matching wildcard using gsutil ls.
    """
    result = subprocess.run([GSUTIL, "ls", gcs_wildcard], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    files = [ln.strip() for ln in result.stdout.splitlines() if ln.strip().startswith("gs://")]
    return files


def estimate_total_gcs_bytes(gcs_wildcard):
    """
    Estimate total bytes for wildcard by using 'gsutil ls -l' and summing sizes.
    """
    result = subprocess.run([GSUTIL, "ls", "-l", gcs_wildcard], capture_output=True, text=True)
    if result.returncode != 0:
        return None

    total = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("TOTAL:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        # first token is size
        try:
            size = int(parts[0])
            total += size
        except Exception:
            pass
    return total


def dir_size_bytes(path):
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except Exception:
                    pass
    except Exception:
        pass
    return total


def download_and_merge_with_progress(gcs_wildcard, temp_local_file, sql_name, base, ts, timeout_seconds):
    """
    V1: Use gsutil -m cp wildcard to temp folder, enforce hard timeout, show progress.
    Merge all parts in order into single CSV.
    """
    set_status(sql_name, "CHECKING GCS PARTS", gcs_wildcard)

    parts = list_gcs_parts(gcs_wildcard)
    if not parts:
        raise Exception("No GCS extract files found for download (wildcard produced 0 files)")

    total_bytes = estimate_total_gcs_bytes(gcs_wildcard)
    if total_bytes is None or total_bytes <= 0:
        total_bytes = None  # unknown

    temp_dir = os.path.join(SAFE_TEMP_DIR, f"TEMP_PARTS_{base}_{ts}")
    os.makedirs(temp_dir, exist_ok=True)

    # Start download
    set_status(sql_name, "DOWNLOADING", "Starting gsutil -m cp ...")

    start_time = time.time()
    process = subprocess.Popen(
        [GSUTIL, "-m", "cp", gcs_wildcard, temp_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    while process.poll() is None:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            process.kill()
            raise Exception(f"Download timed out after {timeout_seconds} seconds")

        downloaded = dir_size_bytes(temp_dir)
        if total_bytes:
            percent = (downloaded / total_bytes) * 100
            set_status(
                sql_name,
                "DOWNLOADING",
                f"{percent:.1f}% | {round(downloaded/1024/1024,2)} MB / {round(total_bytes/1024/1024,2)} MB | {int(elapsed)}s"
            )
        else:
            set_status(
                sql_name,
                "DOWNLOADING",
                f"{round(downloaded/1024/1024,2)} MB downloaded | {int(elapsed)}s"
            )

        time.sleep(2)

    stdout, stderr = process.communicate()

    if process.returncode != 0:
        if stdout:
            log(f"Download STDOUT:\n{stdout.strip()}")
        if stderr:
            log(f"Download STDERR:\n{stderr.strip()}")
        raise Exception("Download failed")

    # Merge
    set_status(sql_name, "MERGING FILES", "Combining downloaded parts")

    # Determine local parts (gsutil keeps original names)
    local_parts = []
    for f in os.listdir(temp_dir):
        fp = os.path.join(temp_dir, f)
        if os.path.isfile(fp) and f.lower().endswith(".csv"):
            local_parts.append(fp)

    if not local_parts:
        # sometimes extracted files may not end with .csv depending on naming
        local_parts = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]

    local_parts.sort()  # correct order due to shard suffix

    with open(temp_local_file, "wb") as out:
        for part_path in local_parts:
            with open(part_path, "rb") as inp:
                shutil.copyfileobj(inp, out, length=1024 * 1024)

    # cleanup local parts folder
    shutil.rmtree(temp_dir, ignore_errors=True)

    set_status(sql_name, "DOWNLOAD COMPLETE", temp_local_file)


# ================== CORE QUERY PIPELINE (V1 upgraded, preserves your summary + cleanup) ==================

def run_query(job):
    """
    V1: accepts job dict (so we can read output_folder + per-job overrides if needed)
    """
    sql_filename = job["sql"]

    start_dt = datetime.now()
    start_time = start_dt.strftime("%Y-%m-%d %H:%M:%S")

    sql_path = os.path.join(QUERIES_DIR, sql_filename)

    if not os.path.exists(sql_path):
        write_summary(sql_filename, "FAILED", start_time=start_time, error="SQL file not found")
        set_status(sql_filename, "FAILED", "SQL file not found")
        raise Exception(f"SQL file not found: {sql_path}")

    inc_active_jobs()

    base = os.path.splitext(sql_filename)[0]
    table_base = safe_name(base)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # V1: per-job output directory
    output_dir, date_str = ensure_output_dir(job)

    temp_table = f"{PROJECT}:{DATASET}.TEMP_AUTO_{table_base}_{ts}"

    # V1: wildcard extract support
    # BigQuery will replace '*' with shard numbers e.g. 000000000000
    gcs_wildcard = f"gs://{BUCKET}/TEMP_AUTO_{base}_{ts}_*.csv"

    temp_file = os.path.join(SAFE_TEMP_DIR, f"TEMP_AUTO_{base}_{ts}.csv")
    final_file = os.path.join(output_dir, f"{base} created on {date_str}.csv")

    table_created = False
    gcs_created = False

    write_summary(sql_filename, "RUNNING", start_time=start_time)

    # V1: per-job download timeout override (optional)
    job_timeout_min = job.get("download_timeout_minutes")
    effective_download_timeout = int(job_timeout_min) * 60 if job_timeout_min else DOWNLOAD_TIMEOUT_SECONDS

    try:
        set_status(sql_filename, "QUERYING", temp_table)
        log(f"🚀 Started: {sql_filename}", console=False)
        log(f"🧪 Temp table: {temp_table}", console=False)

        with open(sql_path, "r", encoding="utf-8") as f:
            query = f.read()

        query_cmd = [
            BQ,
            "query",
            f"--project_id={PROJECT}",
            f"--location={LOCATION}",
            "--nouse_legacy_sql",
            f"--destination_table={temp_table}",
            "--replace",
            "--priority=BATCH"  # V1: less quota pressure
        ]

        try:
            result = subprocess.run(
                query_cmd,
                input=query,
                capture_output=True,
                text=True,
                timeout=QUERY_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            raise Exception(f"Query timed out after {QUERY_TIMEOUT_SECONDS} seconds")

        if result.returncode != 0:
            if result.stdout:
                log(f"Query STDOUT:\n{result.stdout.strip()}")
            if result.stderr:
                log(f"Query STDERR:\n{result.stderr.strip()}")
            raise Exception("Query failed")

        table_created = True

        set_status(sql_filename, "EXTRACTING TO GCS", gcs_wildcard)

        # V1: extract to wildcard
        run_list(
            [BQ, "extract", "--location", LOCATION, "--destination_format=CSV", temp_table, gcs_wildcard],
            "Extract",
            timeout_seconds=EXTRACT_TIMEOUT_SECONDS
        )

        gcs_created = True

        # V1: download wildcard parts + merge with hard timeout
        download_and_merge_with_progress(gcs_wildcard, temp_file, sql_filename, table_base, ts, effective_download_timeout)

        saved_file = move_to_final_with_retry(temp_file, final_file, sql_filename)

        end_dt = datetime.now()
        duration_sec = round((end_dt - start_dt).total_seconds(), 2)

        set_status(sql_filename, "SUCCESS", f"Completed in {format_duration(duration_sec)} | {saved_file}")
        log(f"✅ Completed {sql_filename} in {format_duration(duration_sec)}", console=False)

        write_summary(
            sql_filename,
            "SUCCESS",
            start_time=start_time,
            end_time=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration_sec=duration_sec,
            output_file=saved_file
        )

    except Exception as e:
        end_dt = datetime.now()
        duration_sec = round((end_dt - start_dt).total_seconds(), 2)

        set_status(sql_filename, "FAILED", str(e))

        write_summary(
            sql_filename,
            "FAILED",
            start_time=start_time,
            end_time=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration_sec=duration_sec,
            error=str(e)
        )

        raise

    finally:
        # cleanup only the resources created by this job
        if table_created:
            cleanup_resource([BQ, "rm", "-f", temp_table], temp_table, sql_filename)

        if gcs_created:
            # remove sharded files
            cleanup_resource([GSUTIL, "-m", "rm", gcs_wildcard], gcs_wildcard, sql_filename)

        dec_active_jobs()

        current = get_status_snapshot().get(sql_filename, {})
        if current.get("status") in ("CLEANUP DONE", "CLEANUP"):
            set_status(sql_filename, "SUCCESS + CLEANUP DONE", current.get("detail", ""))


# ================== AUTOMATION ==================

def run_with_retry(job):
    sql_name = job["sql"]

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            if attempt > 1:
                set_status(sql_name, "RETRYING", f"attempt {attempt}/{RETRY_COUNT}")
            run_query(job)
            return True

        except Exception as e:
            log(f"❌ Error {sql_name} → {e}", console=False)

            if "SQL file not found" in str(e):
                set_status(sql_name, "FAILED", "SQL file missing")
                log(f"⛔ Skipping {sql_name} because file is missing", console=False)
                return False

            if attempt < RETRY_COUNT:
                set_status(sql_name, "WAITING TO RETRY", f"attempt {attempt + 1}/{RETRY_COUNT} in 15s")
                log(f"🔁 Retrying {sql_name}... attempt {attempt + 1}/{RETRY_COUNT}", console=False)
                time.sleep(15)
            else:
                set_status(sql_name, "FINAL FAILURE", str(e))
                log(f"❌ Final failure {sql_name}", console=False)
                return False


def run_batch(jobs):
    batch_start = datetime.now()

    with STATUS_LOCK:
        JOB_STATUS.clear()
        for job in jobs:
            JOB_STATUS[job["sql"]] = {
                "status": "WAITING",
                "detail": "Queued",
                "updated": datetime.now().strftime("%H:%M:%S"),
                "start": ""
            }

    stop_dashboard = threading.Event()
    dashboard_thread = threading.Thread(
        target=dashboard_running,
        args=(stop_dashboard,),
        daemon=True
    )
    dashboard_thread.start()

    results = []

    try:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            mapped = list(executor.map(run_with_retry, jobs))

        for job, status in zip(jobs, mapped):
            results.append((job["sql"], status))

    finally:
        stop_dashboard.set()
        dashboard_thread.join(timeout=2)
        os.system("cls")

    success_count = sum(1 for _, status in results if status)
    fail_count = len(results) - success_count

    section("📊 BATCH SUMMARY")

    log(f"✅ Success: {success_count}")
    log(f"❌ Failed : {fail_count}")

    for sql_name, status in results:
        icon = "✅" if status else "❌"
        log(f"{icon} {sql_name}")

    batch_duration = round((datetime.now() - batch_start).total_seconds(), 2)
    log(f"⏱️ Batch duration: {format_duration(batch_duration)}")

    section("📌 FINAL JOB STATES")
    snapshot = get_status_snapshot()
    for sql_name in sorted(snapshot.keys()):
        info = snapshot[sql_name]
        print(f"{sql_name}")
        print(f"   Status : {info.get('status', '')}")
        print(f"   Detail : {info.get('detail', '')}")
        print("-" * 90)


# ================== V1: SCHEDULER RELIABILITY ==================

def is_due(time_str):
    """
    V1: allow small tolerance so jobs don't get skipped due to loop timing.
    """
    now_dt = datetime.now()
    job_dt = datetime.strptime(time_str, "%H:%M")
    return now_dt.hour == job_dt.hour and abs(now_dt.minute - job_dt.minute) <= 1


def mark_ran_today(sql):
    today = datetime.now().strftime("%Y-%m-%d")
    with LAST_RUN_MAP_LOCK:
        LAST_RUN_MAP[(today, sql)] = datetime.now()


def ran_recently(sql, seconds=75):
    today = datetime.now().strftime("%Y-%m-%d")
    with LAST_RUN_MAP_LOCK:
        last = LAST_RUN_MAP.get((today, sql))
    if not last:
        return False
    return (datetime.now() - last).total_seconds() < seconds


# ================== MAIN SCHEDULER ==================

def scheduler():
    banner()

    validate_paths()
    validate_auth()

    jobs = precheck_jobs()

    # V1: do NOT run startup_cleanup() here (prevents skip due to slow cleanup)
    # Instead, cleanup runs in idle time only via background thread.
    show_next_runs(jobs)

    # V1: start idle cleanup thread
    stop_cleanup = threading.Event()
    cleanup_thread = threading.Thread(target=idle_cleanup_runner, args=(stop_cleanup,), daemon=True)
    cleanup_thread.start()

    log("🚀 BigQuery Automation Started (V1)")
    log("🟢 Scheduler is running. Keep this CMD window open.")

    last_display_minute = None

    while True:
        try:
            jobs = load_jobs()
        except Exception as e:
            log(f"❌ Failed to load jobs.json → {e}")
            time.sleep(60)
            continue

        now = datetime.now().strftime("%H:%M")

        if now != last_display_minute:
            print(f"\r🕒 Waiting... Current time: {now} | Press Ctrl+C to stop", end="", flush=True)
            last_display_minute = now

        due_jobs = []
        for job in jobs:
            if not job.get("time") or not job.get("sql"):
                continue

            if is_due(job.get("time")) and not ran_recently(job["sql"]):
                due_jobs.append(job)
                mark_ran_today(job["sql"])

        if due_jobs:
            print()
            log(f"📅 Running batch at {now}")
            run_batch(due_jobs)
            show_next_runs(jobs)

        time.sleep(20)


# ================== ENTRY ==================

if __name__ == "__main__":
    try:
        scheduler()
    except KeyboardInterrupt:
        print("\n\n🛑 Automation stopped by user.")
    except Exception as e:
        log(f"💥 Fatal error → {e}")
        print("\nPress Enter to close...")
        input()
