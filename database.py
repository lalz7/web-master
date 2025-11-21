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
            apiStatus VARCHAR(20) DEFAULT 'pending', 
            apiRetryCount INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(eventId, deviceName)
        )
    """)
    try:
        c.execute("ALTER TABLE events ADD COLUMN apiRetryCount INT DEFAULT 0")
    except mysql.connector.Error: pass 

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
    
    # Tabel Users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL
        )
    """)
    
    # Tabel Settings
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            setting_key VARCHAR(50) PRIMARY KEY,
            setting_value VARCHAR(255) NOT NULL
        )
    """)

    # Data Default User
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        default_pass = generate_password_hash('bukalah123')
        c.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", ('admin', default_pass))

    # Pengaturan Default
    default_settings = [
        ('cleanup_days', '60'),
        ('whatsapp_enabled', 'false'),
        ('whatsapp_target_number', ''),
        ('whatsapp_api_url', 'http://10.1.105.164:60001'),
        ('api_fail_enabled', 'false'),
        ('api_fail_max_retry', '5'),
        ('ping_max_fail', '5'),
        ('suspend_seconds', '300'),
        ('worker_ping_interval', '10'),
        ('worker_api_interval', '15'),
        ('poll_interval', '2'),
        ('event_sleep_delay', '1'),
        ('realtime_tolerance', '120'),
        ('request_timeout', '30'),
        ('api_queue_limit', '5'),
        ('event_batch_max', '100'),
        ('sync_download_retries', '5'),
        ('worker_download_retries', '2')
    ]
    
    for key, val in default_settings:
        c.execute("INSERT IGNORE INTO settings (setting_key, setting_value) VALUES (%s, %s)", (key, val))

    # Migrasi Kolom
    try:
        c.execute("ALTER TABLE devices ADD COLUMN username VARCHAR(255) NULL")
        c.execute("ALTER TABLE devices ADD COLUMN password VARCHAR(255) NULL")
    except mysql.connector.Error: pass
    try:
        c.execute("ALTER TABLE devices ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
    except mysql.connector.Error: pass
    
    c.close()
    conn.close()

# --- FUNGSI PENGATURAN ---

def get_setting(key, default=None):
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
    conn = get_db()
    c = conn.cursor()
    try:
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

# --- FUNGSI USER ---

def get_user_by_username(username):
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = c.fetchone()
    c.close()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = c.fetchone()
    c.close()
    conn.close()
    return user

def update_user_password(user_id, new_password_hash):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_password_hash, user_id))
        return True
    finally:
        c.close()
        conn.close()

# --- FUNGSI DEVICE ---

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

# --- FUNGSI WORKER ---

def get_pending_api_events(limit, max_retries):
    conn = get_db()
    c = conn.cursor(dictionary=True)
    query = """
        SELECT e.*, d.targetApi, d.username as deviceUsername, d.password as devicePassword,
               d.location as location 
        FROM events e
        JOIN devices d ON e.deviceName = d.name
        WHERE (e.apiStatus = 'pending' OR e.apiStatus = 'failed')
          AND e.apiRetryCount < %s
          AND d.targetApi IS NOT NULL 
          AND d.targetApi != ''
        ORDER BY e.id ASC 
        LIMIT %s
    """
    c.execute(query, (max_retries, limit))
    events = c.fetchall()
    c.close()
    conn.close()
    return events

def update_event_api_status(event_id, status, retry_count):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE events SET apiStatus=%s, apiRetryCount=%s WHERE id=%s", 
                  (status, retry_count, event_id))
    except Exception as e:
        print(f"Error updating event API status: {e}")
    finally:
        c.close()
        conn.close()

# --- FUNGSI EVENT & STATISTIK ---

def get_events(**filters):
    conn = get_db()
    c = conn.cursor(dictionary=True)
    select_clause = """
        SELECT
            events.id, events.deviceName, devices.location, events.employeeId, events.name,
            DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date,
            events.time, events.eventDesc, events.pictureURL, events.localImagePath,
            events.syncType, events.apiStatus
    """
    base_sql = " FROM events JOIN devices ON events.deviceName = devices.name"
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
    conn = get_db()
    c = conn.cursor(dictionary=True)
    query = """
        SELECT
            events.id, events.deviceName, devices.ip, devices.location, events.employeeId, events.name,
            DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date,
            events.time, events.eventDesc, events.pictureURL, events.localImagePath,
            events.syncType, events.apiStatus
        FROM events JOIN devices ON events.deviceName = devices.name
        WHERE events.id = %s
    """
    c.execute(query, (event_id,))
    event = c.fetchone()
    c.close()
    conn.close()
    return event

def get_earliest_attendance_by_date(employee_ids, target_date, device_name):
    if not employee_ids or not device_name: return {}
    conn = get_db()
    c = conn.cursor(dictionary=True)
    format_strings = ','.join(['%s'] * len(employee_ids))
    query = f"""
        SELECT employeeId, MIN(time) AS earliest_time
        FROM events
        WHERE date = %s AND deviceName = %s AND employeeId IN ({format_strings}) AND eventDesc = 'Face Recognized'
        GROUP BY employeeId
    """
    params = [target_date, device_name] + employee_ids
    c.execute(query, tuple(params))
    results = c.fetchall()
    c.close()
    conn.close()
    return {row['employeeId']: row['earliest_time'] for row in results}

def get_events_by_date(target_date, location=None, ip=None):
    conn = get_db()
    c = conn.cursor(dictionary=True)
    base_query = """
        SELECT
            events.id, DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date, events.time,
            events.name, events.employeeId, events.deviceName, devices.ip, devices.location,
            events.eventDesc, events.syncType, events.apiStatus, events.pictureURL, events.localImagePath
        FROM events JOIN devices ON events.deviceName = devices.name
        WHERE events.date = %s AND devices.is_active = TRUE
    """
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

def get_recent_events(limit=5):
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
    conn = get_db()
    c = conn.cursor(dictionary=True)
    cutoff_date = date.today() - timedelta(days=days_to_keep)
    cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
    deleted_files = 0
    deleted_rows = 0
    empty_dirs_to_check = set()

    try:
        c.execute("SELECT id, localImagePath FROM events WHERE STR_TO_DATE(date, '%Y-%m-%d') < %s AND localImagePath IS NOT NULL", (cutoff_date_str,))
        events_to_delete = c.fetchall()
        for event in events_to_delete:
            try:
                full_path = os.path.join("static", event['localImagePath'])
                if os.path.exists(full_path):
                    os.remove(full_path)
                    deleted_files += 1
                    empty_dirs_to_check.add(os.path.dirname(full_path))
            except Exception: pass
        
        c.execute("DELETE FROM events WHERE STR_TO_DATE(date, '%Y-%m-%d') < %s", (cutoff_date_str,))
        deleted_rows = c.rowcount
        
        for dir_path in empty_dirs_to_check:
            try:
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    parent_dir = os.path.dirname(dir_path)
                    if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                        safe_base_path = os.path.abspath(os.path.join("static", "images"))
                        if os.path.abspath(parent_dir).startswith(safe_base_path) and os.path.abspath(parent_dir) != safe_base_path:
                            os.rmdir(parent_dir)
            except Exception: pass

    except Exception as e:
        print(f"[CLEANUP_ERROR] Error saat cleanup database: {e}")
    finally:
        c.close()
        conn.close()
    return deleted_rows, deleted_files

# --- FUNGSI DASHBOARD ---
def get_dashboard_stats():
    """Mengambil statistik dashboard (Total, Online, Realtime Hari Ini, Catchup Hari Ini, Failed)."""
    conn = get_db()
    c = conn.cursor(dictionary=True)

    # 1. Status Perangkat
    c.execute("SELECT COUNT(*) as total_devices FROM devices WHERE is_active = TRUE")
    total_devices = c.fetchone()['total_devices']

    c.execute("SELECT COUNT(*) as online_devices FROM devices WHERE status = 'online' AND is_active = TRUE")
    online_devices = c.fetchone()['online_devices']

    # 2. Statistik Event Hari Ini
    today_str = date.today().strftime('%Y-%m-%d')
    c.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN apiStatus='failed' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN syncType='catch-up' THEN 1 ELSE 0 END) as catchup,
            SUM(CASE WHEN syncType='realtime' THEN 1 ELSE 0 END) as realtime
        FROM events 
        WHERE date = %s
    """, (today_str,))
    
    res = c.fetchone()
    
    c.close()
    conn.close()

    return {
        'total_devices': total_devices,
        'online_devices': online_devices,
        'events_today': res['total'],
        'failed_api': int(res['failed'] or 0),
        'catchup_today': int(res['catchup'] or 0),
        'realtime_today': int(res['realtime'] or 0) # <-- DATA BARU
    }

