# ---------- KONFIGURASI ----------
# Database
DB_HOST = "localhost"
DB_USER = "izlal"
DB_PASS = "1sampai8"
DB_NAME = "web_master"

# Pengaturan Global
TIMEZONE = "+07:00"
IMG_DIR = "static/images"

# Pengaturan Direktori Log
EVENT_LOG_DIR = "event_logs"       # Untuk log bersih (event yang diproses)
SERVICE_LOG_DIR = "service_logs" # Untuk semua event mentah yang diterima

# Pengaturan Catch-up (Masih digunakan oleh sync_service.py)
CATCH_UP_CHUNK_MINUTES = 10
BIG_CATCHUP_THRESHOLD_SECONDS = 3600 # 1 jam

# --- PEMETAAN EVENT HIKVISION (LENGKAP) ---
EVENT_MAP = {
    # == Otentikasi Berhasil (Major: 5) ==
    (5, 75): "Face Recognized",
    (5, 1): "Legal Card Pass",
    (5, 17): "Card and Face Authentication Pass",
    (5, 23): "Fingerprint Pass",
    (5, 25): "Fingerprint and Face Authentication Pass",
    (5, 26): "Fingerprint and Card Authentication Pass",
    (5, 28): "Fingerprint, Card, and Face Authentication Pass",
    (5, 33): "Employee No and Fingerprint Authentication Pass",
    (5, 34): "Employee No and Face Authentication Pass",
    (5, 35): "Employee No and Card Authentication Pass",
    (5, 53): "Multi-Factor Authentication Pass",

    # == Otentikasi Gagal (Major: 5) ==
    (5, 80): "Face recognition failed",
    (5, 76): "Stranger face recognition failed",
    (5, 2): "Card Invalid Time Period",
    (5, 3): "Card No Right",
    (5, 4): "Anti-Passback Fail",
    (5, 5): "Card Not Found / Unregistered Card",
    (5, 18): "Card and Face Authentication Failed",
    (5, 24): "Fingerprint Authentication Failed",
    (5, 43): "Card not registered",

    # == Panggilan & Duress (Major: 5) ==
    (5, 48): "Duress Alarm",
    (5, 12): "Call Center",

    # == Event Pintu (Major: 5) ==
    (5, 37): "Door opened",
    (5, 38): "Door closed",
    (5, 39): "Door Exception (Opened Under Duress)",
    (5, 40): "Door Button Pressed to Open",
    (5, 41): "Door held open too long (Door Open Timeout)",
    
    # == Alarm Umum (Major: 1) ==
    (1, 1): "Tamper Alarm for Door Contact",
    (1, 7): "Device tamper alarm",
    (1, 10): "Door not closed",
}