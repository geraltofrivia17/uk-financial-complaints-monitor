"""Download the published files listed in config/sources.csv.

Files already present are kept. The FCA and Ombudsman websites refuse some
automated clients, so a browser-like User-Agent is sent.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import pandas as pd

from .settings import Settings, load_settings

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(settings: Settings | None = None) -> pd.DataFrame:
    settings = settings or load_settings()
    sources = pd.read_csv(settings.config / "sources.csv")
    rows = []
    for row in sources.itertuples():
        folder = settings.raw / ("fca" if row.source == "FCA" else "fos")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / row.file
        status = "already present"
        if not target.exists() or target.stat().st_size == 0:
            request = urllib.request.Request(row.url, headers={"User-Agent": _USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    target.write_bytes(response.read())
                status = "downloaded"
            except OSError as error:
                status = f"failed: {error}"
        ok = target.exists() and target.read_bytes()[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")
        rows.append(
            {
                "file": row.file,
                "status": status if ok or status.startswith("failed") else "not a spreadsheet",
                "sha256": sha256(target) if ok else "",
            }
        )
        print(f"  {rows[-1]['status']:<16} {row.file}")
    return pd.DataFrame(rows)