def get_weekly_analytics():
    """
    Mengambil statistik 'Face Recognized' per perangkat selama 7 hari terakhir.
    Output diformat khusus untuk Chart.js.
    """
    conn = get_db()
    c = conn.cursor(dictionary=True)
    
    # 1. Tentukan rentang tanggal (7 hari terakhir termasuk hari ini)
    today = date.today()
    dates = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    date_labels = [d.strftime('%d-%m-%Y') for d in dates] # Label Sumbu X
    date_keys = [d.strftime('%Y-%m-%d') for d in dates]   # Key untuk pencocokan DB
    
    # 2. Ambil daftar semua perangkat aktif (agar device yang 0 log tetap muncul)
    devices = get_all_devices()
    device_names = [d['name'] for d in devices]
    
    # 3. Siapkan struktur data awal (semua di-set ke 0)
    # Format: {'Device A': [0,0,0,0,0,0,0], 'Device B': ...}
    datasets = {name: [0] * 7 for name in device_names}
    
    # 4. Query Database (Hanya hitung yang SUKSES / Face Recognized)
    start_date_str = date_keys[0]
    end_date_str = date_keys[-1]
    
    # Query ini mengelompokkan jumlah log berdasarkan tanggal dan nama device
    query = """
        SELECT date, deviceName, COUNT(*) as total
        FROM events
        WHERE STR_TO_DATE(date, '%Y-%m-%d') BETWEEN STR_TO_DATE(%s, '%Y-%m-%d') AND STR_TO_DATE(%s, '%Y-%m-%d')
          AND eventDesc = 'Face Recognized'
        GROUP BY date, deviceName
    """
    
    try:
        c.execute(query, (start_date_str, end_date_str))
        rows = c.fetchall()
        
        # 5. Isi data ke struktur datasets
        for row in rows:
            d_name = row['deviceName']
            # Pastikan tanggal dari DB formatnya sesuai
            # Kadang DB simpan YYYY-MM-DD, kadang DD-MM-YYYY tergantung input,
            # Asumsi di sini kolom 'date' di DB konsisten YYYY-MM-DD sesuai insert script.
            # Jika tidak, kita perlu parsing. Kita coba matching string langsung dulu.
            
            row_date = row['date'] # Misal '2023-10-25'
            
            if d_name in datasets:
                try:
                    # Cari index tanggal ini ada di urutan ke berapa (0-6)
                    # Kita coba parsing tanggal dari row DB ke format YYYY-MM-DD untuk memastikan match
                    if '-' in row_date:
                        # Cek apakah format DD-MM-YYYY atau YYYY-MM-DD
                        parts = row_date.split('-')
                        if len(parts[0]) == 4: # YYYY-MM-DD
                            idx = date_keys.index(row_date)
                        else: # DD-MM-YYYY (format legacy)
                            # Ubah ke YYYY-MM-DD untuk dicocokkan
                            fmt_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
                            if fmt_date in date_keys:
                                idx = date_keys.index(fmt_date)
                            else:
                                continue
                        
                        datasets[d_name][idx] = row['total']
                except ValueError:
                    pass # Tanggal tidak masuk range 7 hari, skip
                    
    except Exception as e:
        print(f"Error analytics: {e}")
    finally:
        c.close()
        conn.close()

    return {
        'labels': date_labels,
        'datasets': datasets
    }

