from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
import io
import tempfile

from ps2icon.icon_sys import parse_icon_sys

from .common import SaveArchive
from .psu import read_psu


class MemoryCardBackendUnavailable(RuntimeError):
    pass


class MemoryCardError(RuntimeError):
    pass


@dataclass(slots=True)
class CardSave:
    dirname: str
    title: str
    size: int
    mode: int


@dataclass(slots=True)
class CardInfo:
    path: Path
    size_bytes: int
    free_bytes: int
    saves: list[CardSave]


def _load_backend():
    try:
        from mymcplusplus import mymc, ps2mc  # type: ignore
        from mymcplusplus.ps2mc_dir import DF_EXISTS, mode_is_dir  # type: ignore
    except ImportError as exc:
        raise MemoryCardBackendUnavailable(
            "Memory-card support needs mymc++. Run: pip install mymcplusplus"
        ) from exc
    return mymc, ps2mc, DF_EXISTS, mode_is_dir


def backend_available() -> bool:
    try:
        _load_backend()
        return True
    except MemoryCardBackendUnavailable:
        return False


def _run_mymc(card_path: str | Path, *args: str) -> str:
    mymc, _ps2mc, _df_exists, _mode_is_dir = _load_backend()
    stdout = io.StringIO()
    stderr = io.StringIO()
    argv = ["mymcplusplus", str(card_path), *map(str, args)]
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = mymc.main(argv)
    except SystemExit as exc:
        result = int(exc.code or 0)
    output = (stdout.getvalue() + stderr.getvalue()).strip()
    if result not in (None, 0):
        raise MemoryCardError(output or f"mymc++ failed with exit code {result}")
    return output


