from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Annotated
import copy
import json
import shutil
import sys
import tempfile
import zipfile

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

CORE = Path(__file__).resolve().parents[1] / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from formats import (  # noqa: E402
    PS1MemoryCard,
    PS1Save,
    detect_path,
    create_ps1_memory_card,
    delete_save_from_card,
    extract_icon_frames_rgba,
    read_ps1_card,
    read_ps1_single,
    read_psu,
    read_psv,
    save_ps1_card,
    write_ps1_single,
    write_psu,
    write_psv,
)
from formats.common import SaveArchive, SaveEntry  # noqa: E402
from formats.memorycard import (  # noqa: E402
    MemoryCardBackendUnavailable,
    copy_save_between_cards,
    create_memory_card,
    export_save_as_max,
    export_save_as_psu,
    inspect_memory_card,
    import_save,
    delete_save,
    load_external_ps2_save,
    load_save_archive,
    replace_save_from_archive,
    rebuild_card_to_new_image,
    validate_card,
    write_archive_as_max,
)
from ps1icon import render_ps1_icon  # noqa: E402
from ps2icon import parse_icon_model, parse_icon_sys, render_icon, render_texture  # noqa: E402
from ps2icon.icon_sys import update_icon_sys_title  # noqa: E402

from .storage import create_slot, get_file, max_upload_bytes, result_path


APP_VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()

app = FastAPI(title="PS2 Save Tool Web API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value else None


def _archive_title(archive: SaveArchive) -> str:
    entry = archive.get("icon.sys")
    if not entry:
        return archive.dirname
    try:
        return parse_icon_sys(entry.data).title.replace("\n", " / ") or archive.dirname
    except Exception:
        return archive.dirname


def _archive_json(archive: SaveArchive, fmt: str) -> dict:
    return {
        "kind": "ps2-save",
        "format": fmt,
        "dirname": archive.dirname,
        "title": _archive_title(archive),
        "size": archive.total_size,
        "created": _iso(archive.created),
        "modified": _iso(archive.modified),
        "signatureValid": archive.signature_valid,
        "warnings": archive.warnings,
        "files": [
            {
                "name": e.name,
                "size": e.size,
                "mode": f"0x{e.mode:04X}",
                "attr": f"0x{e.attr:08X}",
                "created": _iso(e.created),
                "modified": _iso(e.modified),
            }
            for e in archive.entries
        ],
        "convertTargets": [t for t in ("PSV", "PSU", "MAX") if t != fmt],
    }


def _user_backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".auto-backup")


def _create_user_backup(path: Path, enabled: bool) -> None:
    if enabled:
        shutil.copy2(path, _user_backup_path(path))


def _ps1_save_json(save: PS1Save, fmt: str) -> dict:
    return {
        "kind": "ps1-save",
        "format": fmt.replace("PS1_", ""),
        "filename": save.filename,
        "title": save.title or save.filename,
        "region": save.region,
        "productCode": save.product_code,
        "identifier": save.identifier,
        "size": save.payload_size,
        "blocks": save.blocks,
        "signatureValid": save.signature_valid,
        "warnings": save.warnings,
        "convertTargets": [t for t in ("PSV", "MCS", "PSX", "RAW") if t != fmt.replace("PS1_", "")],
    }


def _inspect(path: Path) -> dict:
    fmt = detect_path(path)
    if fmt == "PSV":
        return _archive_json(read_psv(path), fmt)
    if fmt == "PSU":
        return _archive_json(read_psu(path), fmt)
    if fmt in {"PS1_PSV", "PS1_MCS", "PS1_PSX", "PS1_RAW"}:
        return _ps1_save_json(read_ps1_single(path), fmt)
    if fmt == "MEMORY_CARD":
        info = inspect_memory_card(path)
        return {
            "kind": "ps2-card",
            "format": path.suffix.lstrip(".").upper() or "PS2",
            "size": info.size_bytes,
            "free": info.free_bytes,
            "used": max(0, info.size_bytes - info.free_bytes),
            "warnings": [],
            "hasBackup": _user_backup_path(path).is_file(),
            "saves": [asdict(s) for s in info.saves],
        }
    if fmt == "PS1_CARD":
        card = read_ps1_card(path)
        return {
            "kind": "ps1-card",
            "format": card.container_format,
            "size": card.size_bytes,
            "freeBlocks": card.free_blocks,
            "warnings": card.warnings,
            "signatureValid": card.vmp_signature_valid,
            "saves": [
                {
                    "slot": s.start_slot,
                    "filename": s.filename,
                    "title": s.title or s.filename,
                    "region": s.region,
                    "productCode": s.product_code,
                    "size": s.payload_size,
                    "blocks": s.blocks,
                }
                for s in card.saves
            ],
        }
    raise ValueError(f"Unsupported format: {fmt}")


