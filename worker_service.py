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
import shutil

# Impor konfigurasi dan modul database kustom
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

def log_system(message, level="INFO"):
    """Mencatat log SISTEM ke folder EVENT_LOG_DIR."""
    file_logger = get_logger(EVENT_LOG_DIR, "system_worker") # Log terpisah
    log_level = level.upper()
    if log_level == "ERROR": file_logger.error(message)
    elif log_level == "WARNING": file_logger.warning(message)
    else: file_logger.info(message)
    
    now = datetime.datetime.now()
    d, t = now.strftime("%d-%m-%Y"), now.strftime("%H:%M:%S")
    console_message = f"[{d}] [{t}] [WORKER] [{log_level}] {message}"
    console_logger.info(console_message)

def cleanup_old_logs(days_to_keep):
    """Menghapus folder log yang lebih tua dari days_to_keep."""
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days_to_keep)
    log_dirs = [EVENT_LOG_DIR, SERVICE_LOG_DIR]
    
    deleted_folders = 0
    for log_dir in log_dirs:
        if not os.path.isdir(log_dir):
            continue
        for date_folder_name in os.listdir(log_dir):
            full_folder_path = os.path.join(log_dir, date_folder_name)
            if not os.path.isdir(full_folder_path):
                continue
            try:
                folder_date = datetime.datetime.strptime(date_folder_name, '%Y-%m-%d')
                if folder_date < cutoff_date:
                    shutil.rmtree(full_folder_path)
                    deleted_folders += 1
            except (ValueError, OSError) as e:
                log_system(f"Gagal memproses/menghapus folder log {full_folder_path}: {e}", level="WARN")
    return deleted_folders
# --- AKHIR SETUP LOGGING ---

# --- Variabel Global & Kunci Thread ---
FAIL_COUNT, SUSPEND_UNTIL, LAST_KNOWN_STATUS = {}, {}, {}
DEVICE_DATA_LOCK = threading.Lock()
# ----------------------------------------

# --- FUNGSI HELPER (Waktu & Notifikasi) ---
def get_indonesian_month_name(now):
    months_map = {
        1: 'Januari', 2: 'Februari', 3: 'Maret', 4: 'April', 5: 'Mei', 6: 'Juni',
        7: 'Juli', 8: 'Agustus', 9: 'September', 10: 'Oktober', 11: 'November', 12: 'Desember'
    }
    return months_map.get(now.month, '')

def _send_wa_request(target_number, message_text, wa_api_url):
    """Fungsi internal untuk mengirim request WA. Dijalankan di thread terpisah."""
    try:
        if not wa_api_url or not target_number:
            log_system(f"WA: URL API ({wa_api_url}) atau Nomor Tujuan ({target_number}) kosong.", level="WARN")
            return
        
        # Mengambil timeout dari DB
        try:
            timeout = int(db.get_setting('request_timeout', '30'))
        except ValueError:
            timeout = 30

        endpoint = wa_api_url.rstrip('/') + "/kirim-pesan-gambar-caption"
        payload = {'recipientId': target_number, 'recipient': target_number, 'message': message_text}
        response = requests.post(endpoint, data=payload, timeout=timeout) # <-- Menggunakan timeout
        
        if response.status_code == 200:
            log_system(f"WA: Notifikasi berhasil dikirim ke {target_number}.", level="INFO")
        else:
            log_system(f"WA: Gagal mengirim notifikasi ke {target_number} (Status: {response.status_code}). Response: {response.text[:100]}", level="ERROR")
            
    except requests.exceptions.RequestException as e:
        log_system(f"WA: Gagal koneksi ke server WhatsApp API ({wa_api_url}). Error: {e}", level="ERROR")
    except Exception as e:
        log_system(f"WA: Terjadi error tidak terduga saat mengirim ke {target_number}. Error: {e}", level="ERROR")

