import mysql.connector
from config import DB_HOST, DB_USER, DB_PASS, DB_NAME
from datetime import datetime, date, timedelta
import os

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
    # Migrasi: Tambahkan kolom yang mungkin belum ada
    try:
        c.execute("ALTER TABLE devices ADD COLUMN username VARCHAR(255) NULL")
        c.execute("ALTER TABLE devices ADD COLUMN password VARCHAR(255) NULL")
    except mysql.connector.Error: pass
    try:
        c.execute("ALTER TABLE devices ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
    except mysql.connector.Error: pass

    c.close()
    conn.close()

def get_device_by_ip(ip):
    """Mengambil detail satu perangkat berdasarkan IP."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM devices WHERE ip = %s", (ip,))
    device = c.fetchone()
    c.close()
    conn.close()
    return device

def get_all_devices():
    """Mengambil SEMUA PERANGKAT AKTIF untuk layanan sinkronisasi."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM devices WHERE is_active = TRUE ORDER BY name")
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

def get_all_devices_for_ui():
    """Mengambil SEMUA perangkat (aktif dan nonaktif) untuk ditampilkan di UI."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT * FROM devices ORDER BY is_active DESC, ip ASC")
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

def toggle_device_active_state(ip):
    """Mengubah status aktif/nonaktif sebuah perangkat."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE devices SET is_active = NOT is_active WHERE ip = %s", (ip,))
        return c.rowcount > 0
    finally:
        c.close()
        conn.close()

def get_all_unique_locations():
    """Mengambil semua lokasi unik dari tabel devices untuk filter dropdown."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    c.execute("SELECT DISTINCT location FROM devices WHERE location IS NOT NULL AND location != '' AND is_active = TRUE ORDER BY location")
    locations = c.fetchall()
    c.close()
    conn.close()
    return locations

def get_events(**filters):
    """Mengambil SEMUA event yang cocok dengan filter, TANPA PAGINASI."""
    conn = get_db()
    c = conn.cursor(dictionary=True)

    select_clause = """
        SELECT
            events.id, events.deviceName, devices.location, events.employeeId, events.name,
            DATE_FORMAT(STR_TO_DATE(events.date, '%Y-%m-%d'), '%d-%m-%Y') as date,
            events.time, events.eventDesc, events.pictureURL, events.localImagePath,
            events.syncType, events.apiStatus
    """
    base_sql = "FROM events JOIN devices ON events.deviceName = devices.name"
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

# --- FUNGSI BARU ---
def get_earliest_attendance_by_date(employee_ids, target_date, device_name):
    """
    Mengambil jam absen terawal untuk daftar employee_id pada tanggal DAN perangkat tertentu.
    Hanya mengambil event 'Face Recognized'.
    """
    if not employee_ids or not device_name:
        return {}
        
    conn = get_db()
    c = conn.cursor(dictionary=True)
    
    # Membuat placeholder untuk query IN (...)
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
    
    # Gabungkan parameter (device_name ditambahkan setelah target_date)
    params = [target_date, device_name] + employee_ids
    
    c.execute(query, tuple(params))
    results = c.fetchall()
    c.close()
    conn.close()
    
    # Ubah hasil query menjadi dictionary {employeeId: 'HH:MM:SS'}
    return {row['employeeId']: row['earliest_time'] for row in results}
# --------------------

def get_events_by_date(target_date, location=None, ip=None):
    """Mengambil event berdasarkan tanggal dengan urutan field yang rapi untuk API."""
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

def add_device(ip, name, location, target_api, username, password):
    """Menambahkan perangkat baru ke database."""
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
    """Memperbarui data perangkat di database."""
    conn = get_db()
    c = conn.cursor()
    # Hanya update password jika diisi, jika tidak, pertahankan yang lama
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
    """Menghapus perangkat dari database."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM devices WHERE ip=%s", (ip,))
    affected = c.rowcount
    c.close()
    conn.close()
    return affected > 0

def update_device_ping_status(ip: str, status: str):
    """Memperbarui status perangkat setelah ping (TANPA MENGUBAH lastSync)."""
    conn = get_db()
    c = conn.cursor()
    query = "UPDATE devices SET status=%s WHERE ip=%s"
    c.execute(query, (status, ip))
    c.close()
    conn.close()

def get_devices_status():
    """PERBAIKAN: Mengambil nama dan lokasi perangkat untuk polling AJAX di dasbor."""
    conn = get_db()
    c = conn.cursor(dictionary=True)
    # Menambahkan 'name' dan 'location' ke query
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

# --- FUNGSI BARU UNTUK CLEANUP ---

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
                # Path di DB: 'images/DEVICE_NAME/YYYY-MM-DD/file.jpg'
                # Path di disk: 'static/images/DEVICE_NAME/YYYY-MM-DD/file.jpg'
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
                # Coba hapus folder tanggal (e.g., .../2025-01-01)
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    
                    # Coba hapus folder device (e.g., .../DEVICE_NAME)
                    parent_dir = os.path.dirname(dir_path)
                    if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                        # Pastikan kita tidak menghapus 'static/images'
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