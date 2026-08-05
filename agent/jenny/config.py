"""Nạp cấu hình từ config/secrets/*.env.

Trên VPS: /opt/jenny/config/secrets. Local dev: đặt JENNY_SECRETS_DIR
trỏ tới <repo>/config/secrets.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

SECRETS_DIR = Path(os.environ.get("JENNY_SECRETS_DIR", "/opt/jenny/config/secrets"))
WORKDIR = Path(os.environ.get("JENNY_WORKDIR", "/opt/jenny/workdir"))

for _name in ("supabase.env", "agent.env"):
    _p = SECRETS_DIR / _name
    if _p.exists():
        load_dotenv(_p)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

LARK_APP_ID = os.environ.get("LARK_APP_ID", "")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET", "")
# Lark Suite quốc tế; tenant Feishu (TQ) thì đổi thành https://open.feishu.cn
LARK_DOMAIN = os.environ.get("LARK_DOMAIN", "https://open.larksuite.com")

AGENT_NAME = "jenny"
MAX_HISTORY_MESSAGES = 12          # số tin nhắn gần nhất đưa vào ngữ cảnh
TELEGRAM_MAX_LEN = 4096


def require(*names: str) -> None:
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(f"Thiếu biến môi trường: {', '.join(missing)} (kiểm tra {SECRETS_DIR})")
