import os
import sqlite3
from datetime import datetime
import requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

LOCKME_API_BASE = "https://api.lock.me/v2.4"

LOCKME_TOKEN = os.getenv("LOCKME_TOKEN", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

START_AT = datetime.utcnow()

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
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK is missing (set it in Render Environment)")
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

@app.get("/test-discord")
def test_discord():
    discord_post("✅ Render -> Discord działa")
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True}

@app.get("/lockme")
async def lockme_webhook(request: Request):
    if WEBHOOK_SECRET and request.query_params.get("s") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
        discord_post("✅ Webhook dotarł do Render")


    msg_id = request.headers.get("X-MessageId")
    if not msg_id:
        raise HTTPException(status_code=400, detail="missing X-MessageId")

    if already_seen(msg_id):
        return {"ok": True}

    try:
        if not LOCKME_TOKEN:
            raise RuntimeError("LOCKME_TOKEN is missing (set it in Render Environment)")

        details = requests.get(
            f"{LOCKME_API_BASE}/message/{msg_id}",
            headers=lockme_headers(),
            timeout=10,
        )
        details.raise_for_status()
        payload = details.json()

        action = payload.get("action")
        data = payload.get("data", {})

        # t = data.get("time")
        # if t:
        #     try:
        #         event_time = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        #         if event_time < START_AT:
        #             ack_message(msg_id)
        #             mark_seen(msg_id)
        #             return {"ok": True}
        #     except Exception:
        #         pass

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
        pricer = data.get("pricer")
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
        if pricer:
            msg += f"\n🏷️ Cennik: {pricer}"
        if price is not None:
            msg += f"\n💰 Cena: {price}"
        if source:
            msg += f"\n🔗 Źródło: {source}"

        discord_post(msg)

        ack_message(msg_id)
        mark_seen(msg_id)
        return {"ok": True}

    except Exception as e:
        try:
            ack_message(msg_id)
            mark_seen(msg_id)
        except Exception:
            pass

        try:
            discord_post(f"⚠️ Błąd obsługi webhooka (msg_id={msg_id}): {type(e).__name__}: {e}")
        except Exception:
            pass

        return {"ok": True}