# --- Update fungsi ini di database.py ---

def get_hourly_analytics():
    """
    Mengambil RATA-RATA log per jam, dipisah antara Realtime vs Catch-up.
    """
    conn = get_db()
    c = conn.cursor(dictionary=True)
    
    # Struktur baru: Dictionary dengan 2 list
    hourly_data = {
        'realtime': [0] * 24,
        'catchup': [0] * 24
    }
    
    try:
        query = """
            SELECT 
                SUBSTRING(time, 1, 2) as hour_str, 
                SUM(CASE WHEN syncType='realtime' THEN 1 ELSE 0 END) as realtime,
                SUM(CASE WHEN syncType='catch-up' THEN 1 ELSE 0 END) as catchup
            FROM events
            WHERE eventDesc = 'Face Recognized'
            AND STR_TO_DATE(date, '%Y-%m-%d') >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY hour_str
        """
        c.execute(query)
        rows = c.fetchall()
        
        for row in rows:
            try:
                h = int(row['hour_str'])
                if 0 <= h < 24:
                    # Hitung rata-rata (bagi 7 hari)
                    hourly_data['realtime'][h] = round(row['realtime'] / 7, 1)
                    hourly_data['catchup'][h] = round(row['catchup'] / 7, 1)
            except (ValueError, TypeError):
                pass
                
    except Exception as e:
        print(f"Error hourly analytics: {e}")
    finally:
        c.close()
        conn.close()
        
    return hourly_data
    
