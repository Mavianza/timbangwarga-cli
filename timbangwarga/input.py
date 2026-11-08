from dataclasses import dataclass
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re


class InputError(ValueError):
    pass


HEADER = ["nota", "tanggal", "anggota", "jenis", "berat_g"]
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
CODE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}")


def code(value: str, label: str) -> str:
    value = value.strip()
    if not CODE.fullmatch(value):
        raise InputError(f"{label} harus 1–40 karakter ASCII berupa huruf, angka, _ atau -.")
    return value.upper()


def iso_date(value: str) -> str:
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise InputError("Tanggal harus valid dan berformat YYYY-MM-DD.")


def month_key(value: str) -> str:
    iso_date(value + "-01")
    return value


def reason_text(value: str) -> str:
    value = value.strip()
    if not (1 <= len(value) <= 200 and value.isprintable()):
        raise InputError("Alasan harus 1–200 karakter yang dapat dicetak.")
    return value


@dataclass(frozen=True)
class Deposit:
    note: str
    date: str
    member: str
    materials: tuple[tuple[str, int], ...]

    def digest(self) -> str:
        payload = json.dumps(
            [self.note, self.date, self.member, self.materials],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(payload.encode("ascii")).hexdigest()


def read_batch(path: Path) -> list[Deposit]:
    if not path.is_file():
        raise InputError("Berkas TSV harus berupa file biasa.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError("Berkas TSV melebihi 2 MiB.")
    try:
        data = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise InputError("Berkas TSV harus memakai UTF-8.") from exc
    reader = io.StringIO(data, newline=None)
    if reader.readline().removesuffix("\n").split("\t") != HEADER:
        raise InputError("Header TSV harus persis: nota, tanggal, anggota, jenis, berat_g (pisahkan dengan tab).")

    batches: dict[str, tuple[str, str, dict[str, int]]] = {}
    for line, row in enumerate(reader, start=2):
        try:
            if line > MAX_ROWS + 1:
                raise InputError("Satu batch maksimal 5.000 baris data.")
            fields = row.removesuffix("\n").split("\t", len(HEADER))
            if len(fields) != len(HEADER):
                raise InputError("Baris harus memiliki lima kolom.")
            note, day, member, material, grams = (field.strip() for field in fields)
            note = code(note, "Nota")
            day = iso_date(day)
            member = code(member, "Anggota")
            material = code(material, "Jenis")
            if not re.fullmatch(r"[0-9]{1,8}", grams) or not 1 <= int(grams) <= 10_000_000:
                raise InputError("berat_g harus bilangan bulat 1 sampai 10.000.000 gram.")
            existing_day, existing_member, materials = batches.setdefault(note, (day, member, {}))
            if (existing_day, existing_member) != (day, member):
                raise InputError(f"Tanggal atau anggota tidak konsisten untuk nota {note}.")
            if material in materials:
                raise InputError(f"Jenis {material} berulang dalam nota {note}.")
            materials[material] = int(grams)
        except InputError as exc:
            raise InputError(f"Baris {line}. {exc}") from exc
    if not batches:
        raise InputError("Batch harus memuat setidaknya satu nota.")
    return [Deposit(note, day, member, tuple(sorted(materials.items())))
            for note, (day, member, materials) in batches.items()]
