import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .input import code, month_key, read_batch, reason_text
from .ledger import Conflict, LedgerError, detail, import_batch, initialize, report, void_deposit


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="TimbangWarga, buku setoran berat bank sampah.")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "Buat database baru."),
        ("import", "Masukkan satu batch nota dari TSV."),
        ("detail", "Tampilkan isi nota dan riwayat pembatalannya."),
        ("void", "Batalkan nota tanpa menghapus catatan."),
        ("report", "Rekap berat aktif per jenis per bulan."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("db", type=Path, help="Path database SQLite.")
        if name == "import":
            command.add_argument("file", type=Path, help="Berkas setoran TSV.")
        if name in {"detail", "void"}:
            command.add_argument("nota")
        if name == "void":
            command.add_argument("--alasan", required=True)
        if name == "report":
            command.add_argument("--bulan", required=True, help="Format YYYY-MM.")
            command.add_argument("--anggota", help="Filter satu kode anggota.")
    return root


def run(args) -> dict:
    match args.command:
        case "init":
            initialize(args.db)
            return {"database": str(args.db), "status": "dibuat"}
        case "import":
            return import_batch(args.db, read_batch(args.file))
        case "detail":
            return detail(args.db, code(args.nota, "Nota"))
        case "void":
            note = code(args.nota, "Nota")
            changed = void_deposit(args.db, note, reason_text(args.alasan))
            return {"nota": note, "status": "dibatalkan" if changed else "sudah_batal"}
        case "report":
            member = code(args.anggota, "Anggota") if args.anggota is not None else None
            return report(args.db, month_key(args.bulan), member)
        case _:
            raise AssertionError(args.command)


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except Conflict as exc:
        print(f"Konflik. {exc}", file=sys.stderr)
        return 1
    except (LedgerError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"Gagal. {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
