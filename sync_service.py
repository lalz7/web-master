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
import threading # <-- PERBAIKAN: Baris ini ditambahkan kembali
from concurrent.futures import ThreadPoolExecutor

# Impor konfigurasi (termasuk EVENT_MAP) dan modul database kustom
from config import *
import database as db

# --- SETUP LOGGING (Tidak berubah) ---
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

def log_raw_event(device, event):
    """Mencatat SEMUA event mentah ke folder SERVICE_LOG_DIR."""
    logger = get_logger(SERVICE_LOG_DIR, sanitize_name(device_label(device)))
    
    major, minor = event.get("major"), event.get("minor")
    event_desc = EVENT_MAP.get((major, minor), f"Event tidak dikenali (Major: {major}, Minor: {minor})")
    
    log_data = {
        "time": event.get("time"),
        "eventID": event.get("serialNo"),
        "description": event_desc,
        "employeeId": event.get("employeeNoString"),
        "name": event.get("name"),
    }
    logger.info(json.dumps(log_data))
# --- AKHIR SETUP LOGGING ---

# --- Variabel Global & Kunci Thread ---
# (Hanya LAST_SEEN_EVENT_ID yang tersisa)
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

# --- FUNGSI DATABASE (Spesifik untuk service ini) ---
# (Hanya fungsi yang relevan yang disimpan)
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

# --- FUNGSI API & PROSES EVENT (DIMODIFIKASI) ---

def download_image_with_retry(device, pictureURL, auth, max_retries=5, delay=2):
    """
    Mencoba mengunduh gambar dengan 5 kali percobaan.
    (Fungsi ini tetap ada karena sync_service masih bertugas mengunduh gambar)
    """
    if not pictureURL:
        log(device, "URL Gambar kosong, download dilewati.", level="WARN")
        return None
        
    for attempt in range(1, max_retries + 1):
        try:
            r_img = requests.get(pictureURL, auth=auth, timeout=REQUEST_TIMEOUT)
            if r_img.status_code == 200:
                return r_img.content  # Sukses, kembalikan konten mentah (bytes)
            
            log(device, f"[Attempt {attempt}/{max_retries}] Gagal mendapatkan gambar (HTTP {r_img.status_code}). URL: {pictureURL}", level="WARN")
        
        except requests.exceptions.RequestException as e:
            log(device, f"[Attempt {attempt}/{max_retries}] Error koneksi saat mengambil gambar: {e}", level="WARN")
        
        if attempt < max_retries:
            time.sleep(delay) # Tunggu sebelum mencoba lagi
    
    log(device, f"Gagal mengunduh gambar setelah {max_retries} percobaan. URL: {pictureURL}", level="ERROR")
    return None # Gagal setelah semua percobaan

# ----------------------------------------------------

# --- HIKVISION & EVENT PROCESSING ---
def iso8601_now(offset_seconds=0):
    t = datetime.datetime.now() - datetime.timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE

