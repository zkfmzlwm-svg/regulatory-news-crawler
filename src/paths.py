import json
import logging
import shutil
import sys
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger(__name__)

FROZEN = getattr(sys, "frozen", False)

# exe(PyInstaller)로 실행할 때 __file__ 은 실행마다 지워지는 임시 폴더를 가리키므로,
# config/data/output 은 exe 파일이 있는 폴더를 기준으로 잡는다.
BASE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent

CONFIG_DIR = BASE_DIR / "config"
SOURCES_CONFIG = CONFIG_DIR / "sources.yaml"
FORMAT_CONFIG = CONFIG_DIR / "summary_format.yaml"
DB_PATH = BASE_DIR / "data" / "articles.db"
OUTPUT_DIR = BASE_DIR / "output"
# exe 가 지금까지 config/sources.yaml 에 넣어 준 기본 사이트 이름 (사용자가 지운 기본 사이트를 다시 넣지 않기 위함)
DEFAULTS_STATE = CONFIG_DIR / ".default_sources.json"


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
    # 방금 복사한 경우에도 실행해 두어야 "이미 넣어 준 기본 사이트" 기록이 남는다
    bundled_sources = bundled / SOURCES_CONFIG.name
    if bundled_sources.is_file():
        try:
            changed = sync_default_sources(bundled_sources, SOURCES_CONFIG, DEFAULTS_STATE)
            if changed:
                logger.info("기본 사이트 설정 갱신: %s", ", ".join(changed))
        except Exception as exc:  # 설정 파일이 깨져 있어도 프로그램은 뜨도록
            logger.warning("기본 사이트 설정 갱신 실패: %s", exc)


def sync_default_sources(bundled: Path, user: Path, state: Path) -> List[str]:
    """새 버전에 들어 있는 기본 사이트 설정을 기존 config/sources.yaml 에 반영하고, 바뀐 사이트 이름을 돌려준다.

    예전에는 exe 를 새로 받아도 옆에 남은 옛 sources.yaml 때문에 고친 사이트 주소가 적용되지 않아
    파일을 직접 지워야 했다. 이제는 실행할 때 자동으로 맞춘다.
    - 기본 사이트(이름 기준)는 새 설정으로 바꾸되, 사용자가 정한 사용/중지(enabled)는 그대로 둔다
    - 새로 생긴 기본 사이트는 끝에 추가 (예전에 넣어 줬는데 사용자가 지운 사이트는 다시 넣지 않음)
    - 사용자가 직접 추가한 사이트는 건드리지 않는다
    """
    defaults = (yaml.safe_load(bundled.read_text(encoding="utf-8")) or {}).get("sources") or []
    data = yaml.safe_load(user.read_text(encoding="utf-8")) or {}
    current = data.get("sources") or []
    try:
        offered = set(json.loads(state.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        offered = set()

    index = {s.get("name"): i for i, s in enumerate(current)}
    changed = []
    for default in defaults:
        name = default.get("name")
        i = index.get(name)
        if i is None:
            if name not in offered:
                current.append(dict(default))
                changed.append(name)
            continue
        merged = dict(default, enabled=current[i].get("enabled", default.get("enabled", True)))
        if merged != current[i]:
            current[i] = merged
            changed.append(name)

    if changed:
        shutil.copyfile(user, user.with_name(user.name + ".bak"))
        data["sources"] = current
        user.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    state.write_text(
        json.dumps(sorted(offered | {d.get("name") for d in defaults}), ensure_ascii=False), encoding="utf-8"
    )
    return changed
