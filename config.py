# ---------- KONFIGURASI ----------
# Database
DB_HOST = "localhost"
DB_USER = "izlal"
DB_PASS = "1sampai8"
DB_NAME = "web_master"

# Pengaturan Polling (Kombinasi Responsif untuk Jam Sibuk)
POLL_INTERVAL = 2
BATCH_MAX_RESULTS = 20 # Diperkecil agar setiap batch cepat diproses

TIMEZONE = "+07:00"
IMG_DIR = "static/images"
LOG_DIR = "payload_logs"
SERVICE_LOG_DIR = "service_logs" 

# Pengaturan Ping & Suspend
PING_MAX_FAIL = 5
SUSPEND_SECONDS = 300

# Batas waktu dalam detik untuk menunggu respons dari perangkat
REQUEST_TIMEOUT = 30

# --- PEMETAAN EVENT HIKVISION ---
EVENT_MAP = {
    # Authentication Success
    (5, 75): "Face Recognized",

    # Authentication Failure
    (5, 80): "Face Recognition Failed",
    (5, 76): "Stranger Face Recognition Failed",
    (5, 18): "Card & Face Authentication Failed",
    (5, 43): "Card Not Registered",

    # Alarms & Status
    (5, 37): "Door Opened",
    (5, 41): "Door Held Open Too Long",
    (1, 10): "Door Not Closed",
    (1, 7): "Device Tamper Alarm",
}