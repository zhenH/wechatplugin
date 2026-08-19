"""运行时设置：管理端可调参数，DB 优先、.env 默认值兜底，改后立即生效无需重启。"""
from .database import session_factory
from .models import AppSetting


class RuntimeSettings:
    def __init__(self):
        self._cache: dict[str, str] = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        with session_factory() as db:
            for row in db.query(AppSetting).all():
                self._cache[row.key] = row.value
        self._loaded = True

    def get(self, key: str, default):
        """取设置：DB 有值用之，否则返回 .env 默认值（类型跟随 default）。"""
        self._load()
        v = self._cache.get(key)
        if v is None or v == "":
            return default
        if isinstance(default, bool):
            return v.lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            try:
                return int(float(v))
            except ValueError:
                return default
        if isinstance(default, float):
            try:
                return float(v)
            except ValueError:
                return default
        return v

    def set(self, key: str, value):
        with session_factory() as db:
            row = db.query(AppSetting).filter(AppSetting.key == key).first()
            if row is not None:
                row.value = str(value)
            else:
                db.add(AppSetting(key=key, value=str(value)))
            db.commit()
        self._cache[key] = str(value)

    def all(self) -> dict[str, str]:
        self._load()
        return dict(self._cache)

    def reset(self):
        """删除全部自定义设置，恢复 .env 默认值。"""
        with session_factory() as db:
            db.query(AppSetting).delete()
            db.commit()
        self._cache.clear()
        self._loaded = True