def send_whatsapp_notification(message_text, check_setting_key='whatsapp_enabled'):
    """
    Mengirim notifikasi WhatsApp ke SEMUA nomor yang terdaftar di thread terpisah.
    """
    try:
        wa_enabled = db.get_setting(check_setting_key, 'false')
        if wa_enabled != 'true':
            return # Fitur dinonaktifkan di pengaturan

        wa_api_url = db.get_setting('whatsapp_api_url')
        number_string = db.get_setting('whatsapp_target_number', '')
        
        if not number_string or not wa_api_url:
             log_system(f"WA: Gagal mengirim, URL API atau Nomor Tujuan belum diatur.", level="WARN")
             return
        
        numbers = number_string.split(',')
        log_system(f"WA: Mempersiapkan notifikasi ({check_setting_key}) ke {len(numbers)} nomor...", level="INFO")

        for num in numbers:
            num = num.strip()
            if num.startswith('62') and num[2:].isdigit():
                t = threading.Thread(target=_send_wa_request, args=(num, message_text, wa_api_url))
                t.daemon = True
                t.start()
            elif num:
                log_system(f"WA: Melewatkan nomor '{num}', format salah (harus 62...).", level="WARN")
        
    except Exception as e:
        log_system(f"WA: Gagal memulai thread notifikasi. Error: {e}", level="ERROR")

# --- FUNGSI PING (Tugas Jaringan) ---
def ping_device_os(ip):
    param = "-n 1 -w 1000" if platform.system().lower() == "windows" else "-c 1 -W 1"
    cmd = f"ping {param} {ip}"
    return os.system(cmd + " > NUL 2>&1" if platform.system().lower()=="windows" else cmd + " > /dev/null 2>&1") == 0

def check_device_status(device, ping_max_fail, suspend_seconds):
    """
    Satu fungsi yang dijalankan di thread untuk mengecek satu perangkat.
    Ini adalah logika yang dipindah dari sync_service.py
    """
    ip = device.get("ip")
    
    with DEVICE_DATA_LOCK:
        if ip in SUSPEND_UNTIL and time.time() < SUSPEND_UNTIL[ip]:
            return # Masih dalam masa suspend
            
    if not ping_device_os(ip):
        with DEVICE_DATA_LOCK:
            fail_count = FAIL_COUNT.get(ip, 0) + 1
            FAIL_COUNT[ip] = fail_count
            if fail_count >= ping_max_fail:
                if LAST_KNOWN_STATUS.get(ip) != 'offline':
                    log_system(f"Device {device.get('name')} OFFLINE.", level="WARN")
                    db.update_device_ping_status(ip, "offline")
                    LAST_KNOWN_STATUS[ip] = 'offline'
                    
                    now = datetime.datetime.now()
                    month_name = get_indonesian_month_name(now)
                    location = device.get('location') or '-'
                    date_line = f"{now.day} {month_name} {now.year}"
                    time_line = now.strftime("%H:%M:%S WIB")
                    
                    message = (
                        f"🚨 PERINGATAN OFFLINE 🚨\n\n"
                        f"Perangkat:\n*{device.get('name')}* - *{location}*\n"
                        f"(IP: {ip})\n\n"
                        f"Telah OFFLINE pada:\n"
                        f"{date_line}\n{time_line}\n\n"
                        f"Layanan sinkronisasi ditangguhkan."
                    )
                    send_whatsapp_notification(message, 'whatsapp_enabled')
                    
                SUSPEND_UNTIL[ip] = time.time() + suspend_seconds
    else:
        with DEVICE_DATA_LOCK:
            if LAST_KNOWN_STATUS.get(ip) != 'online':
                if LAST_KNOWN_STATUS.get(ip) in ['offline', 'error']:
                    log_system(f"Device {device.get('name')} ONLINE.", level="INFO")
                    now = datetime.datetime.now()
                    month_name = get_indonesian_month_name(now)
                    location = device.get('location') or '-'
                    date_line = f"{now.day} {month_name} {now.year}"
                    time_line = now.strftime("%H:%M:%S WIB")
                    
                    message = (
                        f"✅ PEMULIHAN KONEKSI ✅\n\n"
                        f"Perangkat:\n*{device.get('name')}* - *{location}*\n"
                        f"(IP: {ip})\n\n"
                        f"Telah ONLINE kembali pada:\n"
                        f"{date_line}\n{time_line}\n\n"
                        f"Layanan sinkronisasi dilanjutkan."
                    )
                    send_whatsapp_notification(message, 'whatsapp_enabled')
                
                db.update_device_ping_status(ip, "online")
                LAST_KNOWN_STATUS[ip] = 'online'
                
            FAIL_COUNT[ip] = 0
            SUSPEND_UNTIL.pop(ip, None)

