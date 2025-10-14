Tentu. Berdasarkan seluruh program yang telah kita bangun, berikut adalah draf `README.md` yang lengkap dan profesional. Anda bisa langsung menyalinnya ke dalam file `README.md` di proyek Anda.

-----

# Terminal Sinkronisasi Event Hikvision

## Deskripsi

Proyek ini adalah sebuah sistem lengkap yang berfungsi sebagai jembatan antara perangkat *face recognition* Hikvision dengan API eksternal. Sistem ini terdiri dari dua komponen utama:

1.  **Layanan Latar Belakang (`service.py`):** Sebuah skrip Python yang berjalan secara terus-menerus untuk menarik data event dari perangkat Hikvision, memprosesnya, menyimpan gambar, dan mengirimkannya ke API tujuan.
2.  **Aplikasi Web (`app.py`):** Sebuah dasbor berbasis Flask yang menyediakan antarmuka untuk memantau status perangkat dan melihat log event secara *real-time*.

Tujuan utama sistem ini adalah untuk memastikan data presensi dari perangkat Hikvision dapat terintegrasi dengan sistem lain secara andal, sambil menyediakan alat monitoring yang mudah digunakan.

## Fitur Utama

  * **Dasbor Real-time:** Memantau status perangkat dan event terbaru secara langsung tanpa perlu me-refresh halaman, berkat teknologi WebSockets.
  * **Log Event Lengkap:** Halaman khusus untuk melihat riwayat semua event, lengkap dengan fitur filter berdasarkan tanggal, perangkat, dan lokasi.
  * **Manajemen Perangkat:** Antarmuka untuk menambah, mengedit, dan menghapus perangkat Hikvision yang akan dipantau.
  * **Pemantauan Status Perangkat:** Sistem secara otomatis melakukan *ping* ke setiap perangkat. Jika perangkat gagal merespons beberapa kali, statusnya akan diubah menjadi `offline` dan proses sinkronisasi untuk perangkat tersebut akan ditangguhkan sementara.
  * **Pemrosesan Event Cerdas:** Hanya event "Face recognized" yang akan diproses dan dikirim ke API. Event lain (seperti "Access granted", dll.) akan secara otomatis ditandai sebagai `failed`.
  * **Penyimpanan Gambar Otomatis:** Setiap event yang valid akan disertai dengan pengunduhan dan penyimpanan gambar bukti ke server secara otomatis.
  * **Sistem Peringatan Dini:** Memberikan notifikasi visual `Warning` di antarmuka jika koneksi ke perangkat mulai bermasalah, sebelum perangkat tersebut dianggap `offline` sepenuhnya.
  * **Antarmuka Aman:** Dilindungi oleh sistem login untuk memastikan hanya admin yang dapat mengakses dasbor.

## Arsitektur Sistem

Sistem ini bekerja dengan alur sebagai berikut:

1.  **`service.py`** berjalan di latar belakang, terus-menerus melakukan ping dan menarik data dari semua perangkat yang terdaftar di database.
2.  Saat event baru yang valid ("Face recognized") terdeteksi, `service.py` akan:
      * Menyimpan detail event ke database MySQL.
      * Mengunduh dan menyimpan gambar terkait ke folder `static/images/`.
      * Mengirim data yang sudah diproses ke API eksternal.
      * Memberi notifikasi ke `app.py` tentang perubahan status.
3.  **`app.py`** menerima notifikasi dari `service.py` dan menyiarkannya (broadcast) melalui **WebSocket** ke semua pengguna yang sedang membuka dasbor.
4.  **Antarmuka Pengguna (Frontend)** yang dibuka di browser menerima siaran WebSocket dan memperbarui tampilan secara instan.

## Tumpukan Teknologi

  * **Backend:** Python 3, Flask, Flask-SocketIO, Gunicorn
  * **Database:** MySQL
  * **Frontend:** HTML5, Bootstrap 5, JavaScript, jQuery
  * **Lainnya:** `requests` (untuk komunikasi dengan perangkat & API)

## Instalasi

1.  **Clone Repositori**

    ```bash
    git clone https://[URL-repositori-Anda].git
    cd [nama-folder-repositori]
    ```

2.  **Buat Virtual Environment**

    ```bash
    python -m venv venv
    source venv/bin/activate  # Untuk Linux/macOS
    venv\Scripts\activate    # Untuk Windows
    ```

3.  **Instal Dependensi**
    Buat file `requirements.txt` dengan isi berikut:

    ```
    Flask
    Flask-Login
    Flask-SocketIO
    Flask-Cors
    mysql-connector-python
    requests
    gunicorn
    python-dotenv
    eventlet  # Diperlukan untuk Gunicorn + SocketIO
    ```

    Kemudian jalankan:

    ```bash
    pip install -r requirements.txt
    ```

4.  **Konfigurasi Database**
    Pastikan Anda sudah memiliki server MySQL yang berjalan. Buat sebuah database baru (misalnya, `hikvision_db`).

5.  **Konfigurasi Aplikasi**
    Salin file `config.py.example` menjadi `config.py` dan sesuaikan semua variabel di dalamnya, terutama koneksi database dan kredensial perangkat Hikvision.

## Cara Menjalankan

Aplikasi ini terdiri dari 2 proses yang harus berjalan bersamaan. Buka dua terminal terpisah.

1.  **Jalankan Layanan Latar Belakang (Terminal 1)**
    Aktifkan virtual environment, lalu jalankan:

    ```bash
    python service.py
    ```

    Skrip ini akan meminta username dan password perangkat Hikvision jika tidak diatur di `config.py`. Proses ini harus terus berjalan untuk sinkronisasi.

2.  **Jalankan Server Web (Terminal 2)**
    Aktifkan virtual environment, lalu jalankan server menggunakan Gunicorn (direkomendasikan untuk produksi dengan SocketIO):

    ```bash
    gunicorn --worker-class eventlet -w 1 --log-level=info "app:app"
    ```

    Aplikasi web sekarang akan dapat diakses di `http://127.0.0.1:8000`.

## Struktur Proyek

```
/
├── app.py              # File utama aplikasi web Flask
├── service.py          # Skrip layanan sinkronisasi latar belakang
├── database.py         # Modul untuk interaksi database
├── config.py           # File konfigurasi (kredensial, dll.)
├── requirements.txt    # Daftar dependensi Python
├── static/
│   └── images/         # Tempat gambar-gambar event disimpan
└── templates/
    ├── dashboard.html  # Halaman dasbor utama
    ├── events.html     # Halaman log event
    ├── devices.html    # Halaman kelola perangkat
    ├── layout.html     # Template dasar HTML
    └── login.html      # Halaman login
```