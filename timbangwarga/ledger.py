from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .input import Deposit


APP_ID = 1415006001
SCHEMA_VERSION = 1

SCHEMA = (
    """CREATE TABLE deposits (
        note TEXT PRIMARY KEY NOT NULL,
        day TEXT NOT NULL,
        member TEXT NOT NULL,
        digest TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    ) WITHOUT ROWID""",
    """CREATE TABLE materials (
        note TEXT NOT NULL REFERENCES deposits(note),
        material TEXT NOT NULL,
        grams INTEGER NOT NULL CHECK (grams BETWEEN 1 AND 10000000),
        PRIMARY KEY (note, material)
    ) WITHOUT ROWID""",
    """CREATE TABLE voids (
        note TEXT PRIMARY KEY NOT NULL REFERENCES deposits(note),
        reason TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    ) WITHOUT ROWID""",
    "CREATE INDEX deposits_by_day ON deposits(day, member)",
)


class LedgerError(Exception):
    pass


class Conflict(LedgerError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def database(path: Path, *, write: bool = False, check: bool = True):
    uri = path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro")
    connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if check and (
            connection.execute("PRAGMA application_id").fetchone()[0] != APP_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        ):
            raise LedgerError("Berkas bukan database TimbangWarga skema 1.")
        yield connection
    finally:
        connection.close()


def initialize(path: Path) -> None:
    try:
        with path.open("xb"):
            pass
    except FileExistsError as exc:
        raise LedgerError("File database sudah ada.") from exc
    try:
        with database(path, write=True, check=False) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {APP_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def import_batch(path: Path, deposits: list[Deposit]) -> dict[str, int]:
    result = {"baru": 0, "diabaikan": 0, "diabaikan_batal": 0}
    with database(path, write=True) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        recorded_at = now()
        for deposit in deposits:
            digest = deposit.digest()
            prior = connection.execute(
                """SELECT d.digest, v.note AS void_note FROM deposits d
                   LEFT JOIN voids v USING(note) WHERE d.note = ?""", (deposit.note,),
            ).fetchone()
            if prior is not None:
                if prior["digest"] != digest:
                    raise Conflict(f"Nota {deposit.note} sudah ada dengan isi berbeda. Batch dibatalkan.")
                key = "diabaikan" if prior["void_note"] is None else "diabaikan_batal"
                result[key] += 1
                continue
            connection.execute("INSERT INTO deposits VALUES (?, ?, ?, ?, ?)", (
                deposit.note, deposit.date, deposit.member, digest, recorded_at,
            ))
            connection.executemany(
                "INSERT INTO materials VALUES (?, ?, ?)",
                ((deposit.note, material, grams) for material, grams in deposit.materials),
            )
            result["baru"] += 1
    return result


def void_deposit(path: Path, note: str, reason: str) -> bool:
    with database(path, write=True) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            """SELECT v.reason FROM deposits d LEFT JOIN voids v USING(note)
               WHERE d.note = ?""", (note,),
        ).fetchone()
        if prior is None:
            raise LedgerError(f"Nota {note} tidak ditemukan.")
        if prior["reason"] is not None:
            if prior["reason"] != reason:
                raise Conflict(f"Nota {note} sudah dibatalkan dengan alasan berbeda.")
            return False
        connection.execute("INSERT INTO voids VALUES (?, ?, ?)", (note, reason, now()))
    return True


def detail(path: Path, note: str) -> dict:
    with database(path) as connection, connection:
        connection.execute("BEGIN")
        row = connection.execute(
            """SELECT d.note AS nota, d.day AS tanggal, d.member AS anggota,
                      d.recorded_at AS dicatat_utc, v.reason AS alasan_batal,
                      v.recorded_at AS dibatalkan_utc
               FROM deposits d LEFT JOIN voids v USING(note) WHERE d.note = ?""", (note,),
        ).fetchone()
        if row is None:
            raise LedgerError(f"Nota {note} tidak ditemukan.")
        materials = connection.execute(
            """SELECT material AS jenis, grams AS berat_g FROM materials
               WHERE note = ? ORDER BY material""", (note,),
        )
        return dict(row) | {"status": "aktif" if row["alasan_batal"] is None else "batal",
                            "material": [dict(material) for material in materials]}


def report(path: Path, month: str, member: str | None) -> dict:
    where = "d.day BETWEEN ? AND ?"
    params = [month + "-01", month + "-31"]
    if member is not None:
        where += " AND d.member = ?"
        params.append(member)
    with database(path) as connection, connection:
        connection.execute("BEGIN")
        active, voided = connection.execute(
            f"""SELECT COUNT(*) - COUNT(v.note), COUNT(v.note)
                FROM deposits d LEFT JOIN voids v USING(note) WHERE {where}""", params,
        ).fetchone()
        materials = [dict(row) for row in connection.execute(
            f"""SELECT m.material AS jenis, SUM(m.grams) AS berat_g, COUNT(*) AS nota
                FROM deposits d JOIN materials m USING(note) LEFT JOIN voids v USING(note)
                WHERE {where} AND v.note IS NULL
                GROUP BY m.material ORDER BY m.material""", params,
        )]
    return {"bulan": month, "anggota": member, "nota_aktif": active, "nota_batal": voided,
            "total_g": sum(row["berat_g"] for row in materials), "material": materials}
