"""Загрузка и выдача файлов. Доступ проверяется сервером для каждой роли."""
from __future__ import annotations

from typing import Iterator, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Assignment, Block, Certificate, LibraryItem, StoredFile, User
from ..security import Principal, audit, can_view_user, current_principal, require_methodist
from ..services.storage import UploadError, abs_path, convert_file, file_info, store_stream

router = APIRouter(prefix="/api", tags=["Файлы"])


def _with_views(db: Session, ids: set[str]) -> set[str]:
    out = set(ids)
    if ids:
        for f in db.query(StoredFile).filter(StoredFile.id.in_(ids)).all():
            if f.converted_file_id:
                out.add(f.converted_file_id)
    return out


def accessible_file_ids(db: Session, user: User) -> set[str]:
    """Файлы, которые сотрудник вправе скачать: материалы назначенных ему редакций и библиотека."""
    version_ids = {a.version_id for a in db.query(Assignment).filter(
        Assignment.user_id == user.id, Assignment.status != "cancelled").all()}
    ids: set[str] = set()
    if version_ids:
        for b in db.query(Block).filter(Block.version_id.in_(version_ids)).all():
            data = b.data or {}
            for key in ("file_id", "alt_file_id"):
                if data.get(key):
                    ids.add(data[key])
    for item in db.query(LibraryItem).filter(LibraryItem.is_active.is_(True)).all():
        ids.add(item.file_id)
    return _with_views(db, ids)


def check_file_access(db: Session, p: Principal, f: StoredFile) -> None:
    if p.is_methodist:
        return
    if f.kind == "certificate":
        cert = db.query(Certificate).filter(Certificate.file_id == f.id).first()
        owner = db.get(User, cert.user_id) if cert else None
        if owner and can_view_user(db, p, owner):
            return
        raise HTTPException(403, detail={"code": "forbidden", "message": "Документ другого сотрудника недоступен"})
    if f.id in accessible_file_ids(db, p.user):
        return
    raise HTTPException(403, detail={"code": "forbidden", "message": "Файл не входит в ваши назначения"})


def _iter_file(path, start: int, end: int, chunk: int = 256 * 1024) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        fh.seek(start)
        left = end - start + 1
        while left > 0:
            data = fh.read(min(chunk, left))
            if not data:
                break
            left -= len(data)
            yield data


def serve_file(f: StoredFile, request: Request, download: bool = False):
    """Отдаёт файл с поддержкой докачки по диапазонам (Range) — нужна для возобновления загрузки пакета."""
    path = abs_path(f)
    if not path.exists():
        raise HTTPException(410, detail={"message": "Файл отсутствует в хранилище"})
    size = path.stat().st_size
    disposition = "attachment" if download else "inline"
    ascii_name = f.original_name.encode("ascii", "ignore").decode().strip() or ("file." + f.ext)
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": '"%s"' % f.sha256,
        "Cache-Control": "private, no-store",
        "Content-Disposition": "%s; filename=\"%s\"; filename*=UTF-8''%s" % (disposition, ascii_name.replace('"', ""), quote(f.original_name)),
        "X-Content-Type-Options": "nosniff",
    }
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        try:
            a, b = rng[6:].split(",")[0].strip().split("-", 1)
            if a == "":
                start, end = max(0, size - int(b)), size - 1
            else:
                start, end = int(a), int(b) if b else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                raise ValueError
        except ValueError:
            return Response(status_code=416, headers={"Content-Range": "bytes */%d" % size})
        headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(_iter_file(path, start, end), status_code=206, media_type=f.mime, headers=headers)
    headers["Content-Length"] = str(size)
    return StreamingResponse(_iter_file(path, 0, size - 1), media_type=f.mime, headers=headers)


@router.post("/files", summary="Загрузить материал: PDF, MP4, DOCX, XLSX, PPTX")
def upload_file(request: Request, file: UploadFile = File(...), p: Principal = Depends(require_methodist),
                db: Session = Depends(get_db)) -> dict:
    try:
        row = store_stream(db, file.file, file.filename or "file", p.user.id)
    except UploadError as exc:
        db.rollback()
        raise HTTPException(400, detail={"code": "bad_file", "message": str(exc)})
    audit(db, request, p.user.id, "file_upload", "file", row.id, name=row.original_name, size=row.size,
          conversion=row.conversion_status)
    db.commit()
    out = file_info(row)
    if row.converted_file_id:
        out["view_file"] = file_info(db.get(StoredFile, row.converted_file_id))
    return out


@router.post("/files/{file_id}/reconvert", summary="Повторить построение PDF-копии")
def reconvert(file_id: str, p: Principal = Depends(require_methodist), db: Session = Depends(get_db)) -> dict:
    f = db.get(StoredFile, file_id)
    if not f:
        raise HTTPException(404, detail={"message": "Файл не найден"})
    convert_file(db, f)
    db.commit()
    return file_info(f)


@router.get("/files/{file_id}", summary="Сведения о файле")
def file_meta(file_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    f = db.get(StoredFile, file_id)
    if not f:
        raise HTTPException(404, detail={"message": "Файл не найден"})
    check_file_access(db, p, f)
    return file_info(f)


@router.get("/files/{file_id}/content", summary="Содержимое файла (с поддержкой докачки)")
def file_content(file_id: str, request: Request, download: bool = False,
                 p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    f = db.get(StoredFile, file_id)
    if not f:
        raise HTTPException(404, detail={"message": "Файл не найден"})
    check_file_access(db, p, f)
    return serve_file(f, request, download)
