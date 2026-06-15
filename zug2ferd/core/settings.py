from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Optional


@dataclass
class AppSettings:
    validate_with_mustang: bool = True
    ustg_check_enabled: bool = True
    stationery_rel_path: Optional[str] = None
    stationery_apply_all_pages: bool = True
    branding_seller_name: Optional[str] = None
    branding_seller_tax_id: Optional[str] = None
    import_dir_path: Optional[str] = None
    export_dir_path: Optional[str] = None


class SettingsStore:
    def __init__(self, settings_path: Path, root_dir: Path) -> None:
        self._path = settings_path
        self._root = root_dir
        self._lock = Lock()
        self._settings = AppSettings()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def load(self) -> AppSettings:
        with self._lock:
            if not self._path.exists():
                self._settings = AppSettings()
                return self._settings

            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._settings = AppSettings()
                return self._settings

            self._settings = AppSettings(
                validate_with_mustang=bool(data.get("validate_with_mustang", True)),
                ustg_check_enabled=bool(data.get("ustg_check_enabled", True)),
                stationery_rel_path=data.get("stationery_rel_path") or None,
                stationery_apply_all_pages=bool(data.get("stationery_apply_all_pages", True)),
                branding_seller_name=data.get("branding_seller_name") or None,
                branding_seller_tax_id=data.get("branding_seller_tax_id") or None,
                import_dir_path=data.get("import_dir_path") or None,
                export_dir_path=data.get("export_dir_path") or None,
            )
            return self._settings

    def save(self) -> None:
        with self._lock:
            self._path.write_text(json.dumps(asdict(self._settings), indent=2, ensure_ascii=False), encoding="utf-8")

    def set_stationery_rel(self, rel_path: Optional[str]) -> None:
        with self._lock:
            self._settings.stationery_rel_path = rel_path
        self.save()

    def set_import_dir_path(self, path: Optional[str]) -> None:
        with self._lock:
            self._settings.import_dir_path = path
        self.save()

    def set_export_dir_path(self, path: Optional[str]) -> None:
        with self._lock:
            self._settings.export_dir_path = path
        self.save()

    def resolve_stationery_path(self) -> Optional[Path]:
        rel = self._settings.stationery_rel_path
        return self._resolve_relative_path(rel, expect_dir=False)

    def resolve_import_dir(self) -> Optional[Path]:
        return self._resolve_path_string(self._settings.import_dir_path, expect_dir=True)

    def resolve_export_dir(self) -> Optional[Path]:
        return self._resolve_path_string(self._settings.export_dir_path, expect_dir=True)

    def _resolve_relative_path(self, rel: Optional[str], expect_dir: bool) -> Optional[Path]:
        if not rel:
            return None
        p = (self._root / rel).resolve()
        try:
            p.relative_to(self._root.resolve())
        except Exception:
            return None
        if not p.exists():
            return None
        if expect_dir and not p.is_dir():
            return None
        if not expect_dir and not p.is_file():
            return None
        return p

    def _resolve_path_string(self, path_str: Optional[str], expect_dir: bool) -> Optional[Path]:
        if not path_str:
            return None
        p = Path(path_str).expanduser()
        if not p.exists():
            return None
        if expect_dir and not p.is_dir():
            return None
        if not expect_dir and not p.is_file():
            return None
        return p

    def set_ustg_check_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._settings.ustg_check_enabled = enabled
        self.save()

    def set_validate_with_mustang(self, enabled: bool) -> None:
        with self._lock:
            self._settings.validate_with_mustang = enabled
        self.save()

    def set_stationery_apply_all_pages(self, enabled: bool) -> None:
        with self._lock:
            self._settings.stationery_apply_all_pages = enabled
        self.save()

    def has_branding(self) -> bool:
        return bool(self._settings.branding_seller_name) and bool(self._settings.branding_seller_tax_id)

    def set_branding(self, seller_name: str, tax_id: str) -> None:
        with self._lock:
            self._settings.branding_seller_name = seller_name
            self._settings.branding_seller_tax_id = tax_id
        self.save()

    def set_import_dir_from_absolute(self, path: Optional[Path]) -> None:
        self.set_import_dir_path(str(path) if path is not None else None)

    def set_export_dir_from_absolute(self, path: Optional[Path]) -> None:
        self.set_export_dir_path(str(path) if path is not None else None)

    def matches_branding(self, seller_name: Optional[str], tax_ids: tuple[str, ...]) -> bool:
        if not self.has_branding():
            return True
        bn = " ".join((self._settings.branding_seller_name or "").strip().lower().split())
        bt = _normalize_id(self._settings.branding_seller_tax_id or "")

        sn = " ".join((seller_name or "").strip().lower().split())
        if bn and sn and bn != sn:
            return False

        if not bt:
            return True
        for tid in tax_ids:
            if _normalize_id(tid) == bt:
                return True
        return False


def _normalize_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())
