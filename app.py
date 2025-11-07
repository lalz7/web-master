import os
import platform
import datetime
import json
import requests
import base64
from requests.auth import HTTPDigestAuth
from flask import (Flask, render_template, request, redirect, url_for, 
                   flash, jsonify, Response, g)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
import database as db

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
        'whatsapp_api_url': db.get_setting('whatsapp_api_url', default='http://10.1.105.164:60001')
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
        flash('Pengaturan notifikasi berhasil disimpan.', 'success')
    
    return redirect(url_for('settings'))
# ---------------------------------------------

# --- ROUTE UTAMA APLIKASI (TETAP SAMA) ---
@app.route('/')
@login_required
def index():
    stats = db.get_dashboard_stats()
    
    devices_status = db.get_devices_status() or []
    event_limit = len(devices_status) 
    recent_events = db.get_recent_events(limit=event_limit) or []
    
    return render_template('dashboard.html',
                           stats=stats,
                           recent_events=recent_events,
                           devices_status=devices_status)

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

# --- API MANAJEMEN PENGGUNA (TETAP SAMA) ---
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

@app.route('/api/devices/<string:ip>/users/<string:employee_no>/update', methods=['PUT'])
@login_required
def api_update_user_info(ip, employee_no):
    device = db.get_device_by_ip(ip)
    if not device: return jsonify({'error': 'Perangkat tidak ditemukan'}), 404
    data = request.json
    new_name = data.get('name')
    new_gender = data.get('gender')
    if not new_name: return jsonify({'error': 'Nama tidak boleh kosong'}), 400
    url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Modify?format=json"
    auth = HTTPDigestAuth(device['username'], device['password'])
    payload = {"UserInfo": {"employeeNo": employee_no, "name": new_name}}
    if new_gender in ['male', 'female', 'unknown']:
        payload["UserInfo"]["gender"] = new_gender
    try:
        response = requests.put(url, json=payload, auth=auth, timeout=10)
        if response.status_code in [200, 204]:
            return jsonify({'success': True})
        response_data = response.json() if response.content else {'errorMsg': response.text}
        return jsonify({'error': f"Gagal update data: {response_data.get('errorMsg') or 'Respons tidak terduga.'}"}), 502
    except Exception as e:
        return jsonify({'error': f"Error koneksi: {e}"}), 500

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
    user_payload = {"UserInfo": {"employeeNo": employee_no, "name": name, "userType": "normal", "gender": gender if gender in ['male', 'female'] else 'unknown', "Valid": {"enable": True, "beginTime": formatted_start_time, "endTime": formatted_end_time}, "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]}}
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
        files = {'FaceDataRecord': (None, json.dumps(json_payload), 'application/json'), 'FaceImage': (photo_file.filename, photo_data, 'image/jpeg')}
        try:
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

# --- ENTRY POINT ---
if __name__ == '__main__':
    db.init_db() # Pastikan DB diinisialisasi
    app.run(debug=True, host='0.0.0.0')