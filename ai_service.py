import google.generativeai as genai
import database as db
import json
import datetime
import os
from dotenv import load_dotenv

# Memuat environment variables dari file .env
load_dotenv()

# ==========================================
# KONFIGURASI AI
# ==========================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

try:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Menggunakan model "gemini-2.5-flash" (Sesuai request)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash", 
        generation_config={
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 2048,
        }
    )
    AI_AVAILABLE = True
except Exception as e:
    print(f"[AI SERVICE] Gagal inisialisasi Gemini: {e}")
    AI_AVAILABLE = False


# ==========================================
# FUNGSI KONTEKS (DATA UNTUK AI)
# ==========================================

def get_system_context():
    try:
        # 1. Statistik Dashboard (Hari Ini)
        stats_today = db.get_dashboard_stats()
        
        # 2. Statistik Periode LENGKAP (Catchup/Realtime)
        stats_period = db.get_ai_context_stats()
        
        # 3. Status Perangkat
        raw_devices = db.get_devices_status()
        devices_list = []
        for d in raw_devices:
            status_txt = "ONLINE" if d['status'] == 'online' else "OFFLINE"
            devices_list.append(f"- {d['name']} ({d['ip']}): {status_txt} | Loc: {d['location']}")
        devices_str = "\n".join(devices_list) if devices_list else "Tidak ada perangkat."

        # 4. Event Terakhir
        raw_events = db.get_recent_events(limit=5)
        events_list = []
        for e in raw_events:
            status_api = "Gagal" if e['apiStatus'] == 'failed' else "Sukses"
            events_list.append(f"- [{e['time']}] {e['name']} ({e['syncType']}) -> API {status_api}")
        events_str = "\n".join(events_list) if events_list else "Belum ada event hari ini."

        # --- KONTEKS PROMPT DENGAN BREAKDOWN CATCH-UP ---
        context_prompt = f"""
        [DATA REAL-TIME SISTEM PENGGUNA]
        Waktu Server: {datetime.datetime.now().strftime('%A, %d-%m-%Y %H:%M:%S')}
        
        1. STATISTIK ABSENSI & TIPE SYNC:
        - HARI INI: Total {stats_today.get('events_today', 0)} (Catch-up: {stats_today.get('catchup_today', 0)} | API Gagal: {stats_today.get('failed_api', 0)})
        
        - KEMARIN: Total {stats_period['yesterday']['total']}
          (Realtime: {stats_period['yesterday']['realtime']} | Catch-up: {stats_period['yesterday']['catchup']} | API Gagal: {stats_period['yesterday']['failed']})
        
        - MINGGU INI (Senin-Skrg): Total {stats_period['week']['total']}
          (Realtime: {stats_period['week']['realtime']} | Catch-up: {stats_period['week']['catchup']} | API Gagal: {stats_period['week']['failed']})
        
        - BULAN INI: Total {stats_period['month']['total']}
          (Realtime: {stats_period['month']['realtime']} | Catch-up: {stats_period['month']['catchup']} | API Gagal: {stats_period['month']['failed']})

        2. STATUS PERANGKAT (Total {stats_today.get('total_devices', 0)} tapi hidden di dashboard):
        - Online: {stats_today.get('online_devices', 0)}
        - Detail:
        {devices_str}

        3. LIVE LOG:
        {events_str}

        4. CONFIG:
        - WA Notif: {db.get_setting('whatsapp_enabled')}
        - Auto Cleanup: {db.get_setting('cleanup_days')} hari
        """
        return context_prompt

    except Exception as e:
        print(f"[AI SERVICE] Error mengambil konteks DB: {e}")
        return "Maaf, data sistem sedang tidak dapat dibaca."

# ==========================================
# FUNGSI UTAMA (CHATBOT)
# ==========================================

def ask_gemini(user_question):
    if not AI_AVAILABLE:
        return {"success": False, "answer": "Layanan AI belum dikonfigurasi."}

    try:
        system_data = get_system_context()
        full_prompt = f"""
        Bertindaklah sebagai Gemini (Asisten AI Sistem Face Recognition).
        Gunakan data real-time berikut untuk menjawab pertanyaan user:
        
        {system_data}

        TUGAS KAMU:
        1. Jawab dengan gaya santai, natural, dan informatif.
        2. Jika ditanya tipe event (catch-up vs realtime), gunakan data statistik di atas.
           (Penjelasan: 'Realtime' = absen saat online. 'Catch-up' = data susulan saat offline/delay).
        3. Jika ada API gagal, sarankan cek koneksi.

        PERTANYAAN: "{user_question}"
        JAWABAN:
        """
        response = model.generate_content(full_prompt)
        return {"success": True, "answer": response.text}
    except Exception as e:
        return {"success": False, "answer": f"Error koneksi AI: {str(e)}"}