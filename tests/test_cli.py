from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest


PROJECT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "timbangwarga"]
GOOD = PROJECT / "contoh/setoran.tsv"
CONFLICT = PROJECT / "contoh/konflik.tsv"
HEADER = "nota\ttanggal\tanggota\tjenis\tberat_g\n"
ENV = os.environ | {"PYTHONIOENCODING": "utf-8"}


class LedgerIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "uji.sqlite"
        self.cli("init", self.db)

    def cli(self, *args, status=0):
        result = subprocess.run(
            [*CLI, *map(str, args)], cwd=PROJECT, encoding="utf-8",
            capture_output=True, timeout=15, env=ENV,
        )
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        if status:
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)
            return result.stderr
        return json.loads(result.stdout)

    def batch(self, *rows):
        path = self.root / "batch.tsv"
        path.write_bytes((HEADER + "".join("\t".join(row) + "\n" for row in rows)).encode("utf-8"))
        return path

    def report(self, *args):
        return self.cli("report", self.db, "--bulan", "2026-11", *args)

    def test_main_flow_import_detail_report_and_exact_grams(self):
        self.assertEqual(self.cli("import", self.db, GOOD), {
            "baru": 2, "diabaikan": 0, "diabaikan_batal": 0,
        })
        result = self.report()
        self.assertEqual((result["nota_aktif"], result["nota_batal"], result["total_g"]), (2, 0, 4350))
        self.assertEqual(result["material"], [
            {"jenis": "KERTAS", "berat_g": 2000, "nota": 1},
            {"jenis": "LOGAM", "berat_g": 350, "nota": 1},
            {"jenis": "PET", "berat_g": 2000, "nota": 2},
        ])
        self.assertEqual(self.report("--anggota", "a002")["total_g"], 1100)
        detail = self.cli("detail", self.db, "tw-001")
        self.assertEqual((detail["tanggal"], detail["anggota"], detail["status"]), ("2026-11-07", "A001", "aktif"))

    def test_reordered_batch_with_bom_and_case_is_idempotent(self):
        self.cli("import", self.db, GOOD)
        before = self.report()
        lines = GOOD.read_text(encoding="utf-8").splitlines()
        replay = self.root / "ulangan.tsv"
        replay.write_bytes(("\ufeff" + "\r\n".join([lines[0], *(line.lower() for line in reversed(lines[1:]))]) + "\r\n").encode())
        self.assertEqual(self.cli("import", self.db, replay), {
            "baru": 0, "diabaikan": 2, "diabaikan_batal": 0,
        })
        self.assertEqual(self.report(), before)

    def test_conflict_after_new_note_rolls_back_entire_batch(self):
        self.cli("import", self.db, GOOD)
        old = self.report()
        self.assertIn("TW-001", self.cli("import", self.db, CONFLICT, status=1))
        self.assertIn("tidak ditemukan", self.cli("detail", self.db, "TW-003", status=2))
        self.assertEqual(self.report(), old)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0], 4)

    def test_sqlite_error_during_insert_rolls_back_parent_and_other_note(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("""CREATE TRIGGER force_error BEFORE INSERT ON materials
                            WHEN NEW.note = 'TW-002' BEGIN SELECT RAISE(ABORT, 'uji penulisan'); END""")
        self.assertIn("uji penulisan", self.cli("import", self.db, GOOD, status=2))
        self.assertEqual(self.report()["nota_aktif"], 0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0], 0)

    def test_two_importers_wait_for_writer_and_only_one_inserts(self):
        with closing(sqlite3.connect(self.db)) as lock, lock:
            lock.execute("BEGIN IMMEDIATE")
            workers = []
            try:
                for _ in range(2):
                    workers.append(subprocess.Popen(
                        [*CLI, "import", str(self.db), str(GOOD)], cwd=PROJECT,
                        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        env=ENV,
                    ))
                time.sleep(0.2)
                self.assertTrue(all(p.poll() is None for p in workers))
                lock.commit()
                answers = []
                for worker in workers:
                    stdout, stderr = worker.communicate(timeout=15)
                    self.assertEqual(worker.returncode, 0, stdout + stderr)
                    answers.append(json.loads(stdout))
            finally:
                for worker in workers:
                    if worker.poll() is None:
                        worker.kill()
                        worker.communicate()
        self.assertEqual(sorted(x["baru"] for x in answers), [0, 2])
        self.assertEqual(sorted(x["diabaikan"] for x in answers), [0, 2])
        self.assertEqual(self.report()["total_g"], 4350)

    def test_void_preserves_detail_and_repeat_cannot_restore_note(self):
        self.cli("import", self.db, GOOD)
        args = ("void", self.db, "TW-002", "--alasan", "Salah timbang")
        self.assertEqual(self.cli(*args)["status"], "dibatalkan")
        old = self.cli("detail", self.db, "TW-002")
        self.assertEqual(self.cli(*args)["status"], "sudah_batal")
        self.assertEqual(self.cli("import", self.db, GOOD)["diabaikan_batal"], 1)
        self.assertEqual(self.cli("detail", self.db, "TW-002"), old)
        self.assertEqual((old["status"], old["alasan_batal"], len(old["material"])), ("batal", "Salah timbang", 2))
        report = self.report()
        self.assertEqual((report["nota_aktif"], report["nota_batal"], report["total_g"]), (1, 1, 3250))
        self.assertNotIn("LOGAM", {row["jenis"] for row in report["material"]})

    def test_different_void_reason_conflicts_and_keeps_original(self):
        self.cli("import", self.db, GOOD)
        self.cli("void", self.db, "TW-002", "--alasan", "Alasan awal")
        self.assertIn("alasan berbeda", self.cli("void", self.db, "TW-002", "--alasan", "Alasan lain", status=1))
        self.assertEqual(self.cli("detail", self.db, "TW-002")["alasan_batal"], "Alasan awal")

    def test_same_note_after_void_still_conflicts_if_weight_changed(self):
        self.cli("import", self.db, GOOD)
        self.cli("void", self.db, "TW-001", "--alasan", "Salah timbang")
        self.cli("import", self.db, CONFLICT, status=1)
        self.assertEqual(self.report()["total_g"], 1100)
        self.cli("detail", self.db, "TW-003", status=2)

    def test_month_edges_and_leap_day(self):
        path = self.batch(
            ("N1", "2024-02-29", "A", "PET", "550"),
            ("N2", "2024-03-01", "A", "PET", "600"),
            ("N3", "2026-10-31", "A", "PET", "650"),
            ("N4", "2026-11-01", "A", "PET", "700"),
            ("N5", "2026-11-30", "A", "PET", "800"),
            ("N6", "2026-12-01", "A", "PET", "900"),
        )
        self.cli("import", self.db, path)
        self.assertEqual(self.report()["total_g"], 1500)
        self.assertEqual(self.cli("report", self.db, "--bulan", "2024-02")["total_g"], 550)
        self.cli("report", self.db, "--bulan", "2026-13", status=2)

    def test_invalid_weight_or_date_does_not_record_valid_earlier_rows(self):
        for day, grams in [("2026-11-31", "100"), ("2026-11-08", "2,5"), ("2026-11-08", "0")]:
            with self.subTest(day=day, grams=grams):
                path = self.batch(("N1", "2026-11-07", "A", "PET", "100"), ("N2", day, "A", "PET", grams))
                self.assertIn("Baris 3", self.cli("import", self.db, path, status=2))
                self.assertEqual(self.report()["total_g"], 0)

    def test_repeated_material_and_inconsistent_header_metadata_fail(self):
        for row, expected in [
            (("n1", "2026-11-07", "A", "pet", "100"), "berulang"),
            (("N1", "2026-11-07", "B", "KERTAS", "100"), "tidak konsisten"),
        ]:
            path = self.batch(("N1", "2026-11-07", "A", "PET", "100"), row)
            self.assertIn(expected, self.cli("import", self.db, path, status=2))
        self.assertEqual(self.report()["total_g"], 0)

    def test_invalid_utf8_wrong_header_and_oversize_fail(self):
        path = self.root / "rusak.tsv"
        for payload, expected in [(b"\xff", "UTF-8"), (b"nota\n", "Header"), (b" " * (2 * 1024 * 1024 + 1), "2 MiB")]:
            with self.subTest(expected=expected):
                path.write_bytes(payload)
                self.assertIn(expected, self.cli("import", self.db, path, status=2))

    def test_init_never_overwrites_and_other_database_is_rejected(self):
        self.cli("import", self.db, GOOD)
        self.assertIn("sudah ada", self.cli("init", self.db, status=2))
        self.assertEqual(self.report()["total_g"], 4350)
        other = self.root / "lain.sqlite"
        with closing(sqlite3.connect(other)) as conn, conn:
            conn.execute("CREATE TABLE milik_pengguna (id INTEGER)")
        self.assertIn("bukan database TimbangWarga", self.cli("import", other, GOOD, status=2))
        with closing(sqlite3.connect(other)) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [("milik_pengguna",)])

    def test_missing_database_is_not_created_by_import(self):
        missing = self.root / "tidak-ada.sqlite"
        self.cli("import", missing, GOOD, status=2)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