# --- FUNGSI PENGIRIM API (Tugas Antrean) ---

def download_image_from_event(event):
    """
    Mencoba mengunduh gambar event jika ada.
    Menggunakan pengaturan dari DB.
    """
    pictureURL = event.get('pictureURL')
    if not pictureURL:
        log_system(f"Event {event['id']} tidak punya pictureURL.", "WARN")
        return None
        
    auth = HTTPDigestAuth(event['deviceUsername'], event['devicePassword'])
    
    # --- PENGATURAN BARU DARI DB ---
    try:
        max_retries = int(db.get_setting('worker_download_retries', '2'))
        timeout = int(db.get_setting('request_timeout', '30'))
    except ValueError:
        max_retries = 2
        timeout = 30
    # -------------------------------

    for attempt in range(1, max_retries + 1): # <-- Menggunakan var
        try:
            r_img = requests.get(pictureURL, auth=auth, timeout=timeout) # <-- Menggunakan var
            if r_img.status_code == 200:
                return r_img.content
            log_system(f"Gagal unduh gambar (event {event['id']}) (HTTP {r_img.status_code}).", "WARN")
        except requests.exceptions.RequestException as e:
            log_system(f"Error koneksi saat unduh gambar (event {event['id']}): {e}", "WARN")
        time.sleep(1) # Jeda 1 detik antar retry
    return None

