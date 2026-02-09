import os
import sqlite3
import requests
from fastapi import FastAPI, Request, HTTPException, Response

app = FastAPI()
import os

LOCKME_API_BASE = "https://api.lock.me/v2.4"

LOCKME_TOKEN = os.getenv("LOCKME_TOKEN", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "123")

ROOM_NAMES = {
    1398: "Dooby Doo",
    2132: "Syreni Śpiew",
    12834: "Duchy Rosalie",
    14978: "Trupia Główka",
    10985: "Potworne Miasteczko",
    10984: "American School Story"

}

DB_PATH = "seen.db"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS seen (msg_id TEXT PRIMARY KEY)")
    con.commit()
    con.close()

def already_seen(msg_id: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT 1 FROM seen WHERE msg_id = ?", (msg_id,))
    row = cur.fetchone()
    con.close()
    return row is not None

def mark_seen(msg_id: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO seen(msg_id) VALUES (?)", (msg_id,))
    con.commit()
    con.close()

def lockme_headers():
    return {"Authorization": f"Bearer {LOCKME_TOKEN}"}

def discord_post(text: str):
    r = requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
    r.raise_for_status()

def ack_message(msg_id: str):
    r = requests.post(
        f"{LOCKME_API_BASE}/message/{msg_id}",
        headers=lockme_headers(),
        timeout=10,
    )
    r.raise_for_status()


@app.on_event("startup")
def _startup():
    init_db()

@app.get("/lockme")
async def lockme_webhook(request: Request):
    if WEBHOOK_SECRET and request.query_params.get("s") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    msg_id = request.headers.get("X-MessageId")
    if not msg_id:
        raise HTTPException(status_code=400, detail="missing X-MessageId")

    if already_seen(msg_id):
        return {"ok": True}

    try:
        details = requests.get(
            f"{LOCKME_API_BASE}/message/{msg_id}",
            headers=lockme_headers(),
            timeout=10,
        )
        details.raise_for_status()
        payload = details.json()

        action = payload.get("action")
        data = payload.get("data", {})

        # nie interesuje nas nic poza "add"
        if action != "add":
            ack_message(msg_id)
            mark_seen(msg_id)
            return {"ok": True}

        room_id = data.get("roomid") or payload.get("roomid")
        room_name = ROOM_NAMES.get(int(room_id), f"Pokój #{room_id}") if room_id else "Nieznany pokój"

        date = data.get("date") or "?"
        time_ = data.get("hour") or "?"
        people = data.get("people")
        price = data.get("price")
        source = data.get("source")
        client = f"{data.get('name','')} {data.get('surname','')}".strip() or "?"

        msg = (
            f"📩 **NOWA REZERWACJA**\n"
            f"🏠 Pokój: {room_name}\n"
            f"📅 Data: {date}\n"
            f"🕒 Godzina: {time_}\n"
            f"👤 Klient: {client}"
        )
        if people is not None:
            msg += f"\n👥 Osoby: {people}"
        if price is not None:
            msg += f"\n💰 Cena: {price}"
        if source:
            msg += f"\n🔗 Źródło: {source}"

        discord_post(msg)

        # ACK + zapisz jako obsłużone
        ack_message(msg_id)
        mark_seen(msg_id)
        return {"ok": True}

    except Exception as e:
        # INFO na discord (opcjonalnie)
        try:
            discord_post(f"⚠️ Błąd obsługi webhooka: {type(e).__name__}: {e}")
        except Exception:
            pass

        # <<< KLUCZ: spróbuj ACK nawet przy błędzie, żeby Lock.me nie retry >>
        try:
            ack_message(msg_id)
            mark_seen(msg_id)
        except Exception:
            pass

        # zwróć 200, żeby Lock.me nie uznał, że webhook padł
        return {"ok": True}