def get_events_from_device(device, start_time, end_time):
    ip, user, password = device.get("ip"), device.get("username"), device.get("password")
    url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    body = {"AcsEventCond": {"searchID": "batch", "searchResultPosition": 0, "maxResults": BATCH_MAX_RESULTS,
                             "major": 0, "minor": 0, "startTime": start_time, "endTime": end_time}}
    try:
        r = requests.post(url, json=body, auth=HTTPDigestAuth(user, password), timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json().get("AcsEvent", {}).get("InfoList", [])
    except requests.exceptions.RequestException as e:
        log(device, f"Gagal mengambil event. Error koneksi: {e}", level="ERROR")
    return []

def get_event_desc(event):
    major, minor = event.get("major"), event.get("minor")
    return EVENT_MAP.get((major, minor))

def save_event(event, device):
    """
    Fungsi ini sekarang HANYA mengunduh gambar dan menyimpan ke DB.
    TIDAK LAGI MENGIRIM KE API.
    """
    user, password = device.get("username"), device.get("password")
    auth = HTTPDigestAuth(user, password)
    
    eventId, device_name, pictureURL = event.get("serialNo"), device_label(device), event.get("pictureURL")
    name = event.get("name") or "unknown"
    
    try:
        dt = datetime.datetime.strptime(event.get("time")[:19], "%Y-%m-%dT%H:%M:%S")
        date_value, time_value = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        dt, date_value, time_value = None, "0000-00-00", "00:00:00"

    sync_type = "realtime" if dt and abs((datetime.datetime.now() - dt).total_seconds()) <= 120 else "catch-up"
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
        
        # LOGIKA send_event_to_api() TELAH DIHAPUS DARI SINI
        
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

# --- PING & WORKER (SEMUA LOGIKA PING DAN NOTIFIKASI DIHAPUS) ---
def process_device(device):
    ip = device.get("ip")
    if not all([device.get("username"), device.get("password")]):
        log(device, "Username atau Password belum diatur. Dilewati.", level="WARN")
        return
        
    # LOGIKA PING, FAIL_COUNT, SUSPEND, NOTIFIKASI SUDAH DIHAPUS
    # Worker service akan menangani ini
    
    try:
        last_sync_str = get_last_sync_time(ip)
        now_time_str = iso8601_now()
        start_dt, end_dt = parse_iso_time(last_sync_str), parse_iso_time(now_time_str)
        time_diff_seconds = (end_dt - start_dt).total_seconds()
        
        if time_diff_seconds > BIG_CATCHUP_THRESHOLD_SECONDS:
            all_events = []
            current_start_dt = start_dt
            while current_start_dt < end_dt:
                current_end_dt = min(current_start_dt + datetime.timedelta(minutes=CATCH_UP_CHUNK_MINUTES), end_dt)
                chunk_events = get_events_from_device(device, current_start_dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE, current_end_dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE)
                if chunk_events: all_events.extend(chunk_events)
                current_start_dt = current_end_dt
            events = all_events
        else:
            events = get_events_from_device(device, last_sync_str, now_time_str)
        
        if not events: return
        
        events.sort(key=lambda x: int(x.get("serialNo") or 0))
        
        with DEVICE_DATA_LOCK:
            last_seen_id = LAST_SEEN_EVENT_ID.get(ip, 0)
        
        new_events = [e for e in events if int(e.get("serialNo") or 0) > last_seen_id]

        if not new_events:
            return

        saved_count = 0
        for e in new_events:
            # Langkah 1: Selalu catat SEMUA event mentah
            log_raw_event(device, e)
            
            # Jeda 1 detik antar event untuk menghindari "burst"
            time.sleep(1)
            
            event_desc = get_event_desc(e)
            
            # Langkah 2: Periksa apakah ini event yang ingin kita proses lebih lanjut
            if event_desc == "Face Recognized":
                # Langkah 3: Tulis log bersih SEKARANG, sebelum menyimpan ke DB
                try:
                    time_value = datetime.datetime.strptime(e.get("time")[:19], "%Y-%m-%dT%H:%M:%S").strftime("%H:%M:%S")
                    sync_type = "realtime" if abs((datetime.datetime.now() - parse_iso_time(e.get("time"))).total_seconds()) <= 120 else "catch-up"
                    log(device, f"Mengambil event '{sync_type}' - {time_value} (ID: {e.get('serialNo')}) untuk '{e.get('name')}'...")
                except Exception:
                    log(device, f"Mengambil event (ID: {e.get('serialNo')}) untuk '{e.get('name')}'...")

                # Langkah 4: Coba simpan ke database
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
# ----------------------------------------------------

# --- MAIN LOOP (DIMODIFIKASI - TANPA CLEANUP) ---
def main_sync():
    db.init_db()
    log_system("Memulai [Sync Service] - (HANYA MENGAMBIL EVENT)...")
    
    try:
        while True:
            # LOGIKA CLEANUP SUDAH DIHAPUS
            
            devices = db.get_all_devices()
            if not devices:
                log_system("Tidak ada device yang terdaftar. Menunggu 15 detik..."), time.sleep(15)
                continue
            
            # Menggunakan ThreadPoolExecutor untuk mengambil data dari semua perangkat secara paralel
            with ThreadPoolExecutor(max_workers=len(devices)) as executor:
                executor.map(process_device, devices)
            
            # POLL_INTERVAL sekarang mengontrol seberapa sering kita MENGECEK semua perangkat
            time.sleep(POLL_INTERVAL) 
            
    except KeyboardInterrupt:
        log_system("Sinkronisasi (Sync Service) dihentikan oleh pengguna.")
    except Exception as e:
        log_system(f"FATAL ERROR [Sync Service]: {e}", level="ERROR")

# --- ENTRY POINT ---
if __name__ == "__main__":
    main_sync()