# --- FUNGSI process_api_event DIMODIFIKASI ---
def process_api_event(event, api_fail_max_retry):
    """
    Satu fungsi yang dijalankan di thread untuk memproses satu event API.
    Akan tetap mengirim API meskipun gambar gagal diunduh.
    """
    event_id = event['id']
    target_api = event['targetApi']
    retry_count = event['apiRetryCount']
    
    try:
        # 1. Buat payload dasar (data teks)
        event_time_obj = datetime.datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M:%S")
        payload = {
            "device": event["deviceName"],
            "authId": event["employeeId"],
            "date": event_time_obj.isoformat(),
            "picture": None # Default adalah None (null)
        }

        # 2. Coba dapatkan gambar.
        image_content = None
        if event['localImagePath'] and os.path.exists(os.path.join("static", event['localImagePath'])):
            try:
                with open(os.path.join("static", event['localImagePath']), "rb") as f:
                    image_content = f.read()
            except Exception as e:
                log_system(f"Gagal baca file lokal {event['localImagePath']}: {e}", "WARN")

        # 3. Jika gagal baca lokal (atau tidak ada), unduh dari perangkat
        if not image_content:
            log_system(f"File lokal tidak ada untuk event {event_id}, mencoba unduh ulang...", "INFO")
            image_content = download_image_from_event(event)
        
        # 4. Tambahkan gambar ke payload HANYA JIKA ADA
        if image_content:
            image_base64 = base64.b64encode(image_content).decode('utf-8')
            payload["picture"] = image_base64
        else:
            # Ini adalah LOGIKA BARU: jangan berhenti, tapi catat.
            log_system(f"Gagal mendapatkan gambar untuk event {event_id} (mungkin 404). Mengirim data tanpa gambar.", "WARN")
            # Payload "picture" sudah None

        # 5. Kirim API (dengan atau tanpa gambar)
        try:
            timeout = int(db.get_setting('request_timeout', '30'))
        except ValueError:
            timeout = 30
        
        r_api = requests.post(target_api, json=payload, timeout=timeout)
        
        if r_api.status_code in [200, 201]:
            # BERHASIL
            log_system(f"API event {event_id} (ke {target_api}) BERHASIL.", "INFO")
            db.update_event_api_status(event_id, 'success', retry_count)
        else:
            # GAGAL
            log_system(f"API event {event_id} (ke {target_api}) GAGAL (Status: {r_api.status_code}). Retry {retry_count + 1}/{api_fail_max_retry}", "WARN")
            db.update_event_api_status(event_id, 'failed', retry_count + 1)
            
            # --- NOTIFIKASI GAGAL (FORMAT BARU) ---
            if (retry_count + 1) >= api_fail_max_retry:
                log_system(f"Event {event_id} GAGAL PERMANEN. Mengirim notifikasi.", "ERROR")
                
                # Ambil data tambahan
                location = event.get('location') or '-'
                waktu_str = event.get('time', 'N/A') + " WIB"
                
                message = (
                    f"⚠️ API GAGAL TERKIRIM ⚠️\n\n"
                    f"Id Event: *{event_id}*\n"
                    f"Nama: *{event.get('name') or 'N/A'}*\n"
                    f"Device: *{event.get('deviceName')}*\n"
                    f"Lokasi: *{location}*\n"
                    f"Waktu: {waktu_str}\n\n"
                    f"Event ini gagal terkirim setelah {api_fail_max_retry} kali percobaan."
                )
                send_whatsapp_notification(message, 'api_fail_enabled')
                # --- AKHIR NOTIFIKASI ---

    except requests.exceptions.RequestException as e:
        # Gagal koneksi
        log_system(f"API event {event_id} GAGAL (Koneksi: {e}). Retry {retry_count + 1}/{api_fail_max_retry}", "WARN")
        db.update_event_api_status(event_id, 'failed', retry_count + 1)
        
        if (retry_count + 1) >= api_fail_max_retry:
            log_system(f"Event {event_id} GAGAL PERMANEN. Mengirim notifikasi.", "ERROR")
            
            # Ambil data tambahan
            location = event.get('location') or '-'
            waktu_str = event.get('time', 'N/A') + " WIB"

            message = (
                f"⚠️ API GAGAL TERKIRIM ⚠️\n\n"
                f"Id Event: *{event_id}*\n"
                f"Nama: *{event.get('name') or 'N/A'}*\n"
                f"Device: *{event.get('deviceName')}*\n"
                f"Lokasi: *{location}*\n"
                f"Waktu: {waktu_str}\n\n"
                f"Event ini gagal terkirim setelah {api_fail_max_retry} kali percobaan."
            )
            send_whatsapp_notification(message, 'api_fail_enabled')
            
    except Exception as e:
        log_system(f"API event {event_id} GAGAL (Error: {e}). Retry {retry_count + 1}/{api_fail_max_retry}", "ERROR")
        db.update_event_api_status(event_id, 'failed', retry_count + 1)
# --- AKHIR MODIFIKASI FUNGSI ---

