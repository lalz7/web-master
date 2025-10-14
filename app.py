import os
import platform
import datetime
import json
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_login import (LoginManager, UserMixin, login_user, logout_user, 
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_cors import CORS
import database as db

app = Flask(__name__)
app.secret_key = 'ganti-dengan-kunci-rahasia-yang-sangat-acak-dan-panjang'
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

admin_user = User(id=1, username='admin', password='bukalah123')
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
    """Route untuk dashboard utama."""
    stats = db.get_dashboard_stats()
    recent_events = db.get_recent_events(limit=7)
    
    # PERBAIKAN: Mengambil devices_status untuk ditampilkan di dasbor
    devices_status = db.get_devices_status()
    
    return render_template('dashboard.html', 
                           stats=stats, 
                           recent_events=recent_events,
                           devices_status=devices_status) # Variabel yang benar dikirim

@app.route('/events')
@login_required
def events():
    """Route untuk halaman Log Event yang detail."""
    filters = {k: v for k, v in request.args.items() if v}
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
    """Route untuk halaman Kelola Perangkat."""
    all_devices = db.get_all_devices()
    return render_template('devices.html', devices=all_devices)
    
# --- ROUTE CRUD & API ---
@app.route('/devices/add', methods=['POST'])
@login_required
def add_device():
    ip, name, location, target_api = (request.form.get(key) for key in ['ip', 'name', 'location', 'targetApi'])
    if not ip or not name:
        flash('IP dan Nama Perangkat wajib diisi.', 'danger')
    else:
        success, message = db.add_device(ip, name, location, target_api)
        flash(message, 'success' if success else 'danger')
    return redirect(url_for('devices'))

@app.route('/devices/update', methods=['POST'])
@login_required
def update_device():
    ip, name, location, target_api = (request.form.get(key) for key in ['ip', 'name', 'location', 'targetApi'])
    if not ip or not name:
        flash('IP dan Nama Perangkat wajib diisi.', 'danger')
    else:
        if db.update_device(ip, name, location, target_api):
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
    
@app.route('/api/event/<int:event_id>')
@login_required
def api_get_event(event_id):
    """API endpoint untuk mendapatkan detail satu event."""
    event = db.get_event_by_id(event_id)
    if event:
        # Menentukan URL gambar, prioritaskan gambar lokal
        event['imageUrl'] = url_for('static', filename=event['localImagePath'], _external=True) if event.get('localImagePath') else event.get('pictureURL')
        if 'localImagePath' in event: del event['localImagePath']
        if 'pictureURL' in event: del event['pictureURL']
        return jsonify(event)
    return jsonify({'error': 'Event not found'}), 404

def ping_device(ip):
    """Fungsi untuk melakukan ping ke IP address."""
    param = "-n 1 -w 1000" if platform.system().lower() == "windows" else "-c 1 -W 1"
    cmd = f"ping {param} {ip}"
    # Mengarahkan output ke NUL (Windows) atau /dev/null (Linux/macOS)
    redirect_out = " > NUL 2>&1" if platform.system().lower()=="windows" else " > /dev/null 2>&1"
    return os.system(cmd + redirect_out) == 0
    
@app.route('/api/ping/<string:ip>')
@login_required
def api_ping(ip):
    """API endpoint untuk ping perangkat."""
    status = 'online' if ping_device(ip) else 'offline'
    db.update_device_ping_status(ip, status)
    return jsonify({'status': status})

@app.route('/api/devices_status')
@login_required
def api_devices_status():
    """API endpoint untuk polling status semua perangkat."""
    devices_status = db.get_devices_status()
    return jsonify(devices_status)

@app.route('/api/logs/by-date/<string:date_string>')
def api_get_logs_by_date(date_string):
    """API endpoint publik untuk mengambil log berdasarkan tanggal."""
    try:
        datetime.datetime.strptime(date_string, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Format tanggal tidak valid. Gunakan format YYYY-MM-DD.'}), 400

    location_filter = request.args.get('location', None)
    ip_filter = request.args.get('ip', None)
    events = db.get_events_by_date(date_string, location=location_filter, ip=ip_filter)
    
    # Membersihkan dan menyusun data sebelum dikirim sebagai JSON
    cleaned_events = []
    for event in events:
        image_url = url_for('static', filename=event['localImagePath'], _external=True) if event.get('localImagePath') else event.get('pictureURL')
        cleaned_event = {
            "id": event.get('id'), "location": event.get('location'),
            "deviceName": event.get('deviceName'), "ip": event.get('ip'),
            "employeeId": event.get('employeeId'), "name": event.get('name'),
            "date": event.get('date'), "time": event.get('time'),
            "eventDesc": event.get('eventDesc'), "syncType": event.get('syncType'),
            "apiStatus": event.get('apiStatus'), "imageUrl": image_url
        }
        cleaned_events.append(cleaned_event)

    json_string = json.dumps(cleaned_events, indent=4)
    return Response(json_string, mimetype='application/json')

# --- ENTRY POINT ---
if __name__ == '__main__':
    # Memastikan tabel ada saat aplikasi pertama kali dijalankan
    db.init_db()
    app.run(debug=True, host='0.0.0.0')

