import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

USERS_FILE = Path(__file__).parent.parent.parent / "data" / "users.json"

def load_users() -> Dict[str, Any]:
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_users(users: Dict[str, Any]):
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def upsert_user(username: str, ld_user_id: str) -> Dict[str, Any]:
    users = load_users()
    if username in users:
        user = users[username]
        user["ld_user_id"] = ld_user_id
        user["last_login"] = datetime.utcnow().isoformat()
    else:
        user = {
            "username": username,
            "display_name": username,
            "role": "user",
            "ld_user_id": ld_user_id,
            "created_at": datetime.utcnow().isoformat(),
            "last_login": datetime.utcnow().isoformat(),
            "qa_account_linked": username
        }
        users[username] = user
    save_users(users)
    return user

def get_user(username: str):
    users = load_users()
    return users.get(username)