# --- MAIN LOOP (WORKER BARU) ---
def main_worker():
    db.init_db()
    log_system("Memulai [Worker Service] - (Ping, Notifikasi, Antrean API, Cleanup)...")
        # --- TUGAS 0: Inisialisasi Device SDK ---
    try:
        from database import get_all_devices
        from sdk_service import HikvisionSDKService

        all_devices = db.get_all_devices()
        sdk_devices = [d for d in all_devices if d.get('type') == 'sdk']

        for device in sdk_devices:
            try:
                log_system(f"[SDK INIT] Menghubungkan ke device SDK: {device['name']} ({device['ip']})", "INFO")
                sdk_thread = threading.Thread(target=HikvisionSDKService(device).listen_events, daemon=True)
                sdk_thread.start()
            except Exception as e:
                log_system(f"Gagal inisialisasi SDK device {device['ip']}: {e}", "ERROR")
    except Exception as e:
        log_system(f"Kesalahan saat inisialisasi SDK device: {e}", "ERROR")

    last_ping_time = 0
    last_api_time = 0
    last_cleanup_time = time.time() - 86400 # Set ke kemarin agar langsung jalan

    try:
        while True:
            now = time.time()

            # --- TUGAS 1: CLEANUP (Setiap 24 jam) ---
            if (now - last_cleanup_time) > 86400:
                try:
                    days_str = db.get_setting('cleanup_days', default='60')
                    days_to_keep = int(days_str)
                    log_system(f"Menjalankan tugas cleanup harian (data > {days_to_keep} hari)...")
                    
                    deleted_log_folders = cleanup_old_logs(days_to_keep)
                    log_system(f"Cleanup log selesai. {deleted_log_folders} folder log lama dihapus.")
                    
                    deleted_rows, deleted_files = db.cleanup_old_events_and_images(days_to_keep)
                    log_system(f"Cleanup DB & gambar selesai. {deleted_rows} baris event dan {deleted_files} file gambar dihapus.")
                    
                    last_cleanup_time = now
                except Exception as e:
                    log_system(f"Error saat menjalankan cleanup harian: {e}", level="ERROR")

            # --- TUGAS 2: PING PERANGKAT (Sesuai interval) ---
            ping_interval = int(db.get_setting('worker_ping_interval', '10'))
            if (now - last_ping_time) > ping_interval:
                try:
                    ping_max_fail = int(db.get_setting('ping_max_fail', '5'))
                    suspend_seconds = int(db.get_setting('suspend_seconds', '300'))
                    all_devices = db.get_all_devices()
                    
                    if all_devices:
                        with ThreadPoolExecutor(max_workers=len(all_devices)) as executor:
                            # Kirim pengaturan sebagai argumen
                            futures = [executor.submit(check_device_status, device, ping_max_fail, suspend_seconds) for device in all_devices]
                            for future in futures:
                                future.result() # Menunggu semua selesai (meskipun tidak mengembalikan apa-apa)
                    
                    last_ping_time = now
                except Exception as e:
                    log_system(f"Error di loop PING: {e}", level="ERROR")

            # --- TUGAS 3: PROSES ANTREAN API (Sesuai interval) ---
            api_interval = int(db.get_setting('worker_api_interval', '15'))
            if (now - last_api_time) > api_interval:
                try:
                    api_fail_max_retry = int(db.get_setting('api_fail_max_retry', '5'))
                    # --- PENGATURAN BARU DARI DB ---
                    api_queue_limit = int(db.get_setting('api_queue_limit', '5'))
                    # -------------------------------
                    
                    events_to_send = db.get_pending_api_events(limit=api_queue_limit, max_retries=api_fail_max_retry) # <-- Menggunakan var
                    
                    if events_to_send:
                        log_system(f"Mengambil {len(events_to_send)} event dari antrean API untuk diproses...", "INFO")
                        with ThreadPoolExecutor(max_workers=5) as executor: # max_workers 5 tetap (aman)
                            futures = [executor.submit(process_api_event, event, api_fail_max_retry) for event in events_to_send]
                            for future in futures:
                                future.result()
                    
                    last_api_time = now
                except Exception as e:
                    log_system(f"Error di loop API: {e}", level="ERROR")
            
            # Jeda 1 detik sebelum loop berikutnya
            time.sleep(1) 
            
    except KeyboardInterrupt:
        log_system("Worker Service dihentikan oleh pengguna.")
    except Exception as e:
        log_system(f"FATAL ERROR [Worker Service]: {e}", level="ERROR")
        time.sleep(10) # Jeda 10 detik sebelum crash dan restart

# --- ENTRY POINT ---
if __name__ == "__main__":
    main_worker()