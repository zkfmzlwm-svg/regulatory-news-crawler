import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# exe(PyInstaller)로 실행할 때 __file__ 은 실행마다 지워지는 임시 폴더를 가리키므로,
# config/data/output 은 exe 파일이 있는 폴더를 기준으로 잡는다.
BASE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent

CONFIG_DIR = BASE_DIR / "config"
SOURCES_CONFIG = CONFIG_DIR / "sources.yaml"
FORMAT_CONFIG = CONFIG_DIR / "summary_format.yaml"
DB_PATH = BASE_DIR / "data" / "articles.db"
OUTPUT_DIR = BASE_DIR / "output"


def ensure_default_config() -> None:
    """exe 옆에 config 폴더가 없으면 exe 안에 포함된 기본 설정을 복사해 둔다."""
    if not FROZEN:
        return
    bundled = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "config"
    if not bundled.is_dir():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for src in bundled.iterdir():
        dest = CONFIG_DIR / src.name
        if not dest.exists():
            shutil.copyfile(src, dest)
