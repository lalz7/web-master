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
from logging.handlers import TimedRotatingFileHandler
import threading
from concurrent.futures import ThreadPoolExecutor
import random

# Impor konfigurasi (termasuk EVENT_MAP) dan modul database kustom
from config import *
import database as db

# --- SETUP LOGGING PROFESIONAL ---
logger = logging.getLogger("SyncServiceLogger")
logger.setLevel(logging.INFO)
# Hapus handler default jika ada untuk menghindari duplikasi
if logger.hasHandlers():
    logger.handlers.clear()
    
formatter = logging.Formatter('%(message)s')

# Handler untuk konsol
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# Handler untuk file harian
if SERVICE_LOG_DIR:
    try:
        if not os.path.exists(SERVICE_LOG_DIR):
            os.makedirs(SERVICE_LOG_DIR)
        
        log_file = os.path.join(SERVICE_LOG_DIR, "sync_service.log")
        file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=30, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"FATAL: Gagal menginisialisasi file logger: {e}")
# --- AKHIR SETUP LOGGING ---


# --- Variabel Global & Kunci Thread ---
FAIL_COUNT = {}
SUSPEND_UNTIL = {}
LAST_KNOWN_STATUS = {} 
DATA_LOCK = threading.Lock() # Kunci untuk melindungi akses ke variabel global di atas
# ----------------------------------------

# --- FUNGSI BANTU (HELPERS) ---
def sanitize_name(name):
    """Membuat nama folder/file aman dari karakter aneh."""
    if not name:
        return "unknown"
    s = re.sub(r"[^a-zA-Z0-9 _-]", "_", name).strip()
    return s.replace(" ", "_")

def device_label(device):
    """Mengembalikan nama device, atau IP jika nama kosong."""
    return device.get("name") or device.get("ip")

def parse_iso_time(time_str):
    """Mengubah string ISO 8601 menjadi objek datetime."""
    # Menghilangkan informasi timezone untuk perbandingan
    return datetime.datetime.fromisoformat(time_str.replace(TIMEZONE, ''))
# ----------------------------------

# --- FUNGSI DATABASE (Spesifik untuk service ini) ---
def set_last_sync_time(ip, time_iso_str):
    try:
        dt = parse_iso_time(time_iso_str)
    except Exception:
        dt = datetime.datetime.now()
    
    conn = db.get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET lastSync=%s WHERE ip=%s", (dt, ip))
    c.close()
    conn.close()

def get_last_sync_time(ip):
    conn = db.get_db()
    c = conn.cursor()
    c.execute("SELECT lastSync FROM devices WHERE ip=%s", (ip,))
    row = c.fetchone()
    c.close()
    conn.close()
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
    c.close()
    conn.close()

def update_api_status(db_id, status):
    conn = db.get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE events SET apiStatus=%s WHERE id=%s", (status, db_id))
    except Exception as e:
        log_system(f"Error updating API status: {e}", level="ERROR")
    finally:
        c.close()
        conn.close()
        
def event_exists(eventId, device_name):
    conn = db.get_db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM events WHERE eventId=%s AND deviceName=%s", (eventId, device_name))
    exists = c.fetchone() is not None
    c.close()
    conn.close()
    return exists
# ----------------------------------------------------

# --- FUNGSI LOGGING KUSTOM ---
def log(device, message, level="INFO"):
    """
    Memformat dan mencatat pesan untuk perangkat tertentu.
    Urutan: [Tanggal] [Waktu] [Nama Perangkat] [Level] Pesan
    """
    label = device_label(device)
    now = datetime.datetime.now()
    d = now.strftime("%d-%m-%Y")
    t = now.strftime("%H:%M:%S")
    
    level_map = {"OK": "INFO", "WARN": "WARNING"}
    log_level = level_map.get(level.upper(), level.upper())
    
    formatted_message = f"[{d}] [{t}] [{label}] [{log_level}] {message}"
    
    if log_level == "ERROR":
        logger.error(formatted_message)
    elif log_level == "WARNING":
        logger.warning(formatted_message)
    else:
        logger.info(formatted_message)

