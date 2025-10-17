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
from getpass import getpass

# Impor konfigurasi dan modul database kustom
from config import *
import database as db

# --- Runtime Counters (Jangan Diubah) ---
FAIL_COUNT = {}
SUSPEND_UNTIL = {}
# ----------------------------------------

# --- EVENT_MAP hanya berisi satu event yang dianggap valid ---
EVENT_MAP = {
    (5, 75): "Face recognized"
}
# -------------------------------------------------------------

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
# ----------------------------------

# --- FUNGSI DATABASE (Spesifik untuk service ini) ---
def set_last_sync_time(ip, time_iso_str):
    try:
        dt = datetime.datetime.strptime(time_iso_str[:19], "%Y-%m-%dT%H:%M:%S")
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
    dt = datetime.datetime.now() - datetime.timedelta(days=1)
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
        print(f"Error updating API status: {e}")
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

# --- LOGGER & LOGGING ---
def log(device, message):
    """Menampilkan log ke konsol dengan format standar."""
    label = device_label(device)
    now = datetime.datetime.now()
    t = now.strftime("%H:%M:%S")
    d = now.strftime("%d-%m-%Y")
    print(f"[{label}] [{t}] [{d}] {message}")

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
        print(f"    +-- [INFO] Payload lengkap tersimpan di: {file_path}")
    except Exception as e:
        print(f"    +-- [WARN] Gagal menyimpan payload ke file log: {e}")

# --- API & IMAGE ---
def send_event_to_api(event_data, device, user, password, db_event_id):
    """Mengirim data event ke target API spesifik milik perangkat."""
    target_api = device.get('targetApi')
    
    if not target_api:
        print("    +-- [INFO] Target API tidak diatur untuk perangkat ini. Pengiriman dilewati.")
        update_api_status(db_event_id, "skipped_no_api")
        return

    if not event_data.get("employeeId"):
        print("    +-- [INFO] Event tanpa employeeId, pengiriman ke API dilewati.")
        update_api_status(db_event_id, "skipped")
        return
    
    try:
        r_img = requests.get(event_data["pictureURL"], auth=HTTPDigestAuth(user, password), timeout=15)
        if r_img.status_code == 200:
            image_base64 = base64.b64encode(r_img.content).decode('utf-8')
            image_status_msg = f"Terkirim ({len(r_img.content) // 1024} KB)"
        else:
            print(f"    +-- [WARN] Gagal mendapatkan gambar dari device (HTTP {r_img.status_code}).")
            update_api_status(db_event_id, "failed")
            return
    except Exception as e:
        print(f"    +-- [ERROR] Error koneksi saat mengambil gambar: {e}")
        update_api_status(db_event_id, "failed")
        return

    payload = {
        "device": event_data["deviceName"], "authId": event_data["employeeId"],
        "date": event_data["datetime_obj"].isoformat(), "picture": image_base64
    }
    
    save_payload_to_log_file(payload, device, event_data.get("eventId", "unknown"))
    sent_details = (f"authId: {payload['authId']}, device: '{payload['device']}', date: {payload['date']}, image: {image_status_msg}")
    print(f"    +-- [INFO] Mengirim ke '{target_api}' -> {sent_details}")

    try:
        r_api = requests.post(target_api, json=payload, timeout=20)
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
        log_char = "[OK]" if status_code in [200, 201] else "[FAIL]"
        print(f"    +-- {log_char} Respons <- {response_details}")
        update_api_status(db_event_id, "success" if status_code in [200, 201] else "failed")
    except requests.exceptions.RequestException as e:
        print(f"    +-- [ERROR] Gagal koneksi ke API: {e}")
        update_api_status(db_event_id, "failed")

# --- HIKVISION & EVENT PROCESSING ---
def iso8601_now(offset_seconds=0):
    t = datetime.datetime.now() - datetime.timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE

def get_events_from_device(ip, user, password, start_time, end_time):
    url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    body = {"AcsEventCond": {"searchID": "batch", "searchResultPosition": 0, "maxResults": BATCH_MAX_RESULTS,
                             "major": 0, "minor": 0, "startTime": start_time, "endTime": end_time}}
    r = requests.post(url, json=body, auth=HTTPDigestAuth(user, password), timeout=15)
    r.raise_for_status()
    return r.json().get("AcsEvent", {}).get("InfoList", [])

def get_event_desc(event):
    """
    Mengembalikan deskripsi event. Jika event dikenali, kembalikan namanya.
    Jika tidak, kembalikan 'Event Tidak Dikenali' secara standar.
    """
    return EVENT_MAP.get((event.get("major"), event.get("minor")), "Event Tidak Dikenali")

