import requests
from requests.auth import HTTPDigestAuth
import datetime
from config import TIMEZONE, REQUEST_TIMEOUT

def get_device_credentials(func):
    """Decorator to get device credentials from the database before making an API call."""
    def wrapper(device, *args, **kwargs):
        if not device or not device.get('username') or not device.get('password'):
            return {'error': 'Informasi perangkat atau kredensial tidak lengkap.'}, 400
        
        ip = device.get('ip')
        user = device.get('username')
        password = device.get('password')
        auth = HTTPDigestAuth(user, password)
        
        return func(ip, auth, *args, **kwargs)
    return wrapper

@get_device_credentials
def search_users(ip, auth):
    """Mengambil daftar semua pengguna dari perangkat."""
    url = f"http://{ip}/ISAPI/AccessControl/UserInfo/Search?format=json"
    payload = {
        "UserInfoSearchCond": {
            "searchID": "1",
            "searchResultPosition": 0,
            "maxResults": 2000 # Ambil maksimal 2000 user
        }
    }
    try:
        r = requests.post(url, json=payload, auth=auth, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        response_data = r.json()
        if response_data.get('UserInfoSearch', {}).get('responseStatusStrg') == 'OK':
            return response_data.get('UserInfoSearch', {}).get('UserInfo', []), 200
        else:
            return {'error': response_data.get('UserInfoSearch', {}).get('errorMsg', 'Gagal mencari pengguna')}, 400
    except requests.exceptions.RequestException as e:
        return {'error': f"Error koneksi ke perangkat: {e}"}, 500

@get_device_credentials
def add_or_update_user(ip, auth, user_data, mode='add'):
    """Menambah atau memperbarui pengguna di perangkat."""
    endpoint = 'Record' if mode == 'add' else 'Modify'
    url = f"http://{ip}/ISAPI/AccessControl/UserInfo/{endpoint}?format=json"
    
    # Set masa berlaku default jika tidak ada
    begin_time = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S') + TIMEZONE
    end_time = (datetime.datetime.now() + datetime.timedelta(days=365*10)).strftime('%Y-%m-%dT%H:%M:%S') + TIMEZONE

    payload = {
        "UserInfo": {
            "employeeNo": str(user_data.get("employeeNo")),
            "name": user_data.get("name"),
            "userType": "normal",
            "Valid": {
                "enable": True,
                "beginTime": begin_time,
                "endTime": end_time,
                "timeType": "local"
            },
            # Konfigurasi wajib agar user bisa otentikasi
            "doorRight": "1", 
            "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}]
        }
    }

    try:
        # Gunakan PUT untuk update, POST untuk add
        method = 'PUT' if mode == 'update' else 'POST'
        r = requests.request(method, url, json=payload, auth=auth, timeout=REQUEST_TIMEOUT)
        
        r.raise_for_status()
        response_data = r.json()
        
        if response_data.get('statusCode') == 1 and response_data.get('statusString') == 'OK':
            return {'status': 'success', 'message': f'Pengguna berhasil {"diperbarui" if mode == "update" else "ditambahkan"}.'}, 200
        else:
            error_msg = response_data.get('subStatusCode', 'Gagal')
            if error_msg == 'userAlreadyExist':
                error_msg = 'ID Karyawan sudah digunakan.'
            return {'error': error_msg}, 400
    except requests.exceptions.RequestException as e:
        return {'error': f"Error koneksi ke perangkat: {e}"}, 500

@get_device_credentials
def delete_user(ip, auth, employee_no):
    """Menghapus pengguna dari perangkat."""
    url = f"http://{ip}/ISAPI/AccessControl/UserInfo/Delete?format=json"
    payload = {
        "UserInfoDelCond": {
            "employeeNoList": [
                {"employeeNo": str(employee_no)}
            ]
        }
    }
    try:
        # Hikvision menggunakan metode PUT untuk operasi hapus ini
        r = requests.put(url, json=payload, auth=auth, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        response_data = r.json()
        
        if response_data.get('statusCode') == 1 and response_data.get('statusString') == 'OK':
            return {'status': 'success', 'message': 'Pengguna berhasil dihapus.'}, 200
        else:
            return {'error': response_data.get('subStatusCode', 'Gagal menghapus pengguna')}, 400
    except requests.exceptions.RequestException as e:
        return {'error': f"Error koneksi ke perangkat: {e}"}, 500