def log_system(message, level="INFO"):
    """Mencatat pesan umum sistem."""
    now = datetime.datetime.now()
    d = now.strftime("%d-%m-%Y")
    t = now.strftime("%H:%M:%S")
    log_level = level.upper()
    
    formatted_message = f"[{d}] [{t}] [SYSTEM] [{log_level}] {message}"

    if log_level == "ERROR":
        logger.error(formatted_message)
    elif log_level == "WARNING":
        logger.warning(formatted_message)
    else:
        logger.info(formatted_message)
# --- AKHIR FUNGSI LOGGING ---


def save_payload_to_log_file(payload, device, eventId):
    """Menyimpan payload lengkap ke file JSON terpisah."""
    try:
        if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H-%M-%S")
        dev_label = sanitize_name(device_label(device))
        file_name = f"{timestamp}__{dev_label}__{eventId}.json"
        file_path = os.path.join(LOG_DIR, file_name)
        with open(file_path, 'w') as f:
            json.dump(payload, f, indent=4)
        log(device, f"Payload lengkap tersimpan di: {file_path}")
    except Exception as e:
        log(device, f"Gagal menyimpan payload ke file log: {e}", level="WARN")

# --- API & IMAGE ---
def send_event_to_api(event_data, device, user, password, db_event_id):
    """Mengirim data event ke target API spesifik milik perangkat."""
    target_api = device.get('targetApi')
    
    if not target_api:
        log(device, "Target API tidak diatur, pengiriman dilewati.")
        update_api_status(db_event_id, "skipped_no_api")
        return

    if not event_data.get("employeeId"):
        log(device, "Event tanpa employeeId, pengiriman ke API dilewati.")
        update_api_status(db_event_id, "skipped")
        return
    
    try:
        r_img = requests.get(event_data["pictureURL"], auth=HTTPDigestAuth(user, password), timeout=REQUEST_TIMEOUT)
        if r_img.status_code == 200:
            image_base64 = base64.b64encode(r_img.content).decode('utf-8')
            image_status_msg = f"Terkirim ({len(r_img.content) // 1024} KB)"
        else:
            log(device, f"Gagal mendapatkan gambar dari device (HTTP {r_img.status_code}).", level="WARN")
            update_api_status(db_event_id, "failed")
            return
    except Exception as e:
        log(device, f"Error koneksi saat mengambil gambar: {e}", level="ERROR")
        update_api_status(db_event_id, "failed")
        return

    payload = {
        "device": event_data["deviceName"], "authId": event_data["employeeId"],
        "date": event_data["datetime_obj"].isoformat(), "picture": image_base64
    }
    
    save_payload_to_log_file(payload, device, event_data.get("eventId", "unknown"))
    
    sent_details = (f"authId: {payload['authId']}, device: '{payload['device']}', date: {payload['date']}, image: {image_status_msg}")
    log(device, f"Mengirim ke '{target_api}' -> {sent_details}")

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
        
        log_level = "INFO" if status_code in [200, 201] else "ERROR"
        log(device, f"Respons API <- {response_details}", level=log_level)
        update_api_status(db_event_id, "success" if status_code in [200, 201] else "failed")
    except requests.exceptions.RequestException as e:
        log(device, f"Gagal koneksi ke API: {e}", level="ERROR")
        update_api_status(db_event_id, "failed")

# --- HIKVISION & EVENT PROCESSING ---
def iso8601_now(offset_seconds=0):
    t = datetime.datetime.now() - datetime.timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE

def get_events_from_device(device, start_time, end_time):
    """Mengambil event, kini menerima seluruh objek device untuk kredensial."""
    ip = device.get("ip")
    user = device.get("username")
    password = device.get("password")
    
    url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    body = {"AcsEventCond": {"searchID": "batch", "searchResultPosition": 0, "maxResults": BATCH_MAX_RESULTS,
                             "major": 0, "minor": 0, "startTime": start_time, "endTime": end_time}}
    
    try:
        r = requests.post(url, json=body, auth=HTTPDigestAuth(user, password), timeout=REQUEST_TIMEOUT)
        r.raise_for_status() 
        
        events_list = r.json().get("AcsEvent", {}).get("InfoList", [])
        return events_list
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            log(device, "Gagal mengambil event: 401 Unauthorized. Periksa username dan password.", level="ERROR")
        else:
            log(device, f"Gagal mengambil event. HTTP Error: {e}", level="ERROR")
    except requests.exceptions.RequestException as e:
        log(device, f"Gagal mengambil event. Error koneksi: {e}", level="ERROR")
    return [] 

