"""Canlı yazma: her izleme kaydını seçilen klasördeki canli-gorunumler.csv dosyasına hemen ekler.

Dosya Excel'de açıksa (Windows dosyayı kilitler) yazma başarısız olur; kayıt kaybolmaz, bir sonraki izlemede
bekleyenlerle birlikte yazılır. Veritabanı her zaman asıl kayıttır.
"""

from __future__ import annotations

import csv
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from .export import VIEW_COLUMNS, _cell
from .store import Store

FILE_NAME = "canli-gorunumler.csv"


class LiveWriter:
    def __init__(self, store: Store, get_dir: Callable[[], Optional[Path]], log: Callable[[str, str], None]):
        self.store = store
        self.get_dir = get_dir
        self.log = log
        self._lock = threading.Lock()
        self._last_id = 0
        self._dir: Optional[Path] = None
        self.error = ""
        self.path = ""
        self.written = 0

    def on_view(self) -> None:
        """Yeni izleme kayıtlarını (son yazılandan sonrakileri) dosyaya ekler."""
        with self._lock:
            folder = self.get_dir()
            if folder is None:
                self.error = ""
                return
            folder = Path(folder)
            if self._dir != folder:
                self._dir, self._last_id = folder, 0     # klasör değişti: eski kayıtlar da yeni yere yazılsın
            rows = self.store.export_views(after_id=self._last_id)
            if not rows:
                return
            path = folder / FILE_NAME
            try:
                folder.mkdir(parents=True, exist_ok=True)
                new_file = not path.exists() or path.stat().st_size == 0
                with open(path, "a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as f:
                    w = csv.writer(f, delimiter=";")
                    if new_file:
                        w.writerow(VIEW_COLUMNS)
                    for r in rows:
                        w.writerow([_cell(r.get(c)) for c in VIEW_COLUMNS])
            except OSError as exc:
                msg = f"{type(exc).__name__}: {exc}"
                if msg != self.error:
                    self.log("warn", f"Canlı yazma olmadı ({path}): {msg}. Kayıtlar veritabanında; dosya açıksa kapat, bir sonraki izlemede yazılır.")
                self.error = msg
                return
            self._last_id = max(int(r["id"]) for r in rows)
            self.written += len(rows)
            self.path = str(path)
            if self.error:
                self.log("ok", "Canlı yazma düzeldi.")
            self.error = ""

    def status(self) -> Tuple[str, str]:
        return self.path, self.error
