import mysql.connector
from config import DB_HOST, DB_USER, DB_PASS, DB_NAME
from datetime import datetime, date, timedelta
import os
from werkzeug.security import generate_password_hash, check_password_hash

def get_db():
    """Membuat koneksi ke database."""
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME, autocommit=True
    )

def init_db():
    """Membuat dan memodifikasi tabel jika belum ada."""
    conn = get_db()
    c = conn.cursor()
    # Tabel Events
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id BIGINT AUTO_INCREMENT PRIMARY KEY, deviceName VARCHAR(255),
            eventId BIGINT, employeeId INT NULL, name VARCHAR(255), date VARCHAR(10),
            time VARCHAR(8), eventDesc VARCHAR(255), pictureURL VARCHAR(255),
            localImagePath VARCHAR(255) NULL, syncType VARCHAR(20) DEFAULT 'realtime',
            apiStatus VARCHAR(20) DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(eventId, deviceName)
        )
    """)
    # Tabel Devices
    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            ip VARCHAR(50) PRIMARY KEY,
            name VARCHAR(255),
            location VARCHAR(255),
            targetApi VARCHAR(255) NULL,
            username VARCHAR(255) NULL,
            password VARCHAR(255) NULL,
            status VARCHAR(20) DEFAULT 'offline',
            lastSync DATETIME NULL,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    
    # --- TABEL BARU: USERS ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL
        )
    """)
    
    # --- TABEL BARU: SETTINGS ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            setting_key VARCHAR(50) PRIMARY KEY,
            setting_value VARCHAR(255) NOT NULL
        )
    """)

    # --- Inisialisasi Data Default (Hanya jika kosong) ---
    
    # Buat admin user default jika tabel user baru saja dibuat
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        default_pass = generate_password_hash('bukalah123')
        c.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", ('admin', default_pass))

    # Buat pengaturan default jika tabel settings baru saja dibuat
    c.execute("SELECT COUNT(*) FROM settings")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('cleanup_days', '60'))
        
        # --- TAMBAHAN PENGATURAN WA ---
        # (Akan diabaikan jika 'cleanup_days' sudah ada)
        c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('whatsapp_enabled', 'false'))
        c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('whatsapp_target_number', ''))
        c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('whatsapp_api_url', 'http://10.1.105.164:60001'))
        # -------------------------------

    # Migrasi (tetap ada untuk instalasi lama)
    try:
        c.execute("ALTER TABLE devices ADD COLUMN username VARCHAR(255) NULL")
        c.execute("ALTER TABLE devices ADD COLUMN password VARCHAR(255) NULL")
    except mysql.connector.Error: pass
    try:
        c.execute("ALTER TABLE devices ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
    except mysql.connector.Error: pass
    
    # Migrasi: Tambahkan pengaturan WA jika tabel settings sudah ada tapi pengaturan WA belum
    c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('whatsapp_enabled', 'false'))
    c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('whatsapp_target_number', ''))
    c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", ('whatsapp_api_url', 'http://10.1.105.164:60001'))


    c.close()
    conn.close()

# --- FUNGSI BARU: PENGATURAN (SETTINGS) ---

def get_setting(key, default=None):
    """Mengambil nilai pengaturan dari database."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT setting_value FROM settings WHERE setting_key = %s", (key,))
    result = c.fetchone()
    c.close()
    conn.close()
    if result:
        return result['setting_value']
    return default

def update_setting(key, value):
    """Memperbarui nilai pengaturan di database."""
    conn = get_db()
    c = conn.cursor()
    try:
        # Coba update dulu, jika gagal (tidak ada), baru insert
        c.execute("UPDATE settings SET setting_value = %s WHERE setting_key = %s", (value, key))
        if c.rowcount == 0:
            c.execute("INSERT INTO settings (setting_key, setting_value) VALUES (%s, %s)", (key, value))
        return True
    except Exception as e:
        print(f"Error updating setting: {e}")
        return False
    finally:
        c.close()
        conn.close()

# --- FUNGSI BARU: AUTENTIKASI ---

def get_user_by_username(username):
    """Mengambil data user berdasarkan username."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = c.fetchone()
    c.close()
    conn.close()
    return user

def get_user_by_id(user_id):
    """Mengambil data user berdasarkan ID."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()
    c.close()
    conn.close()
    return user

