import os
import platform
import datetime
import json
import requests
import base64
import time # <-- Diperlukan untuk jeda
from requests.auth import HTTPDigestAuth
from flask import (Flask, render_template, request, redirect, url_for, 
                   flash, jsonify, Response, g)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
import database as db
import ai_service
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ganti-dengan-kunci-rahasia-yang-sangat-acak-dan-panjang')
app.config['JSON_SORT_KEYS'] = False
cors = CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- Konfigurasi Login dan Model User ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Anda harus login untuk mengakses halaman ini.'
login_manager.login_message_category = 'warning'

class User(UserMixin):
    """Model User baru yang mengambil data dari database."""
    def __init__(self, user_id, username, password_hash):
        self.id = user_id
        self.username = username
        self.password_hash = password_hash
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

@login_manager.user_loader
def load_user(user_id):
    """Callback untuk me-reload user dari session."""
    user_data = db.get_user_by_id(user_id)
    if user_data:
        return User(user_data['id'], user_data['username'], user_data['password_hash'])
    return None
# ----------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = 'remember' in request.form
        
        user_data = db.get_user_by_username(username)
        
        if user_data:
            user = User(user_data['id'], user_data['username'], user_data['password_hash'])
            if user.check_password(password):
                login_user(user, remember=remember)
                return redirect(request.args.get('next') or url_for('index'))
        
        flash('Username atau password salah.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- HALAMAN PENGATURAN (DIMODIFIKASI) ---
@app.route('/settings', methods=['GET'])
@login_required
def settings():
    # Ambil semua pengaturan untuk dikirim ke template
    settings_data = {
        'cleanup_days': db.get_setting('cleanup_days', default='60'),
        'whatsapp_enabled': db.get_setting('whatsapp_enabled', default='false'),
        'whatsapp_target_number': db.get_setting('whatsapp_target_number', default=''),
        'whatsapp_api_url': db.get_setting('whatsapp_api_url', default='http://10.1.105.164:60001'),
        'api_fail_enabled': db.get_setting('api_fail_enabled', default='false'),
        'api_fail_max_retry': db.get_setting('api_fail_max_retry', default='5'),
        
        # Pengaturan Worker
        'ping_max_fail': db.get_setting('ping_max_fail', default='5'),
        'suspend_seconds': db.get_setting('suspend_seconds', default='300'),
        'worker_ping_interval': db.get_setting('worker_ping_interval', default='10'),
        'worker_api_interval': db.get_setting('worker_api_interval', default='15'),
        
        # 8 Pengaturan Baru
        'poll_interval': db.get_setting('poll_interval', default='2'),
        'event_sleep_delay': db.get_setting('event_sleep_delay', default='1'),
        'realtime_tolerance': db.get_setting('realtime_tolerance', default='120'),
        'request_timeout': db.get_setting('request_timeout', default='30'),
        'api_queue_limit': db.get_setting('api_queue_limit', default='5'),
        'event_batch_max': db.get_setting('event_batch_max', default='100'),
        'sync_download_retries': db.get_setting('sync_download_retries', default='5'),
        'worker_download_retries': db.get_setting('worker_download_retries', default='2'),
    }
    # Kirim SEMUA pengaturan sebagai satu variabel 'settings'
    return render_template('settings.html', settings=settings_data)

@app.route('/settings/password', methods=['POST'])
@login_required
def save_password_settings():
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not current_user.check_password(current_password):
        flash('Password Anda saat ini salah.', 'danger')
        return redirect(url_for('settings'))

    if not new_password or len(new_password) < 6:
        flash('Password baru harus minimal 6 karakter.', 'danger')
        return redirect(url_for('settings'))

    if new_password != confirm_password:
        flash('Password baru dan konfirmasi tidak cocok.', 'danger')
        return redirect(url_for('settings'))

    new_password_hash = generate_password_hash(new_password)
    if db.update_user_password(current_user.id, new_password_hash):
        flash('Password berhasil diperbarui.', 'success')
    else:
        flash('Gagal memperbarui password.', 'danger')

    return redirect(url_for('settings'))

@app.route('/settings/cleanup', methods=['POST'])
@login_required
def save_cleanup_settings():
    try:
        days = int(request.form.get('cleanup_days'))
        if days < 7:
            flash('Batas hari cleanup minimal adalah 7 hari.', 'danger')
        else:
            if db.update_setting('cleanup_days', str(days)):
                flash('Pengaturan cleanup berhasil disimpan.', 'success')
            else:
                flash('Gagal menyimpan pengaturan cleanup.', 'danger')
    except ValueError:
        flash('Nilai hari tidak valid.', 'danger')
    
    return redirect(url_for('settings'))

# --- RUTE NOTIFIKASI (DIMODIFIKASI) ---
@app.route('/settings/notifications', methods=['POST'])
@login_required
def save_notification_settings():
    enabled = 'true' if request.form.get('whatsapp_enabled') else 'false'
    number_string = request.form.get('whatsapp_target_number', '').strip()
    api_url = request.form.get('whatsapp_api_url', '').strip()
    
    # Ambil data baru
    api_fail_enabled = 'true' if request.form.get('api_fail_enabled') else 'false'
    api_fail_max_retry = request.form.get('api_fail_max_retry', '5')

    all_valid = True
    
    if enabled == 'true':
        if not number_string:
            flash('Nomor WhatsApp Tujuan tidak boleh kosong jika notifikasi diaktifkan.', 'danger')
            all_valid = False
        elif not api_url:
            flash('URL API WhatsApp tidak boleh kosong jika notifikasi diaktifkan.', 'danger')
            all_valid = False
        else:
            # Validasi setiap nomor yang dipisahkan koma
            numbers = number_string.split(',')
            for num in numbers:
                num = num.strip()
                if not num.startswith('62') or not num[2:].isdigit():
                    flash(f'Format nomor "{num}" salah. Harus diawali 62 dan hanya angka.', 'danger')
                    all_valid = False
                    break # Hentikan validasi jika satu saja sudah salah
    
    if all_valid:
        db.update_setting('whatsapp_enabled', enabled)
        db.update_setting('whatsapp_target_number', number_string)
        db.update_setting('whatsapp_api_url', api_url)
        
        # Simpan data baru
        db.update_setting('api_fail_enabled', api_fail_enabled)
        db.update_setting('api_fail_max_retry', api_fail_max_retry)

        flash('Pengaturan notifikasi berhasil disimpan.', 'success')
    
    return redirect(url_for('settings'))
# ---------------------------------------------

# --- RUTE SIMPAN SINKRONISASI (DIMODIFIKASI) ---
@app.route('/settings/sync', methods=['POST'])
@login_required
def save_sync_settings():
    try:
        db.update_setting('ping_max_fail', str(int(request.form.get('ping_max_fail', 5))))
        db.update_setting('suspend_seconds', str(int(request.form.get('suspend_seconds', 300))))
        db.update_setting('worker_ping_interval', str(int(request.form.get('worker_ping_interval', 10))))
        db.update_setting('worker_api_interval', str(int(request.form.get('worker_api_interval', 15))))
        
        # Simpan 2 data baru
        db.update_setting('event_sleep_delay', str(float(request.form.get('event_sleep_delay', 1))))
        db.update_setting('realtime_tolerance', str(int(request.form.get('realtime_tolerance', 120))))

        flash('Pengaturan sinkronisasi berhasil disimpan.', 'success')
    except ValueError:
        flash('Semua nilai sinkronisasi harus berupa angka yang valid.', 'danger')
    except Exception as e:
        flash(f'Gagal menyimpan pengaturan: {e}', 'danger')
        
    return redirect(url_for('settings'))
# -------------------------------------------

# --- RUTE BARU UNTUK SIMPAN LANJUTAN ---
@app.route('/settings/advanced', methods=['POST'])
@login_required
def save_advanced_settings():
    try:
        db.update_setting('poll_interval', str(int(request.form.get('poll_interval', 2))))
        db.update_setting('request_timeout', str(int(request.form.get('request_timeout', 30))))
        db.update_setting('api_queue_limit', str(int(request.form.get('api_queue_limit', 5))))
        db.update_setting('event_batch_max', str(int(request.form.get('event_batch_max', 100))))
        db.update_setting('sync_download_retries', str(int(request.form.get('sync_download_retries', 5))))
        db.update_setting('worker_download_retries', str(int(request.form.get('worker_download_retries', 2))))

        flash('Pengaturan lanjutan berhasil disimpan.', 'success')
    except ValueError:
        flash('Semua nilai pengaturan lanjutan harus berupa angka yang valid.', 'danger')
    except Exception as e:
        flash(f'Gagal menyimpan pengaturan: {e}', 'danger')
        
    return redirect(url_for('settings'))
# -------------------------------------------
# --- Update route index di app.py ---

@app.route('/')
@login_required
def index():
    stats = db.get_dashboard_stats()
    
    devices_status = db.get_devices_status() or []
    event_limit = len(devices_status) 
    recent_events = db.get_recent_events(limit=event_limit) or []
    
    # Data Grafik 1: Tren Mingguan
    chart_data = db.get_weekly_analytics()
    
    # Data Grafik 2: Jam Sibuk (BARU)
    busy_data = db.get_hourly_analytics()
    
    return render_template('dashboard.html',
                           stats=stats,
                           recent_events=recent_events,
                           devices_status=devices_status,
                           chart_data=chart_data,
                           busy_data=busy_data) # <-- Jangan lupa ditambahkan


@app.route('/events')
@login_required
def events():
    filters = {k: v for k, v in request.args.items() if v}
    show_all = 'show' in filters
    if not show_all and 'start_date' not in filters and 'end_date' not in filters:
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        filters['start_date'] = today_str
        filters['end_date'] = today_str
    if 'show' in filters:
        del filters['show']
    events_data = db.get_events(**filters)
    all_devices = db.get_all_devices()
    all_locations = db.get_all_unique_locations()
    return render_template('events.html',
                           events=events_data,
                           filters=filters,
                           all_devices=all_devices,
                           all_locations=all_locations)

@app.route('/devices')
@login_required
def devices():
    all_devices = db.get_all_devices_for_ui()
    return render_template('devices.html', devices=all_devices)

@app.route('/devices/<string:ip>/users')
@login_required
def manage_device_users(ip):
    device = db.get_device_by_ip(ip)
    if not device:
        flash('Perangkat tidak ditemukan.', 'danger')
        return redirect(url_for('devices'))
    return render_template('users.html', device=device)

# --- ROUTE CRUD & API (TETAP SAMA) ---
@app.route('/devices/add', methods=['POST'])
@login_required
def add_device():
    ip, name, location, target_api, username, password = (request.form.get(key) for key in ['ip', 'name', 'location', 'targetApi', 'username', 'password'])
    if not ip or not name or not username or not password:
        flash('IP, Nama, Username, dan Password wajib diisi.', 'danger')
    else:
        success, message = db.add_device(ip, name, location, target_api, username, password)
        flash(message, 'success' if success else 'danger')
    return redirect(url_for('devices'))

@app.route('/devices/update', methods=['POST'])
@login_required
def update_device():
    ip, name, location, target_api, username, password = (request.form.get(key) for key in ['ip', 'name', 'location', 'targetApi', 'username', 'password'])
    if not ip or not name or not username:
        flash('IP, Nama, dan Username wajib diisi.', 'danger')
    else:
        if db.update_device(ip, name, location, target_api, username, password):
            flash('Perangkat berhasil diperbarui.', 'success')
        else:
            flash('Gagal memperbarui perangkat atau tidak ada perubahan data.', 'warning')
    return redirect(url_for('devices'))

@app.route('/devices/delete', methods=['POST'])
@login_required
def delete_device():
    ip = request.form.get('ip')
    if db.delete_device(ip):
        flash('Perangkat berhasil dihapus.', 'success')
    else:
        flash('Gagal menghapus perangkat. IP tidak ditemukan.', 'danger')
    return redirect(url_for('devices'))

@app.route('/devices/toggle_active/<string:ip>', methods=['POST'])
@login_required
def toggle_active(ip):
    if db.toggle_device_active_state(ip):
        flash('Status perangkat berhasil diubah.', 'success')
    else:
        flash('Gagal mengubah status perangkat.', 'danger')
    return redirect(url_for('devices'))

@app.route('/api/event/<int:event_id>')
@login_required
def api_get_event(event_id):
    event = db.get_event_by_id(event_id)
    if event:
        event['imageUrl'] = url_for('static', filename=event['localImagePath'], _external=True) if event.get('localImagePath') else event.get('pictureURL')
        if 'localImagePath' in event: del event['localImagePath']
        if 'pictureURL' in event: del event['pictureURL']
        return jsonify(event)
    return jsonify({'error': 'Event not found'}), 404

def ping_device(ip):
    param = "-n 1 -w 1000" if platform.system().lower() == "windows" else "-c 1 -W 1"
    cmd = f"ping {param} {ip}"
    redirect_out = " > NUL 2>&1" if platform.system().lower()=="windows" else " > /dev/null 2>&1"
    return os.system(cmd + redirect_out) == 0

@app.route('/api/ping/<string:ip>')
@login_required
def api_ping(ip):
    status = 'online' if ping_device(ip) else 'offline'
    db.update_device_ping_status(ip, status)
    return jsonify({'status': status})

@app.route('/api/devices_status')
@login_required
def api_devices_status():
    devices_status = db.get_devices_status()
    return jsonify(devices_status)

# --- API MANAJEMEN PENGGUNA (api_update_user_info DIROMBAK) ---
@app.route('/api/devices/<string:ip>/users')
@login_required
def api_get_device_users(ip):
    device = db.get_device_by_ip(ip)
    if not device or not device.get('username') or not device.get('password'):
        return jsonify({'error': 'Kredensial perangkat tidak ditemukan.'}), 404
    url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Search?format=json"
    auth = HTTPDigestAuth(device['username'], device['password'])
    all_users = []
    search_id = "random_search_id_" + str(datetime.datetime.now().timestamp())
    position = 0
    max_results = 30
    try:
        while True:
            payload = {"UserInfoSearchCond": {"searchID": search_id, "searchResultPosition": position, "maxResults": max_results}}
            response = requests.post(url, json=payload, auth=auth, timeout=15)
            if response.status_code != 200:
                data = response.json()
                return jsonify({'error': f"Error dari perangkat: {data.get('errorMsg', 'Unknown')}"}), 502
            data = response.json().get('UserInfoSearch', {})
            users_batch = data.get('UserInfo', [])
            if users_batch:
                all_users.extend(users_batch)
            matches = data.get('numOfMatches', 0)
            total_matches = data.get('totalMatches', 0)
            position += matches
            if not users_batch or position >= total_matches:
                break
        if all_users:
            employee_ids = [int(u['employeeNo']) for u in all_users if u.get('employeeNo', '').isdigit()]
            device_name = device.get('name') 
            attendance_data = db.get_earliest_attendance_by_date(employee_ids, datetime.date.today().strftime('%Y-%m-%d'), device_name)
            for user in all_users:
                emp_id = int(user.get('employeeNo')) if user.get('employeeNo','').isdigit() else None
                user['attendance_time'] = attendance_data.get(emp_id)
        return jsonify(all_users)
    except Exception as e:
        return jsonify({'error': f"Gagal terhubung atau memproses: {e}"}), 500

# --- FUNGSI UPDATE USER (LOGIKA KONDISIONAL BARU) ---
@app.route('/api/devices/<string:ip>/users/<string:employee_no>/update', methods=['POST'])
@login_required
def api_update_user_info(ip, employee_no):
    device = db.get_device_by_ip(ip)
    if not device: return jsonify({'error': 'Perangkat tidak ditemukan'}), 404
    
    auth = HTTPDigestAuth(device['username'], device['password'])
    data = request.form
    name = data.get('name')
    gender = data.get('gender')
    start_time_str = data.get('startTime')
    end_time_str = data.get('endTime')
    
    if not all([name, start_time_str, end_time_str]):
        return jsonify({'error': 'Nama dan Waktu Mulai/Akhir wajib diisi.'}), 400

    try:
        formatted_start_time = start_time_str + ":00"
        formatted_end_time = end_time_str + ":00"
    except Exception:
        return jsonify({'error': 'Format waktu tidak valid.'}), 400

    # Cek apakah ada file foto baru di request
    photo_is_present = 'photo' in request.files and request.files['photo'].filename != ''

    # --- LOGIKA KONDISIONAL ---
    if photo_is_present:
        
        # --- ALUR A: HAPUS TOTAL DAN BUAT ULANG (karena ada foto baru) ---
        
        # 1. HAPUS PENGGUNA (UserInfo/Delete)
        delete_user_url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Delete?format=json"
        delete_user_payload = {"UserInfoDelCond": {"employeeNoList": [{"employeeNo": employee_no}]}}
        try:
            delete_res = requests.put(delete_user_url, json=delete_user_payload, auth=auth, timeout=10)
            if delete_res.status_code not in [200, 204]:
                delete_res_data = delete_res.json() if delete_res.content else {'statusString': delete_res.text}
                return jsonify({'error': f"Gagal menghapus pengguna lama (sebelum upload baru): {delete_res_data.get('statusString') or 'Error tidak diketahui'}"}), 502
        except Exception as e:
            return jsonify({'error': f"Error koneksi saat menghapus pengguna lama: {e}"}), 500

        # Beri jeda 1 detik agar perangkat bisa memproses penghapusan
        time.sleep(1)

        # 2. BUAT ULANG PENGGUNA (UserInfo/Record)
        user_payload = {
            "UserInfo": {
                "employeeNo": employee_no, "name": name, "userType": "normal",
                "gender": gender if gender in ['male', 'female'] else 'unknown',
                "Valid": {"enable": True, "beginTime": formatted_start_time, "endTime": formatted_end_time},
                "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]
            }
        }
        user_url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Record?format=json"
        try:
            user_res = requests.post(user_url, json=user_payload, auth=auth, timeout=10)
            if user_res.status_code not in [200, 204]:
                user_res_data = user_res.json() if user_res.content else {'statusString': user_res.text}
                return jsonify({'error': f"Gagal membuat ulang data pengguna: {user_res_data.get('statusString') or 'Error tidak diketahui'}"}), 502
        except Exception as e:
            return jsonify({'error': f"Error koneksi saat membuat ulang pengguna: {e}"}), 500

        # 3. UPLOAD FOTO BARU (FDLib/FaceDataRecord)
        photo_file = request.files['photo']
        photo_data = photo_file.read()
        photo_url = f"http://{device['ip']}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json"
        json_payload = {"faceLibType": "blackFD", "FDID": "1", "employeeNo": employee_no, "FPID": employee_no}
        files = {
            'FaceDataRecord': (None, json.dumps(json_payload), 'application/json'), 
            'FaceImage': (photo_file.filename, photo_data, photo_file.mimetype)
        }
        try:
            photo_res = requests.post(photo_url, files=files, auth=auth, timeout=20)
            if photo_res.status_code not in [200, 204]:
                return jsonify({'success': True, 'warning': 'Pengguna diperbarui, tapi unggah foto baru gagal.'})
        except Exception as e:
            return jsonify({'success': True, 'warning': f'Pengguna diperbarui, tapi koneksi unggah foto gagal: {e}'})

        return jsonify({'success': True, 'message': 'Pengguna dan foto berhasil diperbarui.'})

    else:
        
        # --- ALUR B: HANYA UPDATE INFO (karena tidak ada foto baru) ---
        user_payload = {
            "UserInfo": {
                "employeeNo": employee_no, 
                "name": name,
                "gender": gender if gender in ['male', 'female'] else 'unknown',
                "Valid": {"enable": True, "beginTime": formatted_start_time, "endTime": formatted_end_time}
            }
        }
        user_url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Modify?format=json"
        
        try:
            user_res = requests.put(user_url, json=user_payload, auth=auth, timeout=10) 
            if user_res.status_code not in [200, 204]:
                user_res_data = user_res.json() if user_res.content else {'statusString': user_res.text}
                return jsonify({'error': f"Gagal update data pengguna: {user_res_data.get('statusString') or 'Error tidak diketahui'}"}), 502
        except Exception as e:
            return jsonify({'error': f"Error koneksi saat update pengguna: {e}"}), 500
        
        return jsonify({'success': True, 'message': 'Data pengguna berhasil diperbarui (foto tidak diubah).'})
# --- AKHIR FUNGSI UPDATE USER ---


@app.route('/api/devices/<string:ip>/users/add', methods=['POST'])
@login_required
def api_add_user(ip):
    device = db.get_device_by_ip(ip)
    if not device: return jsonify({'error': 'Perangkat tidak ditemukan'}), 404
    
    data = request.form
    employee_no = data.get('employeeNo')
    name = data.get('name')
    gender = data.get('gender')
    start_time_str = data.get('startTime')
    end_time_str = data.get('endTime')
    
    if not all([employee_no, name, start_time_str, end_time_str]):
        return jsonify({'error': 'Semua field teks wajib diisi.'}), 400

    auth = HTTPDigestAuth(device['username'], device['password'])
    
    try:
        formatted_start_time = start_time_str + ":00"
        formatted_end_time = end_time_str + ":00"
    except Exception:
        return jsonify({'error': 'Format waktu tidak valid.'}), 400
    
    user_payload = {
        "UserInfo": {
            "employeeNo": employee_no, "name": name, "userType": "normal",
            "gender": gender if gender in ['male', 'female'] else 'unknown',
            "Valid": {"enable": True, "beginTime": formatted_start_time, "endTime": formatted_end_time},
            "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]
        }
    }
    user_url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Record?format=json"
    
    try:
        user_res = requests.post(user_url, json=user_payload, auth=auth, timeout=10)
        if user_res.status_code not in [200, 204]:
            user_res_data = user_res.json() if user_res.content else {'statusString': user_res.text}
            return jsonify({'error': f"Gagal membuat pengguna: {user_res_data.get('statusString') or 'Error tidak diketahui'}"}), 502
    except Exception as e:
        return jsonify({'error': f"Error koneksi saat membuat pengguna: {e}"}), 500

    if 'photo' in request.files and request.files['photo'].filename != '':
        photo_file = request.files['photo']
        photo_data = photo_file.read()
        
        photo_url = f"http://{device['ip']}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json"
        json_payload = {"faceLibType": "blackFD", "FDID": "1", "employeeNo": employee_no, "FPID": employee_no}
        
        # --- PERBAIKAN BUG MIME TYPE ---
        # Menggunakan photo_file.mimetype dinamis, bukan 'image/jpeg'
        files = {
            'FaceDataRecord': (None, json.dumps(json_payload), 'application/json'), 
            'FaceImage': (photo_file.filename, photo_data, photo_file.mimetype)
        }
        # --- AKHIR PERBAIKAN ---
        
        try:
            # Menambah user baru menggunakan POST
            photo_res = requests.post(photo_url, files=files, auth=auth, timeout=20)
            if photo_res.status_code not in [200, 204]:
                return jsonify({'success': True, 'warning': 'Pengguna dibuat, tapi unggah foto gagal.'})
        except Exception:
            return jsonify({'success': True, 'warning': 'Pengguna dibuat, tapi koneksi unggah foto gagal.'})

    return jsonify({'success': True, 'message': 'Pengguna berhasil ditambahkan.'})

@app.route('/api/devices/<string:ip>/users/<string:employee_no>/delete', methods=['DELETE'])
@login_required
def api_delete_user(ip, employee_no):
    device = db.get_device_by_ip(ip)
    if not device: return jsonify({'error': 'Perangkat tidak ditemukan'}), 404
    url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Delete?format=json"
    auth = HTTPDigestAuth(device['username'], device['password'])
    payload = {"UserInfoDelCond": {"employeeNoList": [{"employeeNo": employee_no}]}}
    try:
        response = requests.put(url, json=payload, auth=auth, timeout=10)
        if response.status_code in [200, 204]:
            return jsonify({'success': True})
        response_data = response.json() if response.content else {'errorMsg': response.text}
        return jsonify({'error': f"Gagal hapus: {response_data.get('errorMsg') or 'Respons tidak terduga.'}"}), 502
    except Exception as e:
        return jsonify({'error': f"Error koneksi: {e}"}), 500
    
# --- API BULK UPDATE EXPIRY (BARU) ---
@app.route('/api/devices/<string:ip>/users/bulk_update_expiry', methods=['POST'])
@login_required
def api_bulk_update_expiry(ip):
    device = db.get_device_by_ip(ip)
    if not device: return jsonify({'error': 'Perangkat tidak ditemukan'}), 404

    auth = HTTPDigestAuth(device['username'], device['password'])
    req_data = request.json
    
    users_list = req_data.get('users', [])
    start_time_str = req_data.get('startTime')
    end_time_str = req_data.get('endTime')

    if not users_list or not start_time_str or not end_time_str:
        return jsonify({'error': 'Data pengguna atau waktu tidak lengkap.'}), 400

    try:
        formatted_start_time = start_time_str + ":00"
        formatted_end_time = end_time_str + ":00"
    except Exception:
        return jsonify({'error': 'Format waktu tidak valid.'}), 400

    success_count = 0
    fail_count = 0
    errors = []

    # URL untuk Modify UserInfo
    url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Modify?format=json"

    # Loop setiap user dan kirim request ke perangkat
    for user in users_list:
        emp_no = user.get('employeeNo')
        name = user.get('name')
        
        # Payload harus menyertakan nama dan tipe user agar tidak error/data hilang
        payload = {
            "UserInfo": {
                "employeeNo": emp_no,
                "name": name,
                "userType": "normal", # Default normal
                "Valid": {
                    "enable": True,
                    "beginTime": formatted_start_time,
                    "endTime": formatted_end_time
                }
            }
        }

        try:
            # Menggunakan PUT untuk update
            res = requests.put(url, json=payload, auth=auth, timeout=5)
            if res.status_code in [200, 204]:
                # Cek status string dari body response jika ada
                if res.content:
                    res_json = res.json()
                    if res_json.get('statusCode') == 1 or res_json.get('statusString') == 'OK':
                         success_count += 1
                    else:
                         fail_count += 1
                         errors.append(f"User {emp_no}: {res_json.get('subStatusCode')}")
                else:
                    success_count += 1
            else:
                fail_count += 1
                errors.append(f"User {emp_no}: HTTP {res.status_code}")
        
        except Exception as e:
            fail_count += 1
            errors.append(f"User {emp_no}: {str(e)}")
        
        # Beri jeda sedikit agar perangkat tidak overload
        time.sleep(0.1)

    message = f"Berhasil update {success_count} pengguna."
    if fail_count > 0:
        message += f" Gagal: {fail_count}. Cek log console untuk detail."
        print(f"[BULK UPDATE ERROR] {errors}")

    return jsonify({
        'success': True, 
        'message': message,
        'stats': {'success': success_count, 'fail': fail_count}
    })

@app.route('/api/logs/by-date/<string:date_string>')
def api_get_logs_by_date(date_string):
    try:
        datetime.datetime.strptime(date_string, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Format tanggal tidak valid.'}), 400
    events = db.get_events_by_date(date_string, location=request.args.get('location'), ip=request.args.get('ip'))
    for event in events:
        event['imageUrl'] = url_for('static', filename=event['localImagePath']) if event.get('localImagePath') else event.get('pictureURL')
    return Response(json.dumps(events, indent=4), mimetype='application/json')


@app.route('/api/ask-ai', methods=['POST'])
def api_ask_ai():
    """Endpoint untuk Chatbot AI di Frontend."""
    data = request.json
    question = data.get('question')
    
    if not question:
        return jsonify({'error': 'Pertanyaan kosong'}), 400
    
    # Memanggil logic cerdas dari file ai_service.py
    # yang sudah memiliki akses ke konteks DB lengkap.
    result = ai_service.ask_gemini(question)
    
    if result["success"]:
        return jsonify({'answer': result["answer"]})
    else:
        return jsonify({'error': result["answer"]}), 500
    
# --- PENGGANTI FUNGSI api_hris_create_user (Logika Update) ---
# (Pastikan 'datetime', 'base64', 'json', 'requests', 'HTTPDigestAuth', 'jsonify', 'time' sudah diimpor di atas)

@app.route('/create', methods=['POST'])
def api_hris_create_user():
    """
    Endpoint untuk menerima data user dari HRIS (FTDevice.php).
    Logika ini secara otomatis menangani UPDATE (Delete-then-Re-add).
    """
    try:
        data = request.form
        
        # 1. Ambil data dari form (sesuai FTDevice.php & FTUser.php)
        employee_no = data.get('id')
        name = data.get('name')
        photo_base64 = data.get('fp') # Foto dalam format base64
        
        # Ambil data perangkat
        device_ip = data.get('ip')
        device_user = data.get('username')
        device_pass = data.get('password')
        
        # Ambil dan format waktu
        start_time_php = data.get('validStart') # Format: Y-m-d H:i:s
        end_time_php = data.get('validEnd')     # Format: Y-m-d H:i:s

        # Validasi data penting
        if not all([employee_no, name, device_ip, device_user, device_pass, start_time_php, end_time_php]):
            return jsonify({'error': 'Data tidak lengkap (id, name, ip, user, pass, waktu wajib diisi)'}), 400

        # 2. Konversi format waktu
        try:
            dt_start = datetime.datetime.strptime(start_time_php, '%Y-%m-%d %H:%M:%S')
            dt_end = datetime.datetime.strptime(end_time_php, '%Y-%m-%d %H:%M:%S')
            formatted_start_time = dt_start.strftime('%Y-%m-%dT%H:%M:%S')
            formatted_end_time = dt_end.strftime('%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return jsonify({'error': 'Format waktu salah. Harap gunakan Y-m-d H:i:s'}), 400

        # 3. Siapkan koneksi ke perangkat
        auth = HTTPDigestAuth(device_user, device_pass)
        
        # 4. (MODIFIKASI) HAPUS USER LAMA (Abaikan jika gagal)
        # Ini adalah langkah 'brute force' untuk memastikan data lama terhapus
        try:
            delete_user_url = f"http://{device_ip}/ISAPI/AccessControl/UserInfo/Delete?format=json"
            delete_user_payload = {"UserInfoDelCond": {"employeeNoList": [{"employeeNo": employee_no}]}}
            # Kirim perintah hapus. Kita tidak peduli hasilnya sukses atau gagal.
            requests.put(delete_user_url, json=delete_user_payload, auth=auth, timeout=10)
            # Beri jeda 1 detik agar perangkat selesai memproses penghapusan
            time.sleep(1) 
        except Exception:
            # Abaikan semua error koneksi atau timeout pada saat hapus
            pass 

        # 5. BUAT USER BARU (Info Teks)
        user_payload = {
            "UserInfo": {
                "employeeNo": employee_no, "name": name, "userType": "normal",
                "gender": "unknown",
                "Valid": {"enable": True, "beginTime": formatted_start_time, "endTime": formatted_end_time},
                "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]
            }
        }
        user_url = f"http://{device_ip}/ISAPI/AccessControl/UserInfo/Record?format=json"
        
        user_res = requests.post(user_url, json=user_payload, auth=auth, timeout=10)
        
        if user_res.status_code not in [200, 204]:
            try:
                user_res_data = user_res.json()
            except:
                 user_res_data = {'statusString': user_res.text}
            # Jika errornya adalah 'userAlreadyExist' (karena delete gagal), kirim pesan jelas
            if "userAlreadyExist" in str(user_res_data):
                 return jsonify({'error': f"Gagal membuat pengguna: User {employee_no} sudah ada dan gagal dihapus sebelumnya."}), 500
            return jsonify({'error': f"Gagal membuat pengguna: {user_res_data.get('statusString') or 'Error tidak diketahui'}"}), 502

        # 6. Upload Foto (jika ada)
        if photo_base64:
            try:
                photo_data = base64.b64decode(photo_base64)
                photo_url = f"http://{device_ip}/ISAPI/Intelligent/FDLib/FaceDataRecord?format=json"
                json_payload = {"faceLibType": "blackFD", "FDID": "1", "employeeNo": employee_no, "FPID": employee_no}
                files = {
                    'FaceDataRecord': (None, json.dumps(json_payload), 'application/json'), 
                    'FaceImage': ('photo_from_hris.jpg', photo_data, 'image/jpeg')
                }
                photo_res = requests.post(photo_url, files=files, auth=auth, timeout=20)
                if photo_res.status_code not in [200, 204]:
                    return jsonify({'success': True, 'message': 'Data teks pengguna diperbarui, tapi unggah foto baru gagal.'})
            
            except base64.binascii.Error:
                return jsonify({'success': True, 'message': 'Data teks pengguna diperbarui, tapi format base64 foto salah.'})
            except Exception as e:
                return jsonify({'success': True, 'message': f'Data teks pengguna diperbarui, tapi koneksi unggah foto gagal: {e}'})

        # 7. Sukses Penuh
        return jsonify({'success': True, 'message': 'Pengguna berhasil diperbarui/dibuat.'}), 200

    except requests.exceptions.RequestException as e:
        return jsonify({'error': f"Error koneksi ke perangkat: {e}"}), 500
    except Exception as e:
        return jsonify({'error': f'Terjadi error internal di server Python: {e}'}), 500

# --- AKHIR FUNGSI api_hris_create_user ---
# --- TAMBAHAN API EKSTERNAL MANAJEMEN PERANGKAT (Tanpa API Key) ---

@app.route('/api/devices', methods=['GET'])
def api_ext_get_all_devices():
    """ [READ] Mengambil daftar semua perangkat yang terdaftar di database. """
    try:
        devices = db.get_all_devices_for_ui()
        return jsonify(devices), 200
    except Exception as e:
        return jsonify({'error': f'Gagal mengambil data: {e}'}), 500

@app.route('/api/devices', methods=['POST'])
def api_ext_add_device():
    """ [CREATE] Menambahkan perangkat baru ke database. """
    data = request.json
    if not data:
        return jsonify({'error': 'Request body harus berupa JSON.'}), 400
        
    ip = data.get('ip')
    name = data.get('name')
    username = data.get('username')
    password = data.get('password')
    location = data.get('location', '') # Opsional
    target_api = data.get('targetApi', '') # Opsional
    
    if not all([ip, name, username, password]):
        return jsonify({'error': 'Field ip, name, username, dan password wajib diisi.'}), 400
    
    success, message = db.add_device(ip, name, location, target_api, username, password)
    
    if success:
        return jsonify({'success': True, 'message': message}), 201
    else:
        return jsonify({'success': False, 'message': message}), 409 # 409 Conflict (jika IP sudah ada)

@app.route('/api/devices/<string:ip>', methods=['PUT'])
def api_ext_update_device(ip):
    """ [UPDATE] Memperbarui perangkat di database berdasarkan IP. """
    data = request.json
    if not data:
        return jsonify({'error': 'Request body harus berupa JSON.'}), 400

    # Ambil data yang diizinkan untuk di-update
    name = data.get('name')
    location = data.get('location')
    target_api = data.get('targetApi')
    username = data.get('username')
    password = data.get('password') # Jika password kosong, fungsi DB akan mengabaikannya
    
    # Memastikan data minimal ada
    if not all([name, username]):
        return jsonify({'error': 'Field name dan username wajib diisi.'}), 400

    success = db.update_device(ip, name, location, target_api, username, password)
    
    if success:
        return jsonify({'success': True, 'message': 'Perangkat berhasil diperbarui.'}), 200
    else:
        return jsonify({'success': False, 'message': 'Perangkat tidak ditemukan atau tidak ada perubahan.'}), 404

@app.route('/api/devices/<string:ip>', methods=['DELETE'])
def api_ext_delete_device(ip):
    """ [DELETE] Menghapus perangkat dari database. """
    success = db.delete_device(ip)
    
    if success:
        return jsonify({'success': True, 'message': 'Perangkat berhasil dihapus.'}), 200
    else:
        return jsonify({'success': False, 'message': 'Perangkat tidak ditemukan.'}), 404

# --- AKHIR TAMBAHAN API MANAJEMEN PERANGKAT ---
# --- ENTRY POINT ---
if __name__ == '__main__':
    db.init_db() # Pastikan DB diinisialisasi
    app.run(debug=True, host='0.0.0.0')