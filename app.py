import os
import platform
import datetime
import json
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
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

class User(UserMixin):
    def __init__(self, id, username, password):
        self.id, self.username, self.password_hash = id, username, generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'bukalah123')

admin_user = User(id=1, username=ADMIN_USERNAME, password=ADMIN_PASSWORD)
users = {str(admin_user.id): admin_user}

@login_manager.user_loader
def load_user(user_id): return users.get(user_id)
# ----------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username, password = request.form.get('username'), request.form.get('password')
        remember = 'remember' in request.form
        user_to_login = next((u for u in users.values() if u.username == username), None)
        if user_to_login and user_to_login.check_password(password):
            login_user(user_to_login, remember=remember)
            return redirect(request.args.get('next') or url_for('index'))
        else:
            flash('Username atau password salah.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- ROUTE UTAMA APLIKASI ---
@app.route('/')
@login_required
def index():
    stats = db.get_dashboard_stats()
    devices_status = db.get_devices_status()
    event_limit = len(devices_status)
    recent_events = db.get_recent_events(limit=event_limit)
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

# --- ROUTE CRUD & API ---
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

# --- API MANAJEMEN PENGGUNA ---
@app.route('/api/devices/<string:ip>/users')
@login_required
def api_get_device_users(ip):
    device = db.get_device_by_ip(ip)
    if not device or not device.get('username') or not device.get('password'):
        return jsonify({'error': 'Kredensial perangkat tidak ditemukan.'}), 404
    url = f"http://{device['ip']}/ISAPI/AccessControl/UserInfo/Search?format=json"
    auth = HTTPDigestAuth(device['username'], device['password'])
    payload = {"UserInfoSearchCond": {"searchID": "1", "searchResultPosition": 0, "maxResults": 2000}}
    try:
        response = requests.post(url, json=payload, auth=auth, timeout=10)
        data = response.json()
        if response.status_code != 200:
            return jsonify({'error': f"Error dari perangkat: {data.get('errorMsg', 'Unknown')}"}), 502
        user_list = data.get('UserInfoSearch', {}).get('UserInfo', [])
        if user_list:
            employee_ids = [int(u['employeeNo']) for u in user_list if u.get('employeeNo', '').isdigit()]
            attendance_data = db.get_earliest_attendance_by_date(employee_ids, datetime.date.today().strftime('%Y-%m-%d'))
            for user in user_list:
                emp_id = int(user.get('employeeNo')) if user.get('employeeNo','').isdigit() else None
                user['attendance_time'] = attendance_data.get(emp_id)
        return jsonify(user_list)
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
    db.init_db()
    app.run(debug=True, host='0.0.0.0')