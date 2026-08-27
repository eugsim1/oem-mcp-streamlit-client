from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_dir: str | Path) -> Path:
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "oem-mcp-streamlit.log"
    logger = logging.getLogger("oem_mcp_client")
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(log_path) for handler in logger.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
    return log_path


def tail_log(path: str | Path, lines: int = 200) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return "No application log has been written yet."
    content = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-max(1, min(lines, 2000)) :])