def inspect_memory_card(path: str | Path, *, ignore_ecc: bool = False) -> CardInfo:
    _mymc, ps2mc, DF_EXISTS, mode_is_dir = _load_backend()
    card_path = Path(path)
    mode = "rb"
    fh = card_path.open(mode)
    mc = None
    try:
        mc = ps2mc.ps2mc(fh, ignore_ecc)
        saves: list[CardSave] = []
        directory = mc.dir_open("/")
        try:
            entries = list(directory)[2:]
        finally:
            directory.close()

        for ent in entries:
            mode_bits = int(ent[0])
            if not (mode_bits & DF_EXISTS) or not mode_is_dir(mode_bits):
                continue
            raw_name = ent[8]
            dirname = raw_name.decode("ascii", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
            title = dirname
            size = 0
            try:
                mc.chdir("/" + dirname)
                size = int(mc.dir_size("."))
                raw_icon_sys = mc.get_icon_sys(".")
                if raw_icon_sys:
                    try:
                        parsed = parse_icon_sys(raw_icon_sys)
                        if parsed.title:
                            title = parsed.title.replace("\n", " / ")
                    except Exception:
                        pass
            finally:
                try:
                    mc.chdir("/")
                except Exception:
                    pass
            saves.append(CardSave(dirname=dirname, title=title, size=size, mode=mode_bits))

        return CardInfo(
            path=card_path,
            size_bytes=card_path.stat().st_size,
            free_bytes=int(mc.get_free_space()),
            saves=saves,
        )
    except Exception as exc:
        if isinstance(exc, (MemoryCardBackendUnavailable, MemoryCardError)):
            raise
        raise MemoryCardError(str(exc)) from exc
    finally:
        if mc is not None:
            try:
                mc.close()
            except Exception:
                pass
        fh.close()


def export_save_as_psu(card_path: str | Path, dirname: str, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_mymc(card_path, "export", "-f", "-o", str(output_path), dirname)


def load_save_archive(card_path: str | Path, dirname: str) -> SaveArchive:
    with tempfile.TemporaryDirectory(prefix="ps2save_") as tmp:
        temp_psu = Path(tmp) / "save.psu"
        export_save_as_psu(card_path, dirname, temp_psu)
        archive = read_psu(temp_psu)
        archive.source_format = "MEMORY_CARD"
        return archive


def import_save(card_path: str | Path, save_path: str | Path) -> None:
    _run_mymc(card_path, "import", str(save_path))


def delete_save(card_path: str | Path, dirname: str) -> None:
    _run_mymc(card_path, "delete", dirname)


def validate_card(card_path: str | Path) -> str:
    return _run_mymc(card_path, "check") or "No errors found."


def create_memory_card(
    path: str | Path,
    *,
    size_mb: int = 8,
    no_ecc: bool | None = None,
    overwrite: bool = False,
) -> None:
    path = Path(path)
    if size_mb not in (8, 16, 32, 64):
        raise ValueError("Supported card sizes are 8, 16, 32 and 64 MB")
    if no_ecc is None:
        no_ecc = path.suffix.lower() == ".mc2"
    clusters = 8192 * size_mb // 8
    args = ["format", "-c", str(clusters)]
    if overwrite:
        args.append("-f")
    if no_ecc:
        args.append("-e")
    _run_mymc(path, *args)


def replace_save_from_archive(card_path: str | Path, dirname: str, archive: SaveArchive) -> None:
    """Replace one save on a PS2 card using a PSU round-trip.

    The caller is responsible for creating a backup before calling this function.
    """
    from .psu import write_psu

    with tempfile.TemporaryDirectory(prefix="ps2save_replace_") as tmp:
        temp_psu = Path(tmp) / f"{archive.dirname or dirname}.psu"
        write_psu(archive, temp_psu)
        delete_save(card_path, dirname)
        try:
            import_save(card_path, temp_psu)
        except Exception:
            # If the import fails after deletion, let the caller restore its backup.
            raise


def copy_save_between_cards(
    source_card: str | Path,
    dirname: str,
    destination_card: str | Path,
) -> None:
    """Copy a single PS2 save between two memory cards via a temporary PSU."""
    with tempfile.TemporaryDirectory(prefix="ps2save_copy_") as tmp:
        temp_psu = Path(tmp) / f"{dirname}.psu"
        export_save_as_psu(source_card, dirname, temp_psu)
        import_save(destination_card, temp_psu)


def migrate_card_saves(source_card: str | Path, destination_card: str | Path) -> int:
    """Copy all user-visible save directories from one PS2 card to another."""
    info = inspect_memory_card(source_card)
    copied = 0
    for save in info.saves:
        copy_save_between_cards(source_card, save.dirname, destination_card)
        copied += 1
    return copied


def export_save_as_max(card_path: str | Path, dirname: str, output_path: str | Path) -> None:
    """Export one PS2 save as MAX Drive format through mymc++."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_mymc(card_path, "export", "-m", "-f", "-o", str(output_path), dirname)


def write_archive_as_max(archive: SaveArchive, output_path: str | Path) -> None:
    """Convert an in-memory PS2 save archive to MAX via a temporary memory card."""
    from .psu import write_psu

    with tempfile.TemporaryDirectory(prefix="ps2save_max_") as tmp:
        tmp_root = Path(tmp)
        card = tmp_root / "temp.ps2"
        psu = tmp_root / f"{archive.dirname or 'SAVE'}.psu"
        create_memory_card(card, size_mb=8, overwrite=True)
        write_psu(archive, psu)
        import_save(card, psu)
        info = inspect_memory_card(card)
        if not info.saves:
            raise MemoryCardError("Temporary MAX conversion card contains no imported save")
        # Prefer the requested directory, but accept the imported save's normalized name.
        dirname = archive.dirname
        if not any(s.dirname.casefold() == dirname.casefold() for s in info.saves):
            dirname = info.saves[0].dirname
        export_save_as_max(card, dirname, output_path)


def load_external_ps2_save(path: str | Path) -> SaveArchive:
    """Load PSV/PSU/MAX/CBS/XPS/SPS as a normalized PS2 SaveArchive.

    PSV and PSU are parsed locally. Legacy formats are normalized through a temporary
    mymc++ memory card, which already supports importing those formats.
    """
    from .detect import detect_path
    from .psv import read_psv

    source = Path(path)
    try:
        fmt = detect_path(source)
    except Exception:
        fmt = ""
    if fmt == "PSV":
        return read_psv(source)
    if fmt == "PSU":
        return read_psu(source)
    if source.suffix.lower() not in {".max", ".cbs", ".xps", ".sps"}:
        raise MemoryCardError("Choose a PS2 PSV, PSU, MAX, CBS, XPS or SPS save")

    with tempfile.TemporaryDirectory(prefix="ps2save_import_") as tmp:
        card = Path(tmp) / "temp.ps2"
        create_memory_card(card, size_mb=8, overwrite=True)
        import_save(card, source)
        info = inspect_memory_card(card)
        if len(info.saves) != 1:
            raise MemoryCardError(f"Expected one imported save, found {len(info.saves)}")
        return load_save_archive(card, info.saves[0].dirname)


def rebuild_card_to_new_image(
    source_card: str | Path,
    destination_card: str | Path,
    *,
    size_mb: int = 8,
    no_ecc: bool | None = None,
) -> tuple[int, list[str]]:
    """Experimental non-destructive repair: rebuild readable saves into a fresh card.

    The source is never modified. Saves that cannot be exported/imported are skipped
    and returned as human-readable errors.
    """
    source = Path(source_card)
    destination = Path(destination_card)
    if destination.exists():
        destination.unlink()
    create_memory_card(destination, size_mb=size_mb, no_ecc=no_ecc, overwrite=True)
    info = inspect_memory_card(source)
    copied = 0
    errors: list[str] = []
    for save in info.saves:
        try:
            copy_save_between_cards(source, save.dirname, destination)
            copied += 1
        except Exception as exc:
            errors.append(f"{save.dirname}: {exc}")
    return copied, errors
