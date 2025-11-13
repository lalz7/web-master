"""
sdk_service.py
--------------
Service untuk komunikasi langsung dengan perangkat Hikvision (non-ISAPI)
menggunakan HCNetSDK (.so) di Linux.

Terintegrasi dengan worker_service.py untuk logging dan database.py untuk penyimpanan event.
"""

import os
import time
import ctypes
import threading
from datetime import datetime

from worker_service import log_system
import database as db


# --------------------------------------------------------------
# 1️⃣ LOAD SDK HIKVISION (.so)
# --------------------------------------------------------------

# Path ke file library SDK
SDK_PATH = os.path.join(os.getcwd(), "sdk/libhcnetsdk.so")

try:
    hcnetsdk = ctypes.CDLL(SDK_PATH)
    # Inisialisasi SDK
    if hasattr(hcnetsdk, "NET_DVR_Init"):
        hcnetsdk.NET_DVR_Init()
        log_system(f"[SDK] HCNetSDK berhasil dimuat dari {SDK_PATH}", "INFO")
    else:
        log_system("[SDK] NET_DVR_Init tidak ditemukan di library SDK!", "ERROR")
except Exception as e:
    log_system(f"[SDK] Gagal memuat HCNetSDK: {e}", "ERROR")
    hcnetsdk = None


# --------------------------------------------------------------
# 2️⃣ KELAS UTAMA SDK SERVICE
# --------------------------------------------------------------

class HikvisionSDKService:
    """Listener untuk device Hikvision berbasis SDK (tanpa Web/ISAPI)."""

    def __init__(self, device):
        self.device = device
        self.ip = device.get("ip")
        self.port = int(device.get("port", 8000))
        self.username = device.get("username", "admin").encode("utf-8")
        self.password = device.get("password", "12345").encode("utf-8")
        self.login_id = -1
        self.active = True

    # ----------------------------------------------------------
    # LOGIN KE DEVICE
    # ----------------------------------------------------------
    def login(self):
        """Login ke device melalui SDK"""
        try:
            log_system(f"[SDK] Login ke device {self.device['name']} ({self.ip})...", "INFO")

            # Struktur login (disederhanakan untuk versi awal)
            # Biasanya butuh struct NET_DVR_DEVICEINFO_V30, tapi di sini dummy dulu
            # untuk verifikasi integrasi dan log.
            self.login_id = 1  # Placeholder sampai fungsi asli dipakai

            # TODO: Implementasikan login real kalau SDK binding sudah lengkap
            # contoh: hcnetsdk.NET_DVR_Login_V30(self.ip, self.port, self.username, self.password, ctypes.byref(device_info))

            return True
        except Exception as e:
            log_system(f"[SDK] Gagal login ke {self.ip}: {e}", "ERROR")
            return False

    # ----------------------------------------------------------
    # LISTEN EVENT
    # ----------------------------------------------------------
    def listen_events(self):
        """Thread utama untuk mendengarkan event dari SDK"""
        if hcnetsdk is None:
            log_system(f"[SDK] Library HCNetSDK belum dimuat, hentikan listener {self.ip}", "ERROR")
            return

        if not self.login():
            log_system(f"[SDK] Device {self.ip} gagal login, hentikan listener.", "ERROR")
            return

        log_system(f"[SDK] Mulai mendengarkan event dari {self.device['name']} ({self.ip})", "INFO")

        while self.active:
            try:
                # TODO: Ganti blok ini dengan callback SDK asli (NET_DVR_SetDVRMessageCallBack_V30)
                # Untuk sekarang simulasi event tiap 5 detik
                time.sleep(5)
                self._simulate_event()
            except Exception as e:
                log_system(f"[SDK] Error event loop {self.ip}: {e}", "ERROR")

    # ----------------------------------------------------------
    # SIMULASI EVENT (SAMPIL CALLBACK BELUM DIPAKAI)
    # ----------------------------------------------------------
    def _simulate_event(self):
        event_data = {
            "DeviceName": self.device["name"],
            "Card": "EMP001",
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Temperature": 36.5,
            "Picture": None
        }
        self.handle_event(event_data)

    # ----------------------------------------------------------
    # HANDLE EVENT DARI SDK
    # ----------------------------------------------------------
def handle_event(self, event_data):
    """Konversi event dari SDK ke format sistem dan kirim ke API lokal"""
    try:
        log_system(f"[SDK] Event dari {self.device['name']} - ID: {event_data['Card']}", "INFO")

        # Format payload identik dengan ISAPI
        payload = {
            "deviceName": event_data["DeviceName"],
            "employeeId": event_data["Card"],
            "date": event_data["Date"],
            "status": "pending",
            "targetApi": self.device.get("targetApi")
        }

        # Simpan ke DB (pakai sistem ISAPI yang sudah ada)
        db.insert_event(payload)
        log_system(f"[SDK] Event {event_data['Card']} disimpan ke database.", "INFO")

    except Exception as e:
        log_system(f"[SDK] Gagal proses event dari {self.ip}: {e}", "ERROR")


    # ----------------------------------------------------------
    # STOP SERVICE
    # ----------------------------------------------------------
    def stop(self):
        """Hentikan listener SDK"""
        self.active = False
        if self.login_id != -1 and hcnetsdk:
            try:
                hcnetsdk.NET_DVR_Logout_V30(self.login_id)
            except Exception:
                pass
        log_system(f"[SDK] Listener untuk {self.device['name']} dihentikan.", "INFO")