# --- FUNGSI STATISTIK AI (LENGKAP) ---
def get_ai_context_stats():
    """
    Mengambil statistik event LENGKAP (Catch-up vs Realtime) untuk AI.
    """
    conn = get_db()
    c = conn.cursor(dictionary=True)
    stats = {}
    today = date.today()

    try:
        # Helper Query
        sql_template = """
            SELECT 
                COUNT(*) as total, 
                SUM(CASE WHEN apiStatus='failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN syncType='catch-up' THEN 1 ELSE 0 END) as catchup,
                SUM(CASE WHEN syncType='realtime' THEN 1 ELSE 0 END) as realtime
            FROM events 
        """

        # 1. KEMARIN (Yesterday)
        yesterday_str = (today - timedelta(days=1)).strftime('%Y-%m-%d')
        c.execute(sql_template + "WHERE date = %s", (yesterday_str,))
        res = c.fetchone()
        stats['yesterday'] = {
            'total': res['total'], 'failed': int(res['failed'] or 0),
            'catchup': int(res['catchup'] or 0), 'realtime': int(res['realtime'] or 0)
        }

        # 2. MINGGU INI (Start Monday)
        start_week_str = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
        c.execute(sql_template + "WHERE STR_TO_DATE(date, '%Y-%m-%d') >= STR_TO_DATE(%s, '%Y-%m-%d')", (start_week_str,))
        res = c.fetchone()
        stats['week'] = {
            'total': res['total'], 'failed': int(res['failed'] or 0),
            'catchup': int(res['catchup'] or 0), 'realtime': int(res['realtime'] or 0)
        }

        # 3. BULAN INI
        month_prefix = today.strftime('%Y-%m') + '%'
        c.execute(sql_template + "WHERE date LIKE %s", (month_prefix,))
        res = c.fetchone()
        stats['month'] = {
            'total': res['total'], 'failed': int(res['failed'] or 0),
            'catchup': int(res['catchup'] or 0), 'realtime': int(res['realtime'] or 0)
        }

    except Exception as e:
        print(f"Error getting AI stats: {e}")
        empty_stat = {'total': 0, 'failed': 0, 'catchup': 0, 'realtime': 0}
        stats = {'yesterday': empty_stat, 'week': empty_stat, 'month': empty_stat}
    finally:
        c.close()
        conn.close()
        
    return stats