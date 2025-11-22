import requests
from requests.auth import HTTPDigestAuth
import mysql.connector
import datetime
import time
import os
import re
import base64
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid  # [PENTING] Untuk generate searchID unik

# Impor konfigurasi (termasuk EVENT_MAP) dan modul database kustom
from config import *
import database as db

# --- SETUP LOGGING ---
LOG_LOCK = threading.Lock()
loggers = {}

def get_logger(base_dir, name):
    """
    Fungsi logger dinamis yang bisa menulis ke direktori berbeda.
    """
    global loggers
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    logger_name = f"{base_dir}.{name}.{date_str}"

    with LOG_LOCK:
        if logger_name in loggers:
            return loggers[logger_name]

        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            daily_log_dir = os.path.join(base_dir, date_str)
            os.makedirs(daily_log_dir, exist_ok=True)
            log_file = os.path.join(daily_log_dir, f"{name}.log")
            handler = logging.FileHandler(log_file, encoding='utf-8')
            handler.setFormatter(logging.Formatter('[%(asctime)s] - %(message)s', datefmt='%H:%M:%S'))
            logger.addHandler(handler)
            loggers[logger_name] = logger
            return logger
        except Exception as e:
            print(f"FATAL: Gagal membuat logger untuk {name} di {base_dir}: {e}")
            return logging.getLogger()

console_logger = logging.getLogger("ConsoleLogger")
if not console_logger.handlers:
    console_logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    console_logger.addHandler(console_handler)

def log(device, message, level="INFO"):
    """Mencatat log BERSIH ke folder EVENT_LOG_DIR."""
    label = sanitize_name(device_label(device))
    file_logger = get_logger(EVENT_LOG_DIR, label)

    log_level = level.upper()
    if log_level == "ERROR": file_logger.error(message)
    elif log_level == "WARNING": file_logger.warning(message)
    else: file_logger.info(message)

    now = datetime.datetime.now()
    d, t = now.strftime("%d-%m-%Y"), now.strftime("%H:%M:%S")
    level_map = {"OK": "INFO", "WARN": "WARNING"}
    log_level_console = level_map.get(level.upper(), level.upper())
    console_message = f"[{d}] [{t}] [{device_label(device)}] [{log_level_console}] {message}"
    console_logger.info(console_message)

def log_system(message, level="INFO"):
    """Mencatat log SISTEM ke folder EVENT_LOG_DIR."""
    file_logger = get_logger(EVENT_LOG_DIR, "system")
    log_level = level.upper()
    if log_level == "ERROR": file_logger.error(message)
    elif log_level == "WARNING": file_logger.warning(message)
    else: file_logger.info(message)
    
    now = datetime.datetime.now()
    d, t = now.strftime("%d-%m-%Y"), now.strftime("%H:%M:%S")
    console_message = f"[{d}] [{t}] [SYSTEM] [{log_level}] {message}"
    console_logger.info(console_message)

# [DIHAPUS] Fungsi log_raw_event telah dihapus untuk menghemat ruang penyimpanan
# Service log (log mentah) tidak akan dicatat lagi.

# --- AKHIR SETUP LOGGING ---

# --- Variabel Global & Kunci Thread ---
LAST_SEEN_EVENT_ID = {}
DEVICE_DATA_LOCK = threading.Lock()
# ----------------------------------------

# --- FUNGSI BANTU (HELPERS) ---
def sanitize_name(name):
    if not name: return "unknown"
    return re.sub(r"[^a-zA-Z0-9 _-]", "_", name).strip().replace(" ", "_")

def device_label(device):
    return device.get("name") or device.get("ip")

def parse_iso_time(time_str):
    return datetime.datetime.fromisoformat(time_str.replace(TIMEZONE, ''))
# ----------------------------------

# --- FUNGSI DATABASE ---
def set_last_sync_time(ip, time_iso_str):
    try: dt = parse_iso_time(time_iso_str)
    except Exception: dt = datetime.datetime.now()
    conn = db.get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET lastSync=%s WHERE ip=%s", (dt, ip))
    c.close(), conn.close()