def save_event(event, device, user, password):
    eventId, device_name, pictureURL = event.get("serialNo"), device_label(device), event.get("pictureURL")
    if not pictureURL or event_exists(eventId, device_name):
        return False
    try:
        dt = datetime.datetime.strptime(event.get("time")[:19], "%Y-%m-%dT%H:%M:%S")
        date_value, time_value = dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        dt, date_value, time_value = None, "0000-00-00", "00:00:00"
    
    employee_id = int(event["employeeNoString"]) if event.get("employeeNoString", "").isdigit() else None
    name = event.get("name") or "unknown"
    
    event_desc = get_event_desc(event)
    is_valid_event = (event_desc == "Face recognized")
    
    log(device, f"[INFO] Memproses event baru (ID: {eventId}) untuk '{name}'...")
    
    if not is_valid_event:
        print(f"    +-- [INFO] Event '{event_desc}' tidak valid. Akan ditandai sebagai gagal.")
        
    initial_api_status = 'pending' if is_valid_event else 'failed'

    local_image_path = None
    if dt:
        try:
            dev_label = device.get("name") or device.get("ip")
            safe_dev = sanitize_name(dev_label)
            date_folder = dt.strftime("%Y-%m-%d")
            relative_folder = os.path.join("images", safe_dev, date_folder)
            absolute_folder = os.path.join("static", relative_folder)
            if not os.path.exists(absolute_folder): os.makedirs(absolute_folder, exist_ok=True)
            r_img = requests.get(pictureURL, auth=HTTPDigestAuth(user, password), timeout=15)
            if r_img.status_code == 200:
                safe_name = sanitize_name(name) if name else "unknown"
                file_name = f"{safe_name}-{eventId}.jpg"
                local_image_path = os.path.join(relative_folder, file_name).replace("\\", "/")
                absolute_file_path = os.path.join(absolute_folder, file_name)
                with open(absolute_file_path, "wb") as f:
                    f.write(r_img.content)
                print(f"    +-- [INFO] Gambar tersimpan di: {absolute_file_path}")
        except Exception as e:
            print(f"    +-- [WARN] Error download gambar: {e}")

    sync_type = "realtime" if dt and abs((datetime.datetime.now() - dt).total_seconds()) <= 100 else "catch-up"
    conn, c, db_event_id = db.get_db(), None, None
    try:
        c = conn.cursor()
        sql = "INSERT INTO events (deviceName, eventId, employeeId, name, date, time, eventDesc, pictureURL, localImagePath, syncType, apiStatus) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        values = (device_name, eventId, employee_id, name, date_value, time_value, event_desc, pictureURL, local_image_path, sync_type, initial_api_status)
        c.execute(sql, values)
        db_event_id = c.lastrowid
        
        if db_event_id and is_valid_event:
            api_data = {
                "deviceName": device_name, "employeeId": employee_id, "pictureURL": pictureURL, 
                "datetime_obj": dt, "eventId": eventId
            }
            send_event_to_api(api_data, device, user, password, db_event_id)
        return True
    except mysql.connector.IntegrityError: 
        return False
    except Exception as e: 
        print(f"    +-- [ERROR] DB error: {e}")
        return False
    finally:
        if c: c.close()
        conn.close()

# --- PING & MAIN LOOP ---
def ping_device_os(ip):
    param = "-n 1 -w 1000" if platform.system().lower() == "windows" else "-c 1 -W 1"
    cmd = f"ping {param} {ip}"
    return os.system(cmd + " > NUL 2>&1" if platform.system().lower()=="windows" else cmd + " > /dev/null 2>&1") == 0

def main_sync():
    db.init_db()
    print("[INFO] Memulai Sinkronisasi Event Hikvision... (CTRL+C untuk berhenti)\n")
    try:
        while True:
            devices = db.get_all_devices()
            if not devices:
                print("[INFO] Tidak ada device yang terdaftar. Menunggu 15 detik...")
                time.sleep(15)
                continue
            for device in devices:
                ip = device.get("ip")
                username = device.get("username")
                password = device.get("password")

                if not username or not password:
                    log(device, "[WARN] Username atau Password belum diatur untuk perangkat ini. Dilewati.")
                    continue

                if ip in SUSPEND_UNTIL and time.time() < SUSPEND_UNTIL[ip]: continue
                if not ping_device_os(ip):
                    FAIL_COUNT[ip] = FAIL_COUNT.get(ip, 0) + 1
                    if FAIL_COUNT[ip] >= PING_MAX_FAIL:
                        SUSPEND_UNTIL[ip] = time.time() + SUSPEND_SECONDS
                        log(device, f"[WARN] Koneksi terputus. Perangkat disuspend selama {SUSPEND_SECONDS // 60} menit.")
                        set_device_status(ip, "offline")
                    else:
                        log(device, f"[WARN] Ping gagal, percobaan ke-{FAIL_COUNT[ip]} dari {PING_MAX_FAIL}.")
                    continue
                was_failing = FAIL_COUNT.get(ip, 0) > 0 or device.get('status') != 'online'
                FAIL_COUNT[ip] = 0
                SUSPEND_UNTIL.pop(ip, None)
                if was_failing:
                    log(device, "[OK] Terhubung kembali. Memeriksa event...")
                    set_device_status(ip, "online")
                try:
                    last_sync = get_last_sync_time(ip)
                    now_time = iso8601_now()
                    events = get_events_from_device(ip, username, password, last_sync, now_time)
                    if not events: continue
                    events.sort(key=lambda x: int(x.get("serialNo") or 0))
                    newest_time, saved_count = last_sync, 0
                    for e in events:
                        if save_event(e, device, username, password):
                            newest_time = e.get("time", newest_time)
                            saved_count += 1
                    if saved_count > 0: log(device, f"Total {saved_count} event baru berhasil diproses.")
                    if newest_time != last_sync: set_last_sync_time(ip, newest_time)
                except Exception as e:
                    log(device, f"[ERROR] Error saat proses event: {e}")
                    set_device_status(ip, "error")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n[INFO] Sinkronisasi dihentikan oleh pengguna.")
    except Exception as e:
        print(f"\n[FATAL] FATAL ERROR: {e}")

# --- ENTRY POINT ---
if __name__ == "__main__":
    main_sync()