def get_event_desc(event):
    """Mengembalikan deskripsi event dari EVENT_MAP di config.py."""
    major = event.get("major")
    minor = event.get("minor")
    # Gunakan fallback dalam Bahasa Indonesia
    return EVENT_MAP.get((major, minor), f"Event tidak dikenali (Major: {major}, Minor: {minor})")

def save_event(event, device):
    """Menyimpan event, kini menerima seluruh objek device."""
    user = device.get("username")
    password = device.get("password")
    
    eventId, device_name, pictureURL = event.get("serialNo"), device_label(device), event.get("pictureURL")
    
    # Event tanpa URL gambar tidak bisa diproses lebih lanjut
    if not pictureURL:
        return False
        
    name_from_device = event.get("name")
    if not name_from_device or name_from_device.lower() == 'unknown':
        name = "unknown"
    else:
        name = name_from_device

    if event_exists(eventId, device_name):
        return False
        
    try:
        dt = datetime.datetime.strptime(event.get("time")[:19], "%Y-%m-%dT%H:%M:%S")
        date_value, time_value = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        dt, date_value, time_value = None, "0000-00-00", "00:00:00"
    
    sync_type = "realtime" if dt and abs((datetime.datetime.now() - dt).total_seconds()) <= 120 else "catch-up"
    
    event_desc = get_event_desc(event)
    
    log(device, f"Memproses event '{sync_type}' - {time_value} (ID: {eventId}) | {event_desc} untuk '{name}'...")
        
    employee_id = int(event["employeeNoString"]) if event.get("employeeNoString", "").isdigit() else None
    
    # Kondisi spesifik untuk mengirim ke API, sesuai permintaan.
    is_valid_for_api = (event_desc == "Face recognized")
    
    if not is_valid_for_api:
        log(device, f"Info: Event '{event_desc}' tidak akan dikirim ke API (ID: {eventId}).")
        
    # Status API awal: 'pending' hanya jika akan dikirim, selain itu 'skipped'.
    initial_api_status = 'pending' if is_valid_for_api else 'skipped'

    local_image_path = None
    if dt:
        try:
            dev_label = device.get("name") or device.get("ip")
            safe_dev = sanitize_name(dev_label)
            date_folder = dt.strftime("%Y-%m-%d")
            relative_folder = os.path.join("images", safe_dev, date_folder)
            absolute_folder = os.path.join("static", relative_folder)
            if not os.path.exists(absolute_folder): os.makedirs(absolute_folder, exist_ok=True)
            r_img = requests.get(pictureURL, auth=HTTPDigestAuth(user, password), timeout=REQUEST_TIMEOUT)
            if r_img.status_code == 200:
                safe_name = sanitize_name(name) if name else "unknown"
                file_name = f"{safe_name}-{eventId}.jpg"
                local_image_path = os.path.join(relative_folder, file_name).replace("\\", "/")
                absolute_file_path = os.path.join(absolute_folder, file_name)
                with open(absolute_file_path, "wb") as f:
                    f.write(r_img.content)
        except Exception as e:
            log(device, f"Error download gambar (ID: {eventId}): {e}", level="WARN")

    conn, c, db_event_id = db.get_db(), None, None
    try:
        c = conn.cursor()
        sql = "INSERT INTO events (deviceName, eventId, employeeId, name, date, time, eventDesc, pictureURL, localImagePath, syncType, apiStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        values = (device_name, eventId, employee_id, name, date_value, time_value, event_desc, pictureURL, local_image_path, sync_type, initial_api_status)
        c.execute(sql, values)
        db_event_id = c.lastrowid
        
        log(device, f"Berhasil menyimpan event (ID: {eventId}) ke database.")

        # Pengiriman API hanya dilakukan jika kondisi terpenuhi
        if db_event_id and is_valid_for_api:
            api_data = {
                "deviceName": device_name, "employeeId": employee_id, "pictureURL": pictureURL, 
                "datetime_obj": dt, "eventId": eventId
            }
            send_event_to_api(api_data, device, user, password, db_event_id)
        return True
    except mysql.connector.IntegrityError: 
        return False
    except Exception as e: 
        log(device, f"DB error (ID: {eventId}): {e}", level="ERROR")
        return False
    finally:
        if c: c.close()
        conn.close()

