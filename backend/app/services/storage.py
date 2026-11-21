"""Локальное файловое хранилище: приём, проверка типа, контрольная сумма, PDF-копия для Office."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy.orm import Session

from .. import config
from ..models import StoredFile, new_id

# расширение → (MIME, вид блока курса)
ALLOWED = {
    "pdf": ("application/pdf", "pdf"),
    "mp4": ("video/mp4", "video"),
    "m4v": ("video/mp4", "video"),
    "webm": ("video/webm", "video"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
}
OFFICE = {"docx", "xlsx", "pptx"}


class UploadError(Exception):
    """Причина отказа, которую можно показать методисту."""


def ext_of(name: str) -> str:
    return Path(name).suffix.lower().lstrip(".")


def _check_signature(path: Path, ext: str) -> None:
    with open(path, "rb") as f:
        head = f.read(16)
    if ext == "pdf" and not head.startswith(b"%PDF"):
        raise UploadError("Файл не похож на PDF. Проверьте, что он не повреждён.")
    if ext in OFFICE and not head.startswith(b"PK"):
        raise UploadError(
            "Файл не похож на документ %s. Старые форматы (.doc, .xls, .ppt) и защищённые паролем файлы "
            "не поддерживаются — сохраните документ заново в формате %s." % (ext.upper(), ext.upper()))
    if ext in ("mp4", "m4v") and head[4:8] != b"ftyp":
        raise UploadError("Файл не похож на видео MP4. Сохраните видео в формате MP4 (H.264).")
    if ext == "webm" and not head.startswith(b"\x1a\x45\xdf\xa3"):
        raise UploadError("Файл не похож на видео WebM.")


def abs_path(f: StoredFile) -> Path:
    return config.FILES_DIR / f.path


def store_stream(db: Session, stream: BinaryIO, original_name: str, uploaded_by: Optional[str],
                 kind: str = "source", convert: bool = True) -> StoredFile:
    """Сохраняет файл, считает SHA-256 и размер. Для DOCX/XLSX/PPTX сразу строит читаемую PDF-копию."""
    ext = ext_of(original_name)
    if ext not in ALLOWED:
        raise UploadError(
            "Формат .%s не поддерживается. Можно загрузить PDF, MP4, DOCX, XLSX или PPTX." % (ext or "?"))
    config.ensure_dirs()
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    sha = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(dir=str(config.FILES_DIR), suffix=".upload")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UploadError("Файл больше %d МБ. Сожмите видео или разделите материал." % config.MAX_UPLOAD_MB)
                sha.update(chunk)
                out.write(chunk)
        if size == 0:
            raise UploadError("Файл пустой.")
        tmp = Path(tmp_name)
        _check_signature(tmp, ext)
        digest = sha.hexdigest()
        rel = Path(digest[:2]) / ("%s.%s" % (digest, ext))
        dest = config.FILES_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            tmp.unlink()
        else:
            os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    row = StoredFile(
        id=new_id(), sha256=digest, size=size, mime=ALLOWED[ext][0], ext=ext,
        original_name=Path(original_name).name[:300], path=rel.as_posix(), kind=kind, uploaded_by=uploaded_by,
    )
    db.add(row)
    db.flush()
    if convert and ext in OFFICE:
        convert_file(db, row)
    return row


def store_path(db: Session, src: Path, original_name: Optional[str] = None, uploaded_by: Optional[str] = None,
               kind: str = "source", convert: bool = True) -> StoredFile:
    with open(src, "rb") as f:
        return store_stream(db, f, original_name or src.name, uploaded_by, kind=kind, convert=convert)


def store_bytes_as(db: Session, data: bytes, name: str, mime: str, kind: str, uploaded_by: Optional[str] = None) -> StoredFile:
    """Сохраняет сгенерированный сервером файл (сертификат) без проверки формата загрузки."""
    config.ensure_dirs()
    digest = hashlib.sha256(data).hexdigest()
    ext = ext_of(name) or "bin"
    rel = Path(digest[:2]) / ("%s.%s" % (digest, ext))
    dest = config.FILES_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(".tmp-%s" % new_id()[:8])
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    row = StoredFile(id=new_id(), sha256=digest, size=len(data), mime=mime, ext=ext, original_name=name,
                     path=rel.as_posix(), kind=kind, uploaded_by=uploaded_by)
    db.add(row)
    db.flush()
    return row


def convert_file(db: Session, row: StoredFile) -> None:
    """Строит PDF-копию Office-файла. Ошибка не прерывает загрузку: она сохраняется и видна методисту."""
    from .convert import convert_office_to_pdf  # импорт здесь: модуль тянет тяжёлые библиотеки

    src = abs_path(row)
    work = Path(tempfile.mkdtemp(prefix="lmsconv-"))
    try:
        out = work / "copy.pdf"
        try:
            result = convert_office_to_pdf(src, out, row.ext)
        except Exception as exc:  # конвертер обязан не падать, но страхуемся
            row.conversion_status = "error"
            row.conversion_error = "Не удалось построить PDF-копию: %s" % exc
            return
        row.converter = result.converter
        row.conversion_warnings = list(result.warnings or [])
        if not result.ok or not out.exists():
            row.conversion_status = "error"
            row.conversion_error = result.error or "Не удалось построить PDF-копию."
            row.converted_file_id = None
            return
        with open(out, "rb") as f:
            pdf = store_stream(db, f, Path(row.original_name).stem + ".pdf", row.uploaded_by,
                               kind="converted", convert=False)
        pdf.pages = result.pages
        row.pages = result.pages
        row.converted_file_id = pdf.id
        row.conversion_status = "ok"
        row.conversion_error = None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def file_info(f: Optional[StoredFile]) -> Optional[dict]:
    if not f:
        return None
    return {
        "id": f.id, "name": f.original_name, "size": f.size, "mime": f.mime, "ext": f.ext, "sha256": f.sha256,
        "pages": f.pages, "conversion_status": f.conversion_status, "conversion_error": f.conversion_error,
        "conversion_warnings": f.conversion_warnings or [], "converter": f.converter,
        "converted_file_id": f.converted_file_id,
    }