def get_last_sync_time(ip):
    conn = db.get_db()
    c = conn.cursor()
    c.execute("SELECT lastSync FROM devices WHERE ip=%s", (ip,))
    row = c.fetchone()
    c.close(), conn.close()
    if row and row[0]:
        dt = row[0]
        if isinstance(dt, datetime.datetime):
            return dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE
    dt = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE
# ----------------------------------------------------

# --- FUNGSI API & PROSES EVENT ---

def download_image_with_retry(device, pictureURL, auth):
    """
    Mencoba mengunduh gambar.
    """
    if not pictureURL:
        log(device, "URL Gambar kosong, download dilewati.", level="WARN")
        return None
        
    try:
        max_retries = int(db.get_setting('sync_download_retries', '5'))
        timeout = int(db.get_setting('request_timeout', '30'))
    except ValueError:
        max_retries = 5
        timeout = 30

    for attempt in range(1, max_retries + 1):
        try:
            r_img = requests.get(pictureURL, auth=auth, timeout=timeout)
            if r_img.status_code == 200:
                return r_img.content  # Sukses, kembalikan konten mentah (bytes)
            
            log(device, f"[Attempt {attempt}/{max_retries}] Gagal mendapatkan gambar (HTTP {r_img.status_code}). URL: {pictureURL}", level="WARN")
        
        except requests.exceptions.RequestException as e:
            log(device, f"[Attempt {attempt}/{max_retries}] Error koneksi saat mengambil gambar: {e}", level="WARN")
        
        if attempt < max_retries:
            time.sleep(2)
    
    log(device, f"Gagal mengunduh gambar setelah {max_retries} percobaan. URL: {pictureURL}", level="ERROR")
    return None

# ----------------------------------------------------

# --- HIKVISION & EVENT PROCESSING (SMART FALLBACK) ---
def iso8601_now(offset_seconds=0):
    t = datetime.datetime.now() - datetime.timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE

def get_events_from_device(device, start_time, end_time, batch_max, timeout):

    ip, user, password = device.get("ip"), device.get("username"), device.get("password")
    
    # URL Standar
    url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"

    # Fungsi internal pengirim request
    def _send_request(s_time, e_time):
        # Gunakan random string pendek untuk searchID agar sesi selalu baru
        import random
        search_id = f"search_{random.randint(1000, 9999)}"
        
        body = {
            "AcsEventCond": {
                "searchID": search_id,
                "searchResultPosition": 0,
                "maxResults": batch_max,
                "major": 0,
                "minor": 0,
                "startTime": s_time,
                "endTime": e_time
            }
        }
        headers = {"Content-Type": "application/json"}
        return requests.post(url, json=body, auth=HTTPDigestAuth(user, password), headers=headers, timeout=timeout)

    try:
        # --- Percobaan 1: Normal (dengan Timezone) ---
        r = _send_request(start_time, end_time)
        
        # Handle Error 400 (Biasanya masalah format waktu)
        if r.status_code == 400:
            log(device, f"Gagal Format Standar (400). Response: {r.text}", level="WARN")
            
            # --- Percobaan 2: Retry Tanpa Timezone ---
            s_clean = start_time.split('+')[0]
            e_clean = end_time.split('+')[0]
            
            log(device, f"Mencoba retry tanpa timezone: {s_clean} s/d {e_clean}...", level="INFO")
            r_retry = _send_request(s_clean, e_clean)
            
            if r_retry.status_code == 200:
                log(device, "Berhasil dengan format tanpa timezone!", level="INFO")
                return r_retry.json().get("AcsEvent", {}).get("InfoList", [])
            else:
                log(device, f"Tetap gagal (HTTP {r_retry.status_code}).", level="ERROR")
                return []

        r.raise_for_status()
        return r.json().get("AcsEvent", {}).get("InfoList", [])

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
             log(device, "GAGAL AUTH (401). Device terkunci/password salah. Jeda 10s...", level="ERROR")
             time.sleep(10)
        else:
             log(device, f"HTTP Error: {e}", level="ERROR")

    except requests.exceptions.RequestException as e:
        log(device, f"Koneksi Error: {e}", level="ERROR")
        
    return []