def _load_ps2_archive(path: Path) -> SaveArchive:
    fmt = detect_path(path)
    if fmt == "PSV":
        return read_psv(path)
    if fmt == "PSU":
        return read_psu(path)
    raise ValueError("This action requires a PS2 PSV or PSU save")


def _find_ps2_icon(archive: SaveArchive):
    sys_entry = archive.get("icon.sys")
    icon_sys = None
    if sys_entry:
        try:
            icon_sys = parse_icon_sys(sys_entry.data)
        except Exception:
            pass
    names: list[str] = []
    if icon_sys:
        names.extend([icon_sys.icon_name, icon_sys.copy_icon_name, icon_sys.delete_icon_name])
    names.extend(e.name for e in archive.entries if e.name.lower().endswith((".ico", ".icn")))
    seen = set()
    for name in names:
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        entry = archive.get(name)
        if not entry:
            continue
        try:
            return icon_sys, parse_icon_model(entry.data)
        except Exception:
            continue
    return icon_sys, None


def _png_response(image) -> Response:
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.get("/api/health")
def health():
    return {"ok": True, "version": APP_VERSION}


@app.post("/api/files/upload")
async def upload(file: Annotated[UploadFile, File()]):
    slot = create_slot(file.filename or "upload.bin")
    total = 0
    try:
        with slot.path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_upload_bytes():
                    raise HTTPException(413, "File is larger than the configured upload limit")
                out.write(chunk)
        details = _inspect(slot.path)
        return {"id": slot.file_id, "name": slot.original_name, "details": details}
    except HTTPException:
        shutil.rmtree(slot.path.parent, ignore_errors=True)
        raise
    except MemoryCardBackendUnavailable as exc:
        shutil.rmtree(slot.path.parent, ignore_errors=True)
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        shutil.rmtree(slot.path.parent, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/{file_id}/preview")
def preview(file_id: str, mode: str = "rendered", save: str | None = None, slot: int | None = None):
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt in {"PSV", "PSU"}:
            archive = _load_ps2_archive(stored.path)
            icon_sys, model = _find_ps2_icon(archive)
            if not model:
                raise HTTPException(404, "No renderable PS2 icon model found")
            return _png_response(render_texture(model) if mode == "texture" else render_icon(icon_sys, model, 0.0))
        if fmt == "MEMORY_CARD":
            if not save:
                raise HTTPException(400, "Choose a save first")
            archive = load_save_archive(stored.path, save)
            icon_sys, model = _find_ps2_icon(archive)
            if not model:
                raise HTTPException(404, "No renderable PS2 icon model found")
            return _png_response(render_texture(model) if mode == "texture" else render_icon(icon_sys, model, 0.0))
        if fmt in {"PS1_PSV", "PS1_MCS", "PS1_PSX", "PS1_RAW"}:
            ps1 = read_ps1_single(stored.path)
            frames = extract_icon_frames_rgba(ps1)
            return _png_response(render_ps1_icon(frames[0] if frames else None))
        if fmt == "PS1_CARD":
            card = read_ps1_card(stored.path)
            chosen = next((s for s in card.saves if s.start_slot == slot), None)
            if chosen is None:
                raise HTTPException(404, "PS1 save not found")
            frames = extract_icon_frames_rgba(chosen)
            return _png_response(render_ps1_icon(frames[0] if frames else None))
        raise HTTPException(400, "This format has no preview")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/convert")
def convert(file_id: str, target: Annotated[str, Form()], save: Annotated[str | None, Form()] = None):
    target = target.upper()
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt in {"PSV", "PSU"}:
            archive = _load_ps2_archive(stored.path)
            if target == fmt:
                raise HTTPException(400, "Choose a different output format")
            out = result_path(file_id, f"{archive.dirname}.{target.lower()}")
            if target == "PSV":
                write_psv(archive, out)
            elif target == "PSU":
                write_psu(archive, out)
            elif target == "MAX":
                write_archive_as_max(archive, out)
            else:
                raise HTTPException(400, "Supported PS2 targets: PSV, PSU, MAX")
            return FileResponse(out, filename=out.name.removeprefix("result-"), media_type="application/octet-stream")
        if fmt == "MEMORY_CARD":
            if not save:
                raise HTTPException(400, "Choose a save first")
            ext = target.lower()
            out = result_path(file_id, f"{save}.{ext}")
            if target == "PSU":
                export_save_as_psu(stored.path, save, out)
            elif target == "PSV":
                archive = load_save_archive(stored.path, save)
                write_psv(archive, out)
            elif target == "MAX":
                export_save_as_max(stored.path, save, out)
            else:
                raise HTTPException(400, "Supported PS2 card export targets: PSV, PSU, MAX")
            return FileResponse(out, filename=out.name.removeprefix("result-"), media_type="application/octet-stream")
        if fmt in {"PS1_PSV", "PS1_MCS", "PS1_PSX", "PS1_RAW"}:
            ps1 = read_ps1_single(stored.path)
            suffix = {"PSV": ".psv", "MCS": ".mcs", "PSX": ".psx", "RAW": ".raw"}.get(target)
            if not suffix:
                raise HTTPException(400, "Supported PS1 targets: PSV, MCS, PSX, RAW")
            out = result_path(file_id, f"{Path(ps1.filename).stem or 'save'}{suffix}")
            write_ps1_single(ps1, out, target)
            return FileResponse(out, filename=out.name.removeprefix("result-"), media_type="application/octet-stream")
        if fmt == "PS1_CARD":
            if save is None:
                raise HTTPException(400, "Choose a PS1 save first")
            card = read_ps1_card(stored.path)
            try:
                slot_index = int(save)
            except ValueError as exc:
                raise HTTPException(400, "Invalid PS1 slot") from exc
            ps1 = next((s for s in card.saves if s.start_slot == slot_index), None)
            if ps1 is None:
                raise HTTPException(404, "PS1 save not found")
            suffix = {"PSV": ".psv", "MCS": ".mcs", "PSX": ".psx", "RAW": ".raw"}.get(target)
            if not suffix:
                raise HTTPException(400, "Supported PS1 targets: PSV, MCS, PSX, RAW")
            out = result_path(file_id, f"{Path(ps1.filename).stem or 'save'}{suffix}")
            write_ps1_single(ps1, out, target)
            return FileResponse(out, filename=out.name.removeprefix("result-"), media_type="application/octet-stream")
        raise HTTPException(400, "This file cannot be converted with this endpoint")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/{file_id}/export-folder")
def export_folder(file_id: str, save: str | None = None):
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt in {"PSV", "PSU"}:
            archive = _load_ps2_archive(stored.path)
        elif fmt == "MEMORY_CARD" and save:
            archive = load_save_archive(stored.path, save)
        else:
            raise HTTPException(400, "Folder export is available for PS2 saves")
        out = result_path(file_id, f"{archive.dirname}_files.zip")
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for entry in archive.entries:
                safe = Path(entry.name).name
                zf.writestr(f"{archive.dirname}/{safe}", entry.data)
        return FileResponse(out, filename=out.name.removeprefix("result-"), media_type="application/zip")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/{file_id}/validate")
def validate(file_id: str):
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt == "MEMORY_CARD":
            return {"message": validate_card(stored.path)}
        details = _inspect(stored.path)
        warnings = details.get("warnings", [])
        return {"message": "No structural warnings detected." if not warnings else "\n".join(warnings)}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/cards/create")
def create_card(size_mb: Annotated[int, Form()] = 8, format: Annotated[str, Form()] = "ps2"):
    fmt = format.lower()
    if fmt not in {"ps2", "mc2", "ps1"}:
        raise HTTPException(400, "Card format must be ps2, mc2 or ps1")
    suffix = "mcr" if fmt == "ps1" else fmt
    slot = create_slot(f"New-Memory-Card.{suffix}")
    try:
        if fmt == "ps1":
            create_ps1_memory_card(slot.path, overwrite=True)
        else:
            create_memory_card(slot.path, size_mb=size_mb, no_ecc=(fmt == "mc2"), overwrite=True)
        return FileResponse(slot.path, filename=slot.original_name, media_type="application/octet-stream")
    except Exception as exc:
        shutil.rmtree(slot.path.parent, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc


async def _save_upload_temp(upload: UploadFile, folder: Path, fallback: str) -> Path:
    name = Path(upload.filename or fallback).name
    path = folder / name
    total = 0
    with path.open("wb") as out:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > max_upload_bytes():
                raise HTTPException(413, "File is larger than the configured upload limit")
            out.write(chunk)
    return path


@app.post("/api/cards/merge")
async def merge_cards(
    source: Annotated[UploadFile, File()],
    destination: Annotated[UploadFile, File()],
):
    """Copy non-duplicate saves from source into a copy of destination and return it."""
    with tempfile.TemporaryDirectory(prefix="ps2web_merge_") as tmp:
        folder = Path(tmp)
        src = await _save_upload_temp(source, folder, "source.ps2")
        dst = await _save_upload_temp(destination, folder, "destination.ps2")
        try:
            if detect_path(src) != "MEMORY_CARD" or detect_path(dst) != "MEMORY_CARD":
                raise HTTPException(400, "Merge Cards accepts two PS2 memory-card images")
            out = folder / f"Merged-{dst.name}"
            shutil.copy2(dst, out)
            src_info = inspect_memory_card(src)
            dst_info = inspect_memory_card(out)
            existing = {s.dirname.casefold() for s in dst_info.saves}
            copied = 0
            skipped = []
            for save in src_info.saves:
                if save.dirname.casefold() in existing:
                    skipped.append(save.dirname)
                    continue
                copy_save_between_cards(src, save.dirname, out)
                existing.add(save.dirname.casefold())
                copied += 1
            payload = out.read_bytes()
            headers = {
                "Content-Disposition": f'attachment; filename="{out.name}"',
                "X-PS2-Merged": str(copied),
                "X-PS2-Skipped": str(len(skipped)),
            }
            return Response(payload, media_type="application/octet-stream", headers=headers)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc


@app.post("/api/ps2/inject")
async def inject_ps2_psv(
    target: Annotated[UploadFile, File()],
    modified: Annotated[UploadFile, File()],
):
    """Create a newly signed PS2 PSV using a modified compatible save payload."""
    with tempfile.TemporaryDirectory(prefix="ps2web_inject_") as tmp:
        folder = Path(tmp)
        target_path = await _save_upload_temp(target, folder, "target.psv")
        modified_path = await _save_upload_temp(modified, folder, "modified.psu")
        try:
            if detect_path(target_path) != "PSV":
                raise HTTPException(400, "The target must be a PS2 PSV file")
            original = read_psv(target_path)
            changed = load_external_ps2_save(modified_path)
            if original.dirname.casefold() != changed.dirname.casefold():
                raise HTTPException(400, f"Save ID mismatch: {original.dirname} vs {changed.dirname}")
            if changed.total_size <= 0 or not changed.entries:
                raise HTTPException(400, "Modified save contains no payload files")
            injected = copy.deepcopy(changed)
            injected.dirname = original.dirname
            injected.mode = original.mode
            injected.created = original.created
            injected.modified = original.modified
            injected.attr = original.attr
            out = folder / f"Injected-{Path(target_path.name).stem}.psv"
            write_psv(injected, out)
            verified = read_psv(out)
            if verified.signature_valid is False or verified.dirname.casefold() != original.dirname.casefold():
                raise HTTPException(500, "Generated PSV did not pass post-write validation")
            return Response(
                out.read_bytes(),
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{out.name}"'},
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc


def _selected_ps2_archive(stored_path: Path, save: str | None = None) -> SaveArchive:
    fmt = detect_path(stored_path)
    if fmt in {"PSV", "PSU"}:
        return _load_ps2_archive(stored_path)
    if fmt == "MEMORY_CARD":
        if not save:
            raise HTTPException(400, "Choose a PS2 save first")
        return load_save_archive(stored_path, save)
    raise HTTPException(400, "This action requires a PS2 save or PS2 memory card")


def _archive_detail_json(archive: SaveArchive, *, parent: str = "PS2") -> dict:
    return {
        "kind": "ps2-card-save" if parent == "MEMORY_CARD" else "ps2-save",
        "format": parent,
        "dirname": archive.dirname,
        "title": _archive_title(archive),
        "size": archive.total_size,
        "created": _iso(archive.created),
        "modified": _iso(archive.modified),
        "fileCount": len(archive.entries),
        "files": [
            {
                "name": e.name,
                "size": e.size,
                "mode": f"0x{e.mode:04X}",
                "attr": f"0x{e.attr:08X}",
                "created": _iso(e.created),
                "modified": _iso(e.modified),
            }
            for e in archive.entries
        ],
    }


def _replace_archive_title(archive: SaveArchive, title: str) -> None:
    icon = archive.get("icon.sys")
    if icon is None:
        raise ValueError("This PS2 save has no icon.sys metadata file")
    icon.data = update_icon_sys_title(icon.data, title)


def _validate_dirname(dirname: str) -> str:
    dirname = dirname.strip()
    if not dirname:
        raise HTTPException(400, "Directory / Save ID cannot be empty")
    try:
        raw = dirname.encode("ascii")
    except UnicodeEncodeError as exc:
        raise HTTPException(400, "PS2 directory names must use ASCII characters") from exc
    if len(raw) > 31:
        raise HTTPException(400, "PS2 directory names are limited to 31 ASCII bytes")
    return dirname


def _card_backup(path: Path) -> Path:
    backup = path.with_name(path.name + ".web-backup")
    shutil.copy2(path, backup)
    return backup


def _restore_card_backup(path: Path, backup: Path) -> None:
    if backup.is_file():
        shutil.copy2(backup, path)
        backup.unlink(missing_ok=True)


@app.get("/api/files/{file_id}/save-details")
def save_details(file_id: str, save: str | None = None, slot: int | None = None):
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt in {"PSV", "PSU"}:
            return _archive_detail_json(_load_ps2_archive(stored.path), parent=fmt)
        if fmt == "MEMORY_CARD":
            if not save:
                raise HTTPException(400, "Choose a save first")
            return _archive_detail_json(load_save_archive(stored.path, save), parent="MEMORY_CARD")
        if fmt == "PS1_CARD":
            card = read_ps1_card(stored.path)
            chosen = next((s for s in card.saves if s.start_slot == slot), None)
            if chosen is None:
                raise HTTPException(404, "PS1 save not found")
            return _ps1_save_json(chosen, "PS1_CARD")
        raise HTTPException(400, "No selectable save details for this format")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/{file_id}/download")
def download_current(file_id: str):
    try:
        stored = get_file(file_id)
        return FileResponse(stored.path, filename=stored.original_name, media_type="application/octet-stream")
    except Exception as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/files/{file_id}/restore-backup")
def restore_latest_backup(file_id: str):
    try:
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Restore backup requires a PS2 memory card")
        backup = _user_backup_path(stored.path)
        if not backup.is_file():
            raise HTTPException(404, "No automatic backup is available for this card")
        shutil.copy2(backup, stored.path)
        return {"details": _inspect(stored.path)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/metadata")
def edit_metadata(
    file_id: str,
    dirname: Annotated[str, Form()],
    title: Annotated[str, Form()],
    save: Annotated[str | None, Form()] = None,
    auto_backup: Annotated[bool, Form()] = True,
):
    dirname = _validate_dirname(dirname)
    title = title.strip()
    if not title:
        raise HTTPException(400, "Browser title cannot be empty")
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt == "MEMORY_CARD":
            if not save:
                raise HTTPException(400, "Choose a save first")
            archive = load_save_archive(stored.path, save)
            _create_user_backup(stored.path, auto_backup)
            archive.dirname = dirname
            _replace_archive_title(archive, title)
            backup = _card_backup(stored.path)
            try:
                replace_save_from_archive(stored.path, save, archive)
                backup.unlink(missing_ok=True)
            except Exception:
                _restore_card_backup(stored.path, backup)
                raise
            return {"details": _inspect(stored.path), "selected": archive.dirname}
        if fmt in {"PSV", "PSU"}:
            archive = _load_ps2_archive(stored.path)
            archive.dirname = dirname
            _replace_archive_title(archive, title)
            if fmt == "PSV":
                write_psv(archive, stored.path)
                verify = read_psv(stored.path)
                if verify.signature_valid is not True:
                    raise ValueError("Rewritten PSV did not pass signature verification")
            else:
                write_psu(archive, stored.path)
            return {"details": _inspect(stored.path), "selected": archive.dirname}
        raise HTTPException(400, "Metadata editing is available for PS2 saves")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/delete-save")
def delete_card_save(file_id: str, save: Annotated[str, Form()], auto_backup: Annotated[bool, Form()] = True):
    try:
        stored = get_file(file_id)
        fmt = detect_path(stored.path)
        if fmt == "MEMORY_CARD":
            _create_user_backup(stored.path, auto_backup)
            backup = _card_backup(stored.path)
            try:
                delete_save(stored.path, save)
                backup.unlink(missing_ok=True)
            except Exception:
                _restore_card_backup(stored.path, backup)
                raise
            return {"details": _inspect(stored.path)}
        if fmt == "PS1_CARD":
            card = read_ps1_card(stored.path)
            try:
                slot = int(save)
            except ValueError as exc:
                raise HTTPException(400, "Invalid PS1 save slot") from exc
            delete_save_from_card(card, slot)
            save_ps1_card(card, stored.path, target_format=card.container_format)
            return {"details": _inspect(stored.path)}
        raise HTTPException(400, "Delete Save requires a memory card")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/files/extract")
def extract_card_files(
    file_id: str,
    names_json: Annotated[str, Form()],
    save: Annotated[str | None, Form()] = None,
):
    try:
        names = json.loads(names_json)
        if not isinstance(names, list) or not names:
            raise HTTPException(400, "Choose one or more files")
        stored = get_file(file_id)
        archive = _selected_ps2_archive(stored.path, save)
        entries = [e for e in archive.entries if e.name in names]
        if not entries:
            raise HTTPException(404, "Selected files were not found")
        if len(entries) == 1:
            e = entries[0]
            return Response(
                e.data,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{Path(e.name).name}"'},
            )
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for e in entries:
                zf.writestr(Path(e.name).name, e.data)
        return Response(
            buf.getvalue(), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{archive.dirname}-files.zip"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


def _commit_card_archive(stored_path: Path, save: str, archive: SaveArchive) -> None:
    backup = _card_backup(stored_path)
    try:
        replace_save_from_archive(stored_path, save, archive)
        backup.unlink(missing_ok=True)
    except Exception:
        _restore_card_backup(stored_path, backup)
        raise


@app.post("/api/files/{file_id}/files/replace")
async def replace_card_file(
    file_id: str,
    save: Annotated[str, Form()],
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    auto_backup: Annotated[bool, Form()] = True,
):
    try:
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Replace is available for files inside PS2 memory-card saves")
        archive = load_save_archive(stored.path, save)
        idx = next((i for i, e in enumerate(archive.entries) if e.name.casefold() == name.casefold()), None)
        if idx is None:
            raise HTTPException(404, "Save file not found")
        payload = await file.read()
        _create_user_backup(stored.path, auto_backup)
        if len(payload) > max_upload_bytes():
            raise HTTPException(413, "Replacement file is too large")
        archive.entries[idx].data = payload
        _commit_card_archive(stored.path, save, archive)
        return {"details": _inspect(stored.path), "saveDetails": _archive_detail_json(load_save_archive(stored.path, archive.dirname), parent="MEMORY_CARD")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/files/add")
async def add_card_file(
    file_id: str,
    save: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    auto_backup: Annotated[bool, Form()] = True,
):
    try:
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Add is available for files inside PS2 memory-card saves")
        archive = load_save_archive(stored.path, save)
        name = Path(file.filename or "file.bin").name
        if any(e.name.casefold() == name.casefold() for e in archive.entries):
            raise HTTPException(400, f"A file named '{name}' already exists in this save")
        payload = await file.read()
        _create_user_backup(stored.path, auto_backup)
        if len(payload) > max_upload_bytes():
            raise HTTPException(413, "Added file is too large")
        archive.entries.append(SaveEntry(name=name, data=payload))
        _commit_card_archive(stored.path, save, archive)
        return {"details": _inspect(stored.path), "saveDetails": _archive_detail_json(load_save_archive(stored.path, archive.dirname), parent="MEMORY_CARD")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/files/delete")
def delete_card_files(
    file_id: str,
    save: Annotated[str, Form()],
    names_json: Annotated[str, Form()],
    auto_backup: Annotated[bool, Form()] = True,
):
    try:
        names = json.loads(names_json)
        if not isinstance(names, list) or not names:
            raise HTTPException(400, "Choose one or more files")
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Delete is available for files inside PS2 memory-card saves")
        archive = load_save_archive(stored.path, save)
        _create_user_backup(stored.path, auto_backup)
        wanted = {str(n).casefold() for n in names}
        original_count = len(archive.entries)
        archive.entries = [e for e in archive.entries if e.name.casefold() not in wanted]
        if len(archive.entries) == original_count:
            raise HTTPException(404, "Selected save files were not found")
        _commit_card_archive(stored.path, save, archive)
        return {"details": _inspect(stored.path), "saveDetails": _archive_detail_json(load_save_archive(stored.path, archive.dirname), parent="MEMORY_CARD")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/files/{file_id}/card/import")
async def batch_import_card(file_id: str, files: Annotated[list[UploadFile], File()], auto_backup: Annotated[bool, Form()] = True):
    try:
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Batch import requires a PS2 memory card")
        _create_user_backup(stored.path, auto_backup)
        backup = _card_backup(stored.path)
        count = 0
        try:
            with tempfile.TemporaryDirectory(prefix="ps2web_import_") as tmp:
                folder = Path(tmp)
                for item in files:
                    temp_path = await _save_upload_temp(item, folder, f"save-{count}.psu")
                    # Convert supported external formats to a temporary PSU when needed.
                    try:
                        archive = load_external_ps2_save(temp_path)
                        psu_path = folder / f"normalized-{count}.psu"
                        write_psu(archive, psu_path)
                        import_save(stored.path, psu_path)
                    except Exception:
                        import_save(stored.path, temp_path)
                    count += 1
            backup.unlink(missing_ok=True)
        except Exception:
            _restore_card_backup(stored.path, backup)
            raise
        return {"details": _inspect(stored.path), "imported": count}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/{file_id}/batch-export")
def batch_export_card(file_id: str, target: str = "PSV"):
    target = target.upper()
    if target not in {"PSV", "PSU", "MAX"}:
        raise HTTPException(400, "Supported batch targets: PSV, PSU, MAX")
    try:
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Batch export requires a PS2 memory card")
        info = inspect_memory_card(stored.path)
        buf = BytesIO()
        with tempfile.TemporaryDirectory(prefix="ps2web_batch_") as tmp, zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            folder = Path(tmp)
            suffix = target.lower()
            for card_save in info.saves:
                out = folder / f"{card_save.dirname}.{suffix}"
                if target == "PSU":
                    export_save_as_psu(stored.path, card_save.dirname, out)
                elif target == "MAX":
                    export_save_as_max(stored.path, card_save.dirname, out)
                else:
                    write_psv(load_save_archive(stored.path, card_save.dirname), out)
                zf.write(out, out.name)
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="PS2-Saves-{target}.zip"'})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/files/{file_id}/repair")
def experimental_repair_card(file_id: str):
    try:
        stored = get_file(file_id)
        if detect_path(stored.path) != "MEMORY_CARD":
            raise HTTPException(400, "Experimental repair requires a PS2 memory card")
        info = inspect_memory_card(stored.path)
        size_mb = min((8, 16, 32, 64), key=lambda n: abs(info.size_bytes - n * 1024 * 1024))
        suffix = stored.path.suffix if stored.path.suffix.lower() in {".ps2", ".mc2"} else ".ps2"
        out = result_path(file_id, f"{Path(stored.original_name).stem}_repaired{suffix}")
        copied, errors = rebuild_card_to_new_image(
            stored.path,
            out,
            size_mb=size_mb,
            no_ecc=suffix.lower() == ".mc2",
        )
        headers = {
            "Content-Disposition": f'attachment; filename="{out.name.removeprefix("result-")}"',
            "X-PS2-Copied": str(copied),
            "X-PS2-Errors": str(len(errors)),
        }
        return FileResponse(out, filename=out.name.removeprefix("result-"), media_type="application/octet-stream", headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