# --- PING & FUNGSI PEKERJA (WORKER) ---
def ping_device_os(ip):
    param = "-n 1 -w 1000" if platform.system().lower() == "windows" else "-c 1 -W 1"
    cmd = f"ping {param} {ip}"
    return os.system(cmd + " > NUL 2>&1" if platform.system().lower()=="windows" else cmd + " > /dev/null 2>&1") == 0

def process_device(device):
    """Fungsi ini berisi logika untuk memproses satu perangkat. Akan dijalankan oleh setiap thread."""
    
    time.sleep(random.uniform(0, 3))

    ip = device.get("ip")
    
    if not device.get("username") or not device.get("password"):
        log(device, "Username atau Password belum diatur. Dilewati.", level="WARN")
        return

    with DATA_LOCK:
        if ip in SUSPEND_UNTIL and time.time() < SUSPEND_UNTIL[ip]:
            return

    if not ping_device_os(ip):
        with DATA_LOCK:
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

    with DATA_LOCK:
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
        
        start_dt = parse_iso_time(last_sync_str)
        end_dt = parse_iso_time(now_time_str)
        
        time_diff_seconds = (end_dt - start_dt).total_seconds()
        
        CATCH_UP_CHUNK_MINUTES = 10
        BIG_CATCHUP_THRESHOLD_SECONDS = 3600 # 1 jam

        if time_diff_seconds > BIG_CATCHUP_THRESHOLD_SECONDS:
            
            current_start_dt = start_dt
            all_events = []
            
            while current_start_dt < end_dt:
                current_end_dt = current_start_dt + datetime.timedelta(minutes=CATCH_UP_CHUNK_MINUTES)
                if current_end_dt > end_dt:
                    current_end_dt = end_dt
                
                start_chunk_str = current_start_dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE
                end_chunk_str = current_end_dt.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE
                
                chunk_events = get_events_from_device(device, start_chunk_str, end_chunk_str)
                if chunk_events:
                    all_events.extend(chunk_events)
                
                current_start_dt = current_end_dt
            
            events = all_events
        else:
            events = get_events_from_device(device, last_sync_str, now_time_str)

        if not events:
            return
        
        events.sort(key=lambda x: int(x.get("serialNo") or 0))
        
        newest_time = last_sync_str
        saved_count = 0
        
        for e in events:
            if save_event(e, device):
                newest_time = e.get("time", newest_time)
                saved_count += 1
        
        if saved_count > 0:
            log(device, f"Selesai, total {saved_count} event baru berhasil diproses.")
        
        if newest_time != last_sync_str:
            set_last_sync_time(ip, newest_time)
    
    except Exception as e:
        with DATA_LOCK:
            LAST_KNOWN_STATUS[ip] = 'error'
        log(device, f"Terjadi error tak terduga: {e}", level="ERROR")
        set_device_status(ip, "error")

# --- MAIN LOOP ---
def main_sync():
    db.init_db()
    log_system("Memulai Sinkronisasi Event Hikvision (Mode Multi-Thread)...")
    try:
        while True:
            devices = db.get_all_devices()
            if not devices:
                log_system("Tidak ada device yang terdaftar. Menunggu 15 detik...")
                time.sleep(15)
                continue

            num_workers = len(devices)
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                executor.map(process_device, devices)

            time.sleep(POLL_INTERVAL)
    
    except KeyboardInterrupt:
        log_system("Sinkronisasi dihentikan oleh pengguna.")
    except Exception as e:
        log_system(f"FATAL ERROR: {e}", level="ERROR")

# --- ENTRY POINT ---
if __name__ == "__main__":
    main_sync()