import platform
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
import random
import shutil

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

# --- FUNGSI BARU UNTUK CLEANUP LOG ---
def cleanup_old_logs(days_to_keep):
    """Menghapus folder log yang lebih tua dari days_to_keep."""
    # Tentukan tanggal batas (cutoff)
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days_to_keep)
    # Ambil direktori log dari config
    log_dirs = [EVENT_LOG_DIR, SERVICE_LOG_DIR]
    
    deleted_folders = 0
    for log_dir in log_dirs:
        if not os.path.isdir(log_dir):
            continue
        
        # Iterasi setiap item di dalam folder log (e.g., 'event_logs' atau 'service_logs')
        for date_folder_name in os.listdir(log_dir):
            full_folder_path = os.path.join(log_dir, date_folder_name)
            
            # Pastikan itu adalah direktori
            if not os.path.isdir(full_folder_path):
                continue
            
            try:
                # Nama folder log adalah 'YYYY-MM-DD'
                folder_date = datetime.datetime.strptime(date_folder_name, '%Y-%m-%d')
                
                # Jika tanggal folder lebih tua dari tanggal batas, hapus
                if folder_date < cutoff_date:
                    shutil.rmtree(full_folder_path)
                    deleted_folders += 1
            except (ValueError, OSError) as e:
                # ValueError jika nama folder bukan format tanggal
                # OSError jika gagal menghapus folder
                log_system(f"Gagal memproses/menghapus folder log {full_folder_path}: {e}", level="WARN")
                
    return deleted_folders
# --- AKHIR SETUP LOGGING ---

# --- Variabel Global & Kunci Thread ---
FAIL_COUNT, SUSPEND_UNTIL, LAST_KNOWN_STATUS = {}, {}, {}
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

def set_device_status(ip, status):
    conn = db.get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET status=%s WHERE ip=%s", (status, ip))
    c.close(), conn.close()

def update_api_status(db_id, status):
    conn = db.get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE events SET apiStatus=%s WHERE id=%s", (status, db_id))
    except Exception as e:
        log_system(f"Error updating API status: {e}", level="ERROR")
    finally:
        c.close(), conn.close()

def event_exists(eventId, device_name):
    conn = db.get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM events WHERE eventId=%s AND deviceName=%s", (eventId, device_name))
    exists = c.fetchone() is not None
    c.close(), conn.close()
    return exists
# ----------------------------------------------------

# --- FUNGSI API & PROSES EVENT (DIMODIFIKASI) ---