def get_event_desc(event):
    major, minor = event.get("major"), event.get("minor")
    return EVENT_MAP.get((major, minor))

def save_event(event, device):
    user, password = device.get("username"), device.get("password")
    auth = HTTPDigestAuth(user, password)
    
    eventId, device_name, pictureURL = event.get("serialNo"), device_label(device), event.get("pictureURL")
    name = event.get("name") or "unknown"
    
    try:
        dt = datetime.datetime.strptime(event.get("time")[:19], "%Y-%m-%dT%H:%M:%S")
        date_value, time_value = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        dt, date_value, time_value = None, "0000-00-00", "00:00:00"

    try:
        realtime_tolerance = int(db.get_setting('realtime_tolerance', '120'))
    except ValueError:
        realtime_tolerance = 120

    sync_type = "realtime" if dt and abs((datetime.datetime.now() - dt).total_seconds()) <= realtime_tolerance else "catch-up"
    event_desc = get_event_desc(event)
    employee_id = int(event["employeeNoString"]) if event.get("employeeNoString", "").isdigit() else None
    
    is_valid_for_api = (event_desc == "Face Recognized")
    
    local_image_path = None
    image_content = None
    initial_api_status = 'skipped' # Status default
    
    if dt and pictureURL and is_valid_for_api:
        # Langkah 1: Coba download gambar
        image_content = download_image_with_retry(device, pictureURL, auth)
        
        if image_content:
            # Langkah 2: Jika download berhasil, simpan ke disk
            try:
                safe_dev = sanitize_name(device_name)
                date_folder = dt.strftime("%Y-%m-%d")
                relative_folder = os.path.join("images", safe_dev, date_folder)
                absolute_folder = os.path.join("static", relative_folder)
                os.makedirs(absolute_folder, exist_ok=True)
                file_name = f"{sanitize_name(name)}-{eventId}.jpg"
                local_image_path = os.path.join(relative_folder, file_name).replace("\\", "/")
                with open(os.path.join(absolute_folder, file_name), "wb") as f:
                    f.write(image_content)
                
                # Set status untuk diproses oleh worker
                initial_api_status = 'pending' 
                
            except Exception as e:
                log(device, f"Error simpan gambar ke disk (ID: {eventId}): {e}", level="WARN")
                initial_api_status = 'failed' # Gagal simpan ke disk, tandai gagal
        else:
            # Jika download gambar gagal setelah 5x retry
            log(device, f"Download gambar gagal untuk event {eventId}, menandai 'failed'.", level="ERROR")
            initial_api_status = 'failed'
    
    # Langkah 4: Simpan event ke database
    conn, c, db_event_id = db.get_db(), None, None
    try:
        c = conn.cursor()
        # Perhatikan: apiRetryCount di-set ke 0
        sql = """
            INSERT INTO events 
            (deviceName, eventId, employeeId, name, date, time, eventDesc, 
             pictureURL, localImagePath, syncType, apiStatus, apiRetryCount) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
        """
        values = (device_name, eventId, employee_id, name, date_value, time_value, 
                  event_desc, pictureURL, local_image_path, sync_type, initial_api_status)
        c.execute(sql, values)
        db_event_id = c.lastrowid
        
        return True
    except mysql.connector.IntegrityError:
        log(device, f"Info: Event (ID: {eventId}) sudah ada di database, dilewati.")
        return False
    except Exception as e:
        log(device, f"DB error (ID: {eventId}): {e}", level="ERROR")
        return False
    finally:
        if c: c.close()
        conn.close()
# ----------------------------------------------------