def update_user_password(user_id, new_password_hash):
    """Memperbarui password hash user."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_password_hash, user_id))
        return True
    except Exception as e:
        print(f"Error updating password: {e}")
        return False
    finally:
        c.close()
        conn.close()

# --- FUNGSI DEVICE (TETAP SAMA) ---

def get_device_by_ip(ip):
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM devices WHERE ip = %s", (ip,))
    device = c.fetchone()
    c.close()
    conn.close()
    return device

def get_all_devices():
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM devices WHERE is_active = TRUE ORDER BY name")
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

def get_all_devices_for_ui():
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM devices ORDER BY is_active DESC, ip ASC")
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

def toggle_device_active_state(ip):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE devices SET is_active = NOT is_active WHERE ip = %s", (ip,))
        return c.rowcount > 0
    finally:
        c.close()
        conn.close()

def get_all_unique_locations():
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT DISTINCT location FROM devices WHERE location IS NOT NULL AND location != '' AND is_active = TRUE ORDER BY location")
    locations = c.fetchall()
    c.close()
    conn.close()
    return locations

def add_device(ip, name, location, target_api, username, password):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO devices (ip, name, location, targetApi, username, password, status) VALUES (%s, %s, %s, %s, %s, %s, 'new')",
                  (ip, name, location, target_api, username, password))
        return True, "Perangkat berhasil ditambahkan."
    except mysql.connector.IntegrityError:
        return False, "Perangkat dengan IP tersebut sudah ada."
    finally:
        c.close()
        conn.close()

def update_device(original_ip, name, location, target_api, username, password):
    conn = get_db()
    c = conn.cursor()
    if password:
        query = "UPDATE devices SET name=%s, location=%s, targetApi=%s, username=%s, password=%s WHERE ip=%s"
        values = (name, location, target_api, username, password, original_ip)
    else:
        query = "UPDATE devices SET name=%s, location=%s, targetApi=%s, username=%s WHERE ip=%s"
        values = (name, location, target_api, username, original_ip)
    c.execute(query, values)
    affected = c.rowcount
    c.close()
    conn.close()
    return affected > 0

def delete_device(ip):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM devices WHERE ip=%s", (ip,))
    affected = c.rowcount
    c.close()
    conn.close()
    return affected > 0

def update_device_ping_status(ip: str, status: str):
    conn = get_db()
    c = conn.cursor()
    query = "UPDATE devices SET status=%s WHERE ip=%s"
    c.execute(query, (status, ip))
    c.close()
    conn.close()

def get_devices_status():
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT ip, name, location, status, lastSync FROM devices WHERE is_active = TRUE ORDER BY name")
    rows = c.fetchall()
    c.close()
    conn.close()
    result = []
    for row in rows:
        last_sync = row['lastSync']
        row['lastSync'] = last_sync.strftime('%d-%m-%Y %H:%M:%S') if last_sync else 'Belum pernah'
        result.append(row)
    return result

# --- FUNGSI EVENT & STATISTIK ---

def get_events(**filters):
    """Mengambil SEMUA event yang cocok dengan filter, TANPA PAGINASI."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    
    # PERBAIKAN SQL (SPASI)
    select_clause = "SELECT events.id, events.deviceName, devices.location, events.employeeId, events.name, DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date, events.time, events.eventDesc, events.pictureURL, events.localImagePath, events.syncType, events.apiStatus"
    base_sql = " FROM events JOIN devices ON events.deviceName = devices.name" # <-- SPASI SUDAH ADA DI SINI
    
    where_clauses, values = [], []
    where_clauses.append("devices.is_active = TRUE")

    if filters.get('device'):
        where_clauses.append("events.deviceName = %s")
        values.append(filters['device'])

    if filters.get('location'):
        where_clauses.append("devices.location = %s")
        values.append(filters['location'])

    if filters.get('start_date'):
        where_clauses.append("STR_TO_DATE(events.date, '%Y-%m-%d') >= %s")
        values.append(filters['start_date'])

    if filters.get('end_date'):
        where_clauses.append("STR_TO_DATE(events.date, '%Y-%m-%d') <= %s")
        values.append(filters['end_date'])

    if where_clauses:
        base_sql += " WHERE " + " AND ".join(where_clauses)

    data_sql = select_clause + base_sql + " ORDER BY events.id DESC"
    c.execute(data_sql, tuple(values))
    events = c.fetchall()

    c.close()
    conn.close()
    return events

def get_event_by_id(event_id):
    """Mengambil SEMUA detail event (termasuk lokasi) untuk pop-up."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    query = "SELECT events.id, events.deviceName, devices.ip, devices.location, events.employeeId, events.name, DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date, events.time, events.eventDesc, events.pictureURL, events.localImagePath, events.syncType, events.apiStatus FROM events JOIN devices ON events.deviceName = devices.name WHERE events.id = %s"
    c.execute(query, (event_id,))
    event = c.fetchone()
    c.close()
    conn.close()
    return event

def get_earliest_attendance_by_date(employee_ids, target_date, device_name):
    """
    Mengambil jam absen terawal untuk daftar employee_id pada tanggal DAN perangkat tertentu.
    Hanya mengambil event 'Face Recognized'.
    """
    if not employee_ids or not device_name:
        return {}
        
    conn = get_db()
    c = conn.cursor(dictionary=True)
    
    format_strings = ','.join(['%s'] * len(employee_ids))
    
    query = f"""
        SELECT employeeId, MIN(time) AS earliest_time
        FROM events
        WHERE 
            date = %s 
            AND deviceName = %s
            AND employeeId IN ({format_strings})
            AND eventDesc = 'Face Recognized'
        GROUP BY employeeId
    """
    
    params = [target_date, device_name] + employee_ids
    
    c.execute(query, tuple(params))
    results = c.fetchall()
    c.close()
    conn.close()
    
    return {row['employeeId']: row['earliest_time'] for row in results}

def get_events_by_date(target_date, location=None, ip=None):
    """Mengambil event berdasarkan tanggal dengan urutan field yang rapi untuk API."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    base_query = "SELECT events.id, DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date, events.time, events.name, events.employeeId, events.deviceName, devices.ip, devices.location, events.eventDesc, events.syncType, events.apiStatus, events.pictureURL, events.localImagePath FROM events JOIN devices ON events.deviceName = devices.name WHERE events.date = %s AND devices.is_active = TRUE"
    values = [target_date]
    if location:
        base_query += " AND devices.location = %s"
        values.append(location)
    if ip:
        base_query += " AND devices.ip = %s"
        values.append(ip)
    base_query += " ORDER BY events.id DESC"
    c.execute(base_query, tuple(values))
    events = c.fetchall()
    c.close()
    conn.close()
    return events

