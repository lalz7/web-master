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
AI_AVAILABLE = False

try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        
        # Menggunakan model "gemini-2.5-flash" (Ganti ke 1.5 jika 2.5 belum tersedia)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash", 
            generation_config={
                "temperature": 0.8, # Lebih kreatif agar luwes saat ngobrol santai
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 2048,
            }
        )
        AI_AVAILABLE = True
    else:
        print("[AI SERVICE] API Key belum diset.")
except Exception as e:
    print(f"[AI SERVICE] Gagal inisialisasi Gemini: {e}")
    AI_AVAILABLE = False


# ==========================================
# FUNGSI KONTEKS (DATA LENGKAP UNTUK AI)
# ==========================================

def get_system_context():
    """
    Mengambil SEMUA data sistem untuk konteks AI.
    Termasuk statistik hari ini, kemarin, mingguan, bulanan, status device, dan log.
    """
    try:
        # 1. Statistik Dashboard (Hari Ini)
        stats_today = db.get_dashboard_stats()
        
        # 2. Statistik Periode LENGKAP (Kemarin, Minggu Ini, Bulan Ini)
        stats_period = db.get_ai_context_stats()
        
        # 3. Status Perangkat Detail
        raw_devices = db.get_devices_status()
        devices_list = []
        for d in raw_devices:
            status_txt = "ONLINE" if d['status'] == 'online' else "OFFLINE"
            devices_list.append(f"- {d['name']} ({d['ip']}): {status_txt} | Loc: {d['location']}")
        devices_str = "\n".join(devices_list) if devices_list else "Tidak ada perangkat."

        # 4. Event Terakhir (Live Log)
        raw_events = db.get_recent_events(limit=5)
        events_list = []
        for e in raw_events:
            status_api = "Gagal" if e['apiStatus'] == 'failed' else "Sukses"
            events_list.append(f"- [{e['time']}] {e['name']} ({e['syncType']}) -> API {status_api}")
        events_str = "\n".join(events_list) if events_list else "Belum ada event hari ini."

        # --- MENYUSUN KONTEKS LENGKAP (TIDAK ADA YANG DI-CUT) ---
        context_prompt = f"""
        [DATA MONITORING SISTEM TERKINI]
        Waktu Server: {datetime.datetime.now().strftime('%A, %d-%m-%Y %H:%M:%S')}
        
        1. STATISTIK ABSENSI & SYNC (LENGKAP):
        - HARI INI: Total {stats_today.get('events_today', 0)} 
          (Realtime: {stats_today.get('realtime_today', 0)} | Susulan/Catch-up: {stats_today.get('catchup_today', 0)} | Gagal Kirim: {stats_today.get('failed_api', 0)})
        
        - KEMARIN: Total {stats_period['yesterday']['total']}
          (Realtime: {stats_period['yesterday']['realtime']} | Susulan: {stats_period['yesterday']['catchup']} | Gagal: {stats_period['yesterday']['failed']})
        
        - MINGGU INI (Senin-Skrg): Total {stats_period['week']['total']}
          (Realtime: {stats_period['week']['realtime']} | Susulan: {stats_period['week']['catchup']} | Gagal: {stats_period['week']['failed']})
        
        - BULAN INI: Total {stats_period['month']['total']}
          (Realtime: {stats_period['month']['realtime']} | Susulan: {stats_period['month']['catchup']} | Gagal: {stats_period['month']['failed']})

        2. STATUS PERANGKAT (Total {stats_today.get('total_devices', 0)} - Online: {stats_today.get('online_devices', 0)}):
        {devices_str}

        3. LIVE LOG TERAKHIR:
        {events_str}

        4. CONFIG:
        - WA Notif: {db.get_setting('whatsapp_enabled')}
        - Auto Cleanup: {db.get_setting('cleanup_days')} hari
        """
        return context_prompt

    except Exception as e:
        print(f"[AI SERVICE] Error mengambil konteks DB: {e}")
        return "Maaf, data sistem sedang tidak dapat dibaca (Database Error)."

# ==========================================
# FUNGSI UTAMA (HYBRID CHATBOT: ADMIN + TEMAN)
# ==========================================

def ask_gemini(user_question, chat_history=[]):
    """
    Menghandle chat dengan memori percakapan + Konteks Sistem + Kemampuan Ngobrol Umum.
    """
    if not AI_AVAILABLE:
        return {"success": False, "answer": "Layanan AI belum dikonfigurasi atau API Key salah."}

    try:
        # 1. Ambil Data Sistem Terbaru (Full Context)
        system_data = get_system_context()
        
        # 2. Format History Percakapan (Agar nyambung)
        formatted_history = []
        for msg in chat_history:
            role = "user" if msg.get("role") == "user" else "model"
            formatted_history.append({
                "role": role,
                "parts": [msg.get("text", "")]
            })

        # 3. Mulai Sesi Chat
        chat = model.start_chat(history=formatted_history)

        # 4. Prompt Hybrid (Instruksi Ganda)
        # Kita memberikan data sistem, TAPI memberi kebebasan untuk ngobrol santai.
        full_prompt = f"""
        Anda adalah Gemini, asisten cerdas yang terintegrasi dalam Dashboard Face Recognition.
        
        {system_data}
        
        [INSTRUKSI PENTING UNTUK RESPON]:
        1. **KATEGORI TEKNIS (Sistem/Absensi/Device)**:
           - Jika user bertanya tentang data, statistik, perangkat mati, atau log, JAWABLAH BERDASARKAN DATA DI ATAS.
           - Berikan analisis jika perlu (misal: "Ada lonjakan data susulan kemarin").
           
        2. **KATEGORI UMUM (Obrolan Santai)**:
           - Jika user menyapa, curhat, minta lelucon, koding, atau topik umum lain, JAWABLAH SECARA BEBAS & KREATIF layaknya AI Gemini biasa.
           - JANGAN memaksakan untuk membahas data absensi jika topiknya tidak nyambung.
           - Contoh: Kalau user tanya "Resep soto", jawab resep soto, jangan bahas log device.

        3. **GAYA BICARA**:
           - Gunakan Bahasa Indonesia yang santai, ramah, dan natural (tidak kaku).
           - Gunakan emoji sesekali agar lebih hidup.

        PERTANYAAN USER SAAT INI: "{user_question}"
        """
        
        # 5. Kirim ke Gemini
        response = chat.send_message(full_prompt)
        return {"success": True, "answer": response.text}

    except Exception as e:
        return {"success": False, "answer": f"Error koneksi AI: {str(e)}"}