# --- PING & WORKER ---
def process_device(device):
    ip = device.get("ip")
    if not all([device.get("username"), device.get("password")]):
        log(device, "Username atau Password belum diatur. Dilewati.", level="WARN")
        return
        
    try:
        try:
            batch_max = int(db.get_setting('event_batch_max', '100'))
            timeout = int(db.get_setting('request_timeout', '30'))
            sleep_delay = float(db.get_setting('event_sleep_delay', '1'))
        except ValueError:
            batch_max = 100
            timeout = 30
            sleep_delay = 1

        last_sync_str = get_last_sync_time(ip)
        now_time_str = iso8601_now()
        start_dt, end_dt = parse_iso_time(last_sync_str), parse_iso_time(now_time_str)
        time_diff_seconds = (end_dt - start_dt).total_seconds()
        
        if time_diff_seconds > BIG_CATCHUP_THRESHOLD_SECONDS: # BIG_CATCHUP masih dari config.py
            all_events = []
            current_start_dt = start_dt
            while current_start_dt < end_dt:
                current_end_dt = min(current_start_dt + datetime.timedelta(minutes=CATCH_UP_CHUNK_MINUTES), end_dt)
                chunk_events = get_events_from_device(device, 
                                                    current_start_dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE, 
                                                    current_end_dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE,
                                                    batch_max, timeout)
                if chunk_events: all_events.extend(chunk_events)
                current_start_dt = current_end_dt
            events = all_events
        else:
            events = get_events_from_device(device, last_sync_str, now_time_str, batch_max, timeout)
        
        if not events: return
        
        events.sort(key=lambda x: int(x.get("serialNo") or 0))
        
        with DEVICE_DATA_LOCK:
            last_seen_id = LAST_SEEN_EVENT_ID.get(ip, 0)
        
        new_events = [e for e in events if int(e.get("serialNo") or 0) > last_seen_id]

        if not new_events:
            return

        saved_count = 0
        for e in new_events:
            # [DIHAPUS] Tidak lagi mencatat log raw/service log
            
            time.sleep(sleep_delay)
            
            event_desc = get_event_desc(e)
            
            if event_desc == "Face Recognized":
                try:
                    time_value = datetime.datetime.strptime(e.get("time")[:19], "%Y-%m-%dT%H:%M:%S").strftime("%H:%M:%S")
                    try:
                        realtime_tolerance = int(db.get_setting('realtime_tolerance', '120'))
                    except ValueError:
                        realtime_tolerance = 120
                    
                    sync_type = "realtime" if abs((datetime.datetime.now() - parse_iso_time(e.get("time"))).total_seconds()) <= realtime_tolerance else "catch-up"
                    log(device, f"Mengambil event '{sync_type}' - {time_value} (ID: {e.get('serialNo')}) untuk '{e.get('name')}'...")
                except Exception:
                    log(device, f"Mengambil event (ID: {e.get('serialNo')}) untuk '{e.get('name')}'...")

                if save_event(e, device):
                    saved_count += 1
        
        if saved_count > 0:
            log(device, f"Selesai, total {saved_count} event baru berhasil disimpan ke database (status: pending).")
        
        newest_event = new_events[-1]
        newest_event_id = int(newest_event.get("serialNo") or 0)
        newest_event_time_str = newest_event.get("time")

        with DEVICE_DATA_LOCK:
            LAST_SEEN_EVENT_ID[ip] = newest_event_id
            
        if newest_event_time_str:
            set_last_sync_time(ip, newest_event_time_str)
            
    except Exception as e:
        log(device, f"Terjadi error tak terduga: {e}", level="ERROR")

# --- MAIN LOOP ---
def main_sync():
    db.init_db()
    log_system("Memulai [Sync Service] - (HANYA MENGAMBIL EVENT)...")
    
    try:
        while True:
            devices = db.get_all_devices()
            if not devices:
                log_system("Tidak ada device yang terdaftar. Menunggu 15 detik..."), time.sleep(15)
                continue
            
            with ThreadPoolExecutor(max_workers=len(devices)) as executor:
                executor.map(process_device, devices)
            
            try:
                poll_interval = int(db.get_setting('poll_interval', '2'))
            except ValueError:
                poll_interval = 2
            
            time.sleep(poll_interval) 
            
    except KeyboardInterrupt:
        log_system("Sinkronisasi (Sync Service) dihentikan oleh pengguna.")
    except Exception as e:
        log_system(f"FATAL ERROR [Sync Service]: {e}", level="ERROR")

# --- ENTRY POINT ---
if __name__ == "__main__":
    main_sync()