# --- FUNGSI-FUNGSI YANG HILANG SEBELUMNYA ---

def get_dashboard_stats():
    """Mengambil data statistik ringkas untuk halaman dashboard."""
    conn = get_db()
    c = conn.cursor(dictionary=True)

    c.execute("SELECT COUNT(*) as total_devices FROM devices WHERE is_active = TRUE")
    total_devices = c.fetchone()['total_devices']

    c.execute("SELECT COUNT(*) as online_devices FROM devices WHERE status = 'online' AND is_active = TRUE")
    online_devices = c.fetchone()['online_devices']

    today_str = date.today().strftime('%Y-%m-%d')

    filters_for_today = {'start_date': today_str, 'end_date': today_str}
    events_today_list = get_events(**filters_for_today) 

    events_today_count = len(events_today_list)
    failed_api_count = sum(1 for event in events_today_list if event.get('apiStatus') == 'failed')

    c.close()
    conn.close()

    return {
        'total_devices': total_devices,
        'online_devices': online_devices,
        'events_today': events_today_count,
        'failed_api': failed_api_count
    }

def get_recent_events(limit=5):
    """Mengambil data event terbaru untuk ditampilkan di dashboard."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    query = """
        SELECT
            events.id, events.deviceName, devices.location, events.name, events.apiStatus,
            events.date, events.time, events.eventDesc, events.syncType
        FROM events
        JOIN devices ON events.deviceName = devices.name
        WHERE devices.is_active = TRUE
        ORDER BY events.id DESC
        LIMIT %s
    """
    c.execute(query, (limit,))
    events = c.fetchall()
    c.close()
    conn.close()
    return events

def cleanup_old_events_and_images(days_to_keep):
    """
    Menghapus event dari DB dan file gambar terkait yang lebih tua dari days_to_keep.
    Juga mencoba menghapus folder tanggal/perangkat yang kosong.
    """
    conn = get_db()
    c = conn.cursor(dictionary=True)
    
    cutoff_date = date.today() - timedelta(days=days_to_keep)
    cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
    
    deleted_files = 0
    deleted_rows = 0
    empty_dirs_to_check = set()

    try:
        # 1. Ambil path gambar yang akan dihapus
        c.execute(
            "SELECT id, localImagePath FROM events WHERE STR_TO_DATE(date, '%Y-%m-%d') < %s AND localImagePath IS NOT NULL", 
            (cutoff_date_str,)
        )
        events_to_delete = c.fetchall()
        
        # 2. Hapus file gambar dari disk
        for event in events_to_delete:
            try:
                full_path = os.path.join("static", event['localImagePath'])
                if os.path.exists(full_path):
                    os.remove(full_path)
                    deleted_files += 1
                    # Kumpulkan folder induk (folder tanggal) untuk dicek nanti
                    empty_dirs_to_check.add(os.path.dirname(full_path))
            except Exception as e:
                print(f"[CLEANUP_WARN] Gagal menghapus file {event['localImagePath']}: {e}")
        
        # 3. Hapus event dari database
        c.execute(
            "DELETE FROM events WHERE STR_TO_DATE(date, '%Y-%m-%d') < %s",
            (cutoff_date_str,)
        )
        deleted_rows = c.rowcount
        
        # 4. Coba hapus folder-folder kosong
        for dir_path in empty_dirs_to_check:
            try:
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    parent_dir = os.path.dirname(dir_path)
                    if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                        safe_base_path = os.path.abspath(os.path.join("static", "images"))
                        if os.path.abspath(parent_dir).startswith(safe_base_path) and os.path.abspath(parent_dir) != safe_base_path:
                            os.rmdir(parent_dir)
            except Exception as e:
                print(f"[CLEANUP_WARN] Gagal menghapus folder kosong {dir_path}: {e}")

    except Exception as e:
        print(f"[CLEANUP_ERROR] Error saat cleanup database: {e}")
    finally:
        c.close()
        conn.close()
        
    return deleted_rows, deleted_files