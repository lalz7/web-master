import google.generativeai as genai
import database as db
import json
import datetime
import os

# ==========================================
# KONFIGURASI AI
# ==========================================

# 1. Masukkan API Key Gemini Anda di sini
# (Dapatkan gratis di https://aistudio.google.com/)
GEMINI_API_KEY = "AIzaSyC39kH30iHIWsXF7fDVlD5JxiGIpA8gSXg" 

try:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Konfigurasi Model (Flash lebih cepat dan ringan)
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={
            "temperature": 0.8,     # Sedikit lebih kreatif (0.8) agar lebih luwes
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
# FUNGSI KONTEKS (MATA AI)
# ==========================================

def get_system_context():
    """
    Fungsi ini mengumpulkan seluruh data 'Page' dan 'Database'
    untuk diberikan ke AI sebagai pengetahuan tambahan.
    """
    try:
        # 1. Ambil Data Statistik
        stats = db.get_dashboard_stats()
        
        # 2. Ambil Status Perangkat
        raw_devices = db.get_devices_status()
        devices_list = []
        for d in raw_devices:
            # Format yang mudah dibaca
            devices_list.append(f"- {d['name']} ({d['ip']}): Status {d['status'].upper()} (Lokasi: {d['location']})")
        devices_str = "\n".join(devices_list) if devices_list else "Tidak ada perangkat terdaftar."

        # 3. Ambil Event Terakhir
        raw_events = db.get_recent_events(limit=5)
        events_list = []
        for e in raw_events:
            status_api = "Gagal" if e['apiStatus'] == 'failed' else "Sukses"
            events_list.append(f"- [{e['time']}] {e['name']} di {e['deviceName']} ({e['syncType']}, API: {status_api})")
        events_str = "\n".join(events_list) if events_list else "Belum ada event hari ini."

        # 4. Ambil Pengaturan
        wa_enabled = db.get_setting('whatsapp_enabled')
        cleanup_days = db.get_setting('cleanup_days')

        # --- RAKIT KONTEKS MENJADI TEKS ---
        context_prompt = f"""
        [DATA REAL-TIME SISTEM PENGGUNA]
        Waktu Server: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        
        1. STATISTIK UMUM:
        - Total Perangkat: {stats.get('total_devices', 0)}
        - Perangkat Online: {stats.get('online_devices', 0)}
        - Total Event Hari Ini: {stats.get('events_today', 0)}
        - API Error Hari Ini: {stats.get('failed_api', 0)}

        2. DAFTAR PERANGKAT:
        {devices_str}

        3. LOG AKTIVITAS TERAKHIR:
        {events_str}

        4. KONFIGURASI:
        - Notifikasi WA: {'Aktif' if wa_enabled == 'true' else 'Nonaktif'}
        - Auto Cleanup: {cleanup_days} hari
        """
        return context_prompt

    except Exception as e:
        print(f"[AI SERVICE] Error mengambil konteks DB: {e}")
        return "Data sistem tidak tersedia saat ini karena error database."


# ==========================================
# FUNGSI UTAMA (OTAK AI)
# ==========================================

def ask_gemini(user_question):
    """
    Fungsi yang dipanggil oleh app.py untuk memproses pertanyaan user.
    """
    if not AI_AVAILABLE:
        return {
            "success": False, 
            "answer": "Layanan AI belum dikonfigurasi. Pastikan API KEY sudah dipasang di ai_service.py."
        }

    try:
        # 1. Dapatkan data sistem terbaru
        system_data = get_system_context()

        # 2. Susun Instruksi (System Prompt)
        # PERUBAHAN DI SINI: Kita minta dia menjadi Gemini biasa yang membantu
        full_prompt = f"""
        Bertindaklah sebagai Gemini, asisten AI yang cerdas, ramah, dan sangat membantu.
        
        Kamu memiliki akses ke data real-time dari "Sistem Face Recognition" milik pengguna.
        Gunakan data berikut sebagai konteks jawabanmu (tapi jangan menyebutkan "berdasarkan data di atas" secara kaku, bicaralah secara natural):
        
        {system_data}

        TUGAS KAMU:
        1. Jawab pertanyaan pengguna dengan gaya bahasa yang santai, natural, dan informatif.
        2. Jika pengguna bertanya tentang sistem (seperti "siapa yang absen?", "ada error ga?"), gunakan data di atas untuk menjawabnya secara akurat.
        3. Jika pengguna bertanya hal umum (seperti "apa kabar?", "ide makan siang"), jawablah layaknya Gemini biasa.
        4. Jika ada masalah pada sistem (misal banyak error), berikan saran yang menenangkan dan solutif.

        PERTANYAAN PENGGUNA:
        "{user_question}"

        JAWABAN KAMU:
        """

        # 3. Kirim ke Google Gemini
        response = model.generate_content(full_prompt)
        
        return {
            "success": True,
            "answer": response.text
        }

    except Exception as e:
        return {
            "success": False,
            "answer": f"Waduh, maaf ya, sepertinya ada gangguan koneksi ke otak AI saya: {str(e)}"
        }