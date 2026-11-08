# TimbangWarga

TimbangWarga adalah CLI Python dan SQLite untuk petugas bank sampah unit. Satu nota dapat memuat beberapa jenis material. Impor hasil timbang, tinjau detail, koreksi nota melalui pembatalan, lalu rekap berat aktif per bulan.

## Masalah dan bukti

| Sumber | Cakupan dan temuan |
| --- | --- |
| Hayati dan Prasetyo, 2023, *Analisis Sistem Informasi Akuntansi Pada Bank Sampah NTB Mandiri* | Studi kasus di Kota Mataram. Peneliti mendokumentasikan alur dari penerimaan dan penimbangan sampah hingga pencatatan hasil setoran nasabah. [Artikel penelitian](https://j-innovative.org/index.php/Innovative/article/download/6895/4703/10668) |
| Erlinda dkk., Mei 2024, *Penerapan Teknologi dalam Pengelolaan Data Sampah di Bank Sampah Unit kantor Dinas Kebersihan dan Lingkungan Hidup* | Laporan pendampingan di Pekanbaru. Tim mendokumentasikan pencatatan dan rekap per jenis yang dikerjakan manual. [Laporan pendampingan](https://jurnal.adai.or.id/index.php/comsep/article/download/603/448/2971) |

Studi Mataram memberi konteks alur timbang dan pencatatan. Laporan Pekanbaru menunjukkan alasan untuk menguji rekap otomatis. Program ini memilih satu hambatan, yaitu menyusun rekap berat dari beberapa batch hasil timbang tanpa menggandakan nota saat file yang sama dikirim ulang. Kebutuhan impor ulang merupakan asumsi desain. Data contoh dan pengujian seluruhnya sintetis. Penerapan pada unit bank sampah di Mataram masih berupa hipotesis yang perlu diuji di lapangan.

## Fitur

1. **Impor batch nota** dari TSV ke SQLite. Satu nota menampung beberapa material dengan berat dalam gram.
2. **Tinjau detail nota**, termasuk waktu pencatatan, material, dan status pembatalan.
3. **Batalkan salah timbang** dengan alasan tercatat. Isi nota lama tetap tersedia untuk penelusuran.
4. **Rekap bulanan** per material dengan filter opsional kode anggota. Keluaran JSON bisa disimpan untuk pemrosesan lanjutan.

## Instalasi dan demo

Gunakan Python 3.10 atau lebih baru. Ekstrak ZIP dan buka terminal di direktori yang berisi `pyproject.toml`. Program memakai pustaka standar Python dan SQLite bawaannya.

```powershell
python -m timbangwarga init demo.sqlite
python -m timbangwarga import demo.sqlite contoh/setoran.tsv
python -m timbangwarga import demo.sqlite contoh/setoran.tsv
python -m timbangwarga report demo.sqlite --bulan 2026-11
```

Impor pertama membuat dua nota. Impor ulang mengembalikan `{"baru": 0, "diabaikan": 2, "diabaikan_batal": 0}`. Rekap tetap 4.350 gram, terdiri dari 2.000 gram kertas, 350 gram logam, dan 2.000 gram PET.

```powershell
python -m timbangwarga import demo.sqlite contoh/konflik.tsv
python -m timbangwarga detail demo.sqlite TW-003
python -m timbangwarga void demo.sqlite TW-002 --alasan "Salah timbang"
python -m timbangwarga detail demo.sqlite TW-002
python -m timbangwarga report demo.sqlite --bulan 2026-11
```

`konflik.tsv` berisi nota baru `TW-003` dan berat berbeda untuk `TW-001`. Impor mengembalikan kode 1 dan seluruh batch dibatalkan, sehingga detail `TW-003` belum ada. Setelah `TW-002` dibatalkan, rekap menjadi 3.250 gram. Berat lama dan alasan pembatalan masih terlihat melalui `detail`. Untuk mencatat hasil timbang pengganti, pakai nomor nota baru.

## Format input

Gunakan header persis `nota<TAB>tanggal<TAB>anggota<TAB>jenis<TAB>berat_g`. Lihat berkas tab asli di `contoh/`. Satu baris mewakili satu jenis material dalam nota. Semua baris bernomor nota sama harus memiliki tanggal dan anggota sama. Setiap jenis muncul sekali per nota.

- `nota`, `anggota`, dan `jenis` berupa kode ASCII 1–40 karakter. Kode dinormalisasi ke huruf besar. Nomor nota unik di seluruh database.
- `tanggal` memakai YYYY-MM-DD. `berat_g` adalah bilangan bulat dari 1 sampai 10.000.000 gram. Masukkan 1,25 kg sebagai `1250`.
- TSV memakai UTF-8 dengan pemisah tab. Batas batch 2 MiB dan 5.000 baris data.
- `--bulan YYYY-MM` memilih bulan tanggal setoran. `--anggota A001` membatasi rekap pada satu kode anggota.

| Kode keluar | Arti |
| --- | --- |
| `0` | Berhasil, termasuk percobaan ulang identik. |
| `1` | Konflik isi nota atau alasan pembatalan. |
| `2` | Input, database, atau operasi berkas gagal. |

## Jaminan dan batas

**Satu batch diterapkan seluruhnya atau dibatalkan seluruhnya.** Importir memvalidasi TSV sebelum menulis dan menggunakan `BEGIN IMMEDIATE` supaya pembacaan nota lama dan penulisan hasil baru terjadi dalam satu transaksi. SHA-256 dihitung dari isi nota yang sudah dinormalisasi, termasuk urutan jenis yang disortir. Dua importir pada database yang sama memperoleh giliran menulis. Pengulangan nota identik hanya mengubah penghitung keluaran CLI. Nota yang sudah dibatalkan tetap batal setelah impor ulang. Konflik membatalkan seluruh batch, termasuk nota baru yang diproses sebelumnya.

Pembatalan menyimpan alasan dan timestamp UTC di tabel terpisah. Rekap selalu menunjukkan nota yang masih aktif saat rekap dijalankan dan memakai tanggal setoran. Membatalkan nota lama akan mengoreksi rekap bulan setoran. Cakupan program adalah berat dan koreksi catatan lokal. Saldo, harga, pembayaran, serta sinkronisasi antarperangkat berada di luar cakupan. Simpan kode anggota sesuai prosedur pengelola dan gunakan data sintetis untuk berbagi contoh.

| Berkas | Tanggung jawab |
| --- | --- |
| `timbangwarga/input.py` | Parsing TSV, validasi, dan fingerprint nota. |
| `timbangwarga/ledger.py` | Skema, transaksi, pembatalan, detail, dan rekap SQLite. |
| `timbangwarga/__main__.py` | CLI dan keluaran JSON. |
| `tests/test_cli.py` | Tes alur nyata melalui proses CLI dan SQLite sementara. |

```powershell
python -m unittest discover -s tests -v
```

Tes integrasi meliputi impor ulang dengan urutan berbeda, dua importir pada satu database, konflik setelah nota baru, kegagalan tulis di tengah transaksi, dan pembatalan yang tetap berlaku setelah impor ulang.
