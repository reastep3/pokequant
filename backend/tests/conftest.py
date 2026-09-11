import os
import pathlib

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{pathlib.Path(__file__).parent / 'test.db'}")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_KEY", "test-key")
os.environ.pop("REDIS_URL", None)
os.environ.pop("PPT_API_KEY", None)
os.environ.pop("PARSEBOT_API_KEY", None)