def download_image_with_retry(device, pictureURL, auth, max_retries=5, delay=2):
    """
    Mencoba mengunduh gambar dengan 5 kali percobaan.
    Menggunakan auth (HTTPDigestAuth) yang sama dengan request lainnya.
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

def send_event_to_api(event_data, device, user, password, db_event_id, image_content, max_retries=5, delay=2):
    """
    Mengirim event ke API target dengan 5 kali percobaan.
    Fungsi ini TIDAK LAGI mengunduh gambar, tapi menerimanya sebagai 'image_content'.
    """
    target_api = device.get('targetApi')
    if not target_api:
        log(device, "Target API tidak diatur, pengiriman dilewati."), update_api_status(db_event_id, "skipped_no_api")
        return
        
    if not event_data.get("employeeId"):
        log(device, "Event tanpa employeeId, pengiriman ke API dilewati."), update_api_status(db_event_id, "skipped")
        return
        
    if not image_content:
        log(device, f"send_event_to_api dipanggil tanpa image_content (ID: {db_event_id}), menandai gagal.", level="ERROR")
        update_api_status(db_event_id, "failed")
        return
        
    try:
        image_base64 = base64.b64encode(image_content).decode('utf-8')
        image_status_msg = f"Terkirim ({len(image_content) // 1024} KB)"
    except Exception as e:
        log(device, f"Gagal encode base64 untuk event {db_event_id}: {e}", level="ERROR")
        update_api_status(db_event_id, "failed")
        return

    payload = {
        "device": event_data["deviceName"],
        "authId": event_data["employeeId"],
        "date": event_data["datetime_obj"].isoformat(),
        "picture": image_base64
    }
    
    log(device, f"Mengirim ke '{target_api}' -> authId: {payload['authId']}, image: {image_status_msg}")
    
    # --- MULAI RETRY LOOP UNTUK KIRIM API ---
    for attempt in range(1, max_retries + 1):
        try:
            r_api = requests.post(target_api, json=payload, timeout=REQUEST_TIMEOUT)
            status_code = r_api.status_code
            response_details = ""
            
            try:
                json_data = r_api.json()
                attendance_obj = json_data.get('attendance', {})
                received_auth_id = attendance_obj.get('student_id', 'N/A') if attendance_obj else 'N/A'
                received_device = attendance_obj.get('device', 'N/A') if attendance_obj else 'N/A'
                received_date = attendance_obj.get('date', 'N/A') if attendance_obj else 'N/A'
                response_status = json_data.get('status', 'N/A')
                response_details = (f"authId: {received_auth_id}, device: '{received_device}', date: {received_date}, status: {response_status}")
            except (json.JSONDecodeError, ValueError):
                response_details = f"Status Code [{status_code}], Body: {r_api.text[:100]}..."

            # Periksa status sukses (200 atau 201)
            if status_code in [200, 201]:
                log(device, f"Respons API (Sukses) <- {response_details}", level="INFO")
                update_api_status(db_event_id, "success")
                return # <-- SUKSES, keluar dari fungsi

            # Jika status tidak sukses, log sebagai error dan biarkan loop berlanjut (retry)
            log(device, f"[Attempt {attempt}/{max_retries}] Respons API (Gagal) <- {response_details}", level="ERROR")

        except requests.exceptions.RequestException as e:
            # Jika terjadi error koneksi, log dan biarkan loop berlanjut (retry)
            log(device, f"[Attempt {attempt}/{max_retries}] Gagal koneksi ke API: {e}", level="ERROR")
        
        if attempt < max_retries:
            time.sleep(delay) # Tunggu sebelum mencoba lagi
    
    # --- Jika loop selesai tanpa 'return', berarti GAGAL 5 KALI ---
    log(device, f"Gagal mengirim event (ID: {db_event_id}) ke API setelah {max_retries} percobaan.", level="ERROR")
    update_api_status(db_event_id, "failed")
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
    Fungsi ini sekarang menangani alur:
    1. Download gambar (dengan retry 5x).
    2. Simpan gambar ke disk (jika berhasil).
    3. Simpan event ke DB (dengan status API yang sesuai).
    4. Panggil 'send_event_to_api' (yang punya retry 5x) HANYA jika gambar berhasil di-download.
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

    # Langkah 1: Coba download gambar (hanya jika event valid dan punya URL)
    if dt and pictureURL and is_valid_for_api:
        image_content = download_image_with_retry(device, pictureURL, auth)
    
    # Langkah 2: Jika download berhasil, simpan ke disk
    if image_content:
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
        except Exception as e:
            log(device, f"Error simpan gambar ke disk (ID: {eventId}): {e}", level="WARN")
            local_image_path = None # Gagal simpan, tapi kita masih punya kontennya di 'image_content'
            
    # Langkah 3: Tentukan status API awal berdasarkan hasil download
    if is_valid_for_api:
        if image_content:
            initial_api_status = 'pending' # Siap dikirim
        else:
            initial_api_status = 'failed' # Gagal dikirim karena gambar tidak ada
    else:
        initial_api_status = 'skipped' # Bukan event untuk API

    # Langkah 4: Simpan event ke database
    conn, c, db_event_id = db.get_db(), None, None
    try:
        c = conn.cursor()
        sql = "INSERT INTO events (deviceName, eventId, employeeId, name, date, time, eventDesc, pictureURL, localImagePath, syncType, apiStatus) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        values = (device_name, eventId, employee_id, name, date_value, time_value, event_desc, pictureURL, local_image_path, sync_type, initial_api_status)
        c.execute(sql, values)
        db_event_id = c.lastrowid
        
        # Langkah 5: Panggil API HANYA jika event valid DAN gambar berhasil di-download
        if db_event_id and is_valid_for_api and image_content:
            api_data = {"deviceName": device_name, "employeeId": employee_id, "pictureURL": pictureURL, "datetime_obj": dt, "eventId": eventId}
            # Panggil send_event_to_api (yang sekarang memiliki retry 5x)
            send_event_to_api(api_data, device, user, password, db_event_id, image_content)
        
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
def ping_device_os(ip):
    param = "-n 1 -w 1000" if platform.system().lower() == "windows" else "-c 1 -W 1"
    cmd = f"ping {param} {ip}"
    return os.system(cmd + " > NUL 2>&1" if platform.system().lower()=="windows" else cmd + " > /dev/null 2>&1") == 0

def process_device(device):
    ip = device.get("ip")
    if not all([device.get("username"), device.get("password")]):
        log(device, "Username atau Password belum diatur. Dilewati.", level="WARN")
        return
    with DEVICE_DATA_LOCK:
        if ip in SUSPEND_UNTIL and time.time() < SUSPEND_UNTIL[ip]: return
    if not ping_device_os(ip):
        with DEVICE_DATA_LOCK:
            fail_count = FAIL_COUNT.get(ip, 0) + 1
            FAIL_COUNT[ip] = fail_count
            if fail_count >= PING_MAX_FAIL:
                if LAST_KNOWN_STATUS.get(ip) != 'offline':
                    log(device, f"Koneksi terputus. Disuspend selama {SUSPEND_SECONDS // 60} menit.", level="WARN")
                    set_device_status(ip, "offline")
                    LAST_KNOWN_STATUS[ip] = 'offline'
                SUSPEND_UNTIL[ip] = time.time() + SUSPEND_SECONDS
            else:
                log(device, f"Ping gagal, percobaan ke-{fail_count} dari {PING_MAX_FAIL}.", level="WARN")
        return
    with DEVICE_DATA_LOCK:
        if LAST_KNOWN_STATUS.get(ip) != 'online':
            log_message = "Terhubung kembali." if LAST_KNOWN_STATUS.get(ip) else "Terhubung, memulai sinkronisasi event..."
            log(device, log_message, level="OK" if LAST_KNOWN_STATUS.get(ip) else "INFO")
            set_device_status(ip, "online")
            LAST_KNOWN_STATUS[ip] = 'online'
        FAIL_COUNT[ip] = 0
        SUSPEND_UNTIL.pop(ip, None)
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
        
        last_seen_id = LAST_SEEN_EVENT_ID.get(ip, 0)
        new_events = [e for e in events if int(e.get("serialNo") or 0) > last_seen_id]

        if not new_events:
            return

        saved_count = 0
        for e in new_events:
            # Langkah 1: Selalu catat SEMUA event mentah
            log_raw_event(device, e)
            
            event_desc = get_event_desc(e)
            
            # Langkah 2: Periksa apakah ini event yang ingin kita proses lebih lanjut
            if event_desc == "Face Recognized":
                # Langkah 3: Tulis log bersih SEKARANG, sebelum menyimpan ke DB
                try:
                    time_value = datetime.datetime.strptime(e.get("time")[:19], "%Y-%m-%dT%H:%M:%S").strftime("%H:%M:%S")
                    sync_type = "realtime" if abs((datetime.datetime.now() - parse_iso_time(e.get("time"))).total_seconds()) <= 120 else "catch-up"
                    log(device, f"Memproses event '{sync_type}' - {time_value} (ID: {e.get('serialNo')}) untuk '{e.get('name')}'...")
                except Exception:
                    log(device, f"Memproses event (ID: {e.get('serialNo')}) untuk '{e.get('name')}'...")

                # Langkah 4: Coba simpan ke database (fungsi ini sekarang juga menangani download & API)
                if save_event(e, device):
                    saved_count += 1
        
        if saved_count > 0:
            log(device, f"Selesai, total {saved_count} event baru diproses untuk disimpan ke database.")
        
        newest_event = new_events[-1]
        newest_event_id = int(newest_event.get("serialNo") or 0)
        newest_event_time_str = newest_event.get("time")

        LAST_SEEN_EVENT_ID[ip] = newest_event_id
        if newest_event_time_str:
            set_last_sync_time(ip, newest_event_time_str)
            
    except Exception as e:
        with DEVICE_DATA_LOCK: LAST_KNOWN_STATUS[ip] = 'error'
        log(device, f"Terjadi error tak terduga: {e}", level="ERROR")
        set_device_status(ip, "error")
# ----------------------------------------------------

# --- MAIN LOOP (DIMODIFIKASI DENGAN CLEANUP) ---
def main_sync():
    db.init_db()
    log_system("Memulai Sinkronisasi Event Hikvision (Mode Multi-Thread)...")
    
    # Tambahkan variabel untuk melacak waktu cleanup
    # Set ke kemarin agar langsung jalan saat pertama kali service start
    last_cleanup_time = datetime.datetime.now() - datetime.timedelta(days=1) 
    
    try:
        while True:
            
            # --- Bagian Baru: Jalankan Cleanup Harian ---
            # Cek apakah sudah 24 jam (3600 * 24 = 86400 detik)
            if (datetime.datetime.now() - last_cleanup_time).total_seconds() > 86400:
                log_system("Menjalankan tugas cleanup harian (data > 60 hari)...")
                try:
                    # 1. Panggil fungsi cleanup log
                    deleted_log_folders = cleanup_old_logs(60)
                    log_system(f"Cleanup log selesai. {deleted_log_folders} folder log lama dihapus.")
                    
                    # 2. Panggil fungsi cleanup database & gambar
                    deleted_rows, deleted_files = db.cleanup_old_events_and_images(60)
                    log_system(f"Cleanup DB & gambar selesai. {deleted_rows} baris event dan {deleted_files} file gambar dihapus.")
                    
                    log_system("Tugas cleanup harian selesai.")
                    last_cleanup_time = datetime.datetime.now() # Reset timer
                except Exception as e:
                    log_system(f"Error saat menjalankan cleanup harian: {e}", level="ERROR")
            # --- Akhir Bagian Baru ---

            
            devices = db.get_all_devices()
            if not devices:
                log_system("Tidak ada device yang terdaftar. Menunggu 15 detik..."), time.sleep(15)
                continue
            with ThreadPoolExecutor(max_workers=len(devices)) as executor:
                executor.map(process_device, devices)
            
            time.sleep(POLL_INTERVAL) # Waktu poll interval yang ada
            
    except KeyboardInterrupt:
        log_system("Sinkronisasi dihentikan oleh pengguna.")
    except Exception as e:
        log_system(f"FATAL ERROR: {e}", level="ERROR")

# --- ENTRY POINT ---
if __name__ == "__main__":
    main_sync()