import os
import sqlite3
import time
from datetime import datetime
import threading
import requests
from fastapi import FastAPI, Request, HTTPException
from zoneinfo import ZoneInfo

app = FastAPI()

LOCKME_API_BASE = "https://api.lock.me/v2.4"


LOCKME_TOKEN = os.getenv("LOCKME_TOKEN", "")
DISCORD_TODAY_WEBHOOK = os.getenv("DISCORD_TODAY_WEBHOOK", "")
DISCORD_ALL_WEBHOOK = os.getenv("DISCORD_ALL_WEBHOOK", "")
DISCORD_ALERT_WEBHOOK = os.getenv("DISCORD_ALERT_WEBHOOK", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

START_AT = datetime.utcnow()


ROOM_NAMES = {
    1398: "Dooby Doo",
    2132: "Syreni Śpiew",
    12834: "Duchy Rosalie",
    14978: "Trupia Główka",
    10985: "Potworne Miasteczko",
    10984: "American School Story",
}

def get_mention(env_key):
    role_id = os.getenv(env_key)
    return f"<@&{role_id}>" if role_id else ""

ROOM_MENTIONS = {
    1398:  get_mention("R_D"),
    2132:  get_mention("R_S"),
    12834: get_mention("R_R"),
    14978: get_mention("R_T"),
    10985: get_mention("R_P"),
    10984: get_mention("R_A"),
}

TODAY_ROLE = get_mention("ROLE_TODAY")

DB_PATH = "seen.db"

TOKEN_DEAD = False
TOKEN_DEAD_SINCE = None
TOKEN_ALERT_THREAD_STARTED = False


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


# --- KOMUNIKACJA Z WEBHOOKAMI ---
def lockme_headers():
    return {"Authorization": f"Bearer {LOCKME_TOKEN}"}


def post_webhook(url: str, text: str):
    if not url:
        raise RuntimeError("Webhook URL is empty (check Render Environment vars)")
    r = requests.post(
        url,
        json={"content": text, "allowed_mentions": {"parse": ["roles"]}},
        timeout=10
    )
    r.raise_for_status()



def discord_alert(text: str):
    target = DISCORD_ALERT_WEBHOOK or DISCORD_ALL_WEBHOOK or DISCORD_TODAY_WEBHOOK
    try:
        post_webhook(target, text)
    except Exception as e:
        print("discord_alert failed:", e)



# --- AUTOMATYCZNE POWIADOMIENIA O TOKENIE ---
def mark_token_dead():
    global TOKEN_DEAD, TOKEN_DEAD_SINCE
    if not TOKEN_DEAD:
        TOKEN_DEAD = True
        TOKEN_DEAD_SINCE = datetime.utcnow()
        discord_alert("Lock.me token wygasł (401)")


def mark_token_ok():
    global TOKEN_DEAD, TOKEN_DEAD_SINCE
    if TOKEN_DEAD:
        TOKEN_DEAD = False
        TOKEN_DEAD_SINCE = None
        discord_alert("Token Lock.me znów działa poprawnie")

def token_alert_loop():
    while True:
        time.sleep(600)  # Co 10 minut
        if TOKEN_DEAD:
            discord_alert("Przypomnienie: token Lock.me nadal nie działa.")


def ensure_alert_thread():
    global TOKEN_ALERT_THREAD_STARTED
    if not TOKEN_ALERT_THREAD_STARTED:
        TOKEN_ALERT_THREAD_STARTED = True
        threading.Thread(target=token_alert_loop, daemon=True).start()


def ack_message(msg_id: str):
    try:
        r = requests.post(f"{LOCKME_API_BASE}/message/{msg_id}", headers=lockme_headers(), timeout=10)
        if r.status_code == 401: mark_token_dead()
    except:
        pass

def discord_post(text: str):
    if not DISCORD_ALL_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK is missing (set it in Render Environment)")
    post_webhook(DISCORD_ALL_WEBHOOK, text)


# --- GŁÓWNA OBSŁUGA WEBHOOKA ---
@app.on_event("startup")
def _startup():
    init_db()
    ensure_alert_thread()


@app.get("/health")
def health():
    return {"ok": True}

@app.get("/test-discord")
def test_discord():
    discord_post("✅ Render -> Discord działa (rezerwacje)")
    return {"ok": True}

@app.get("/test-all")
def test_all():
    post_webhook(DISCORD_ALL_WEBHOOK, "✅ TEST ALL webhook działa")
    return {"ok": True}

@app.get("/test-today")
def test_today():
    post_webhook(DISCORD_TODAY_WEBHOOK, "✅ TEST TODAY webhook działa")
    return {"ok": True}


@app.get("/lockme")
async def lockme_webhook(request: Request):
    if WEBHOOK_SECRET and request.query_params.get("s") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    msg_id = request.headers.get("X-MessageId")
    if not msg_id or already_seen(msg_id):
        return {"ok": True}

    try:
        if not LOCKME_TOKEN:
            mark_token_dead()
            raise RuntimeError("Brak LOCKME_TOKEN")

#z lockme
        details = requests.get(f"{LOCKME_API_BASE}/message/{msg_id}", headers=lockme_headers(), timeout=10)
        if details.status_code == 401:
            mark_token_dead()
            mark_seen(msg_id)
            return {"ok": True}

        mark_token_ok()
        details.raise_for_status()
        payload = details.json()

        #tylko add
        if payload.get("action") != "add":
            ack_message(msg_id)
            mark_seen(msg_id)
            return {"ok": True}

        data = payload.get("data", {})
#tylko aktualne
        t = data.get("time")
        if t:
            try:
                event_time = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                if event_time < START_AT:
                    ack_message(msg_id)
                    mark_seen(msg_id)
                    return {"ok": True}
            except Exception:
                pass



        room_id = data.get("roomid") or payload.get("roomid")
        room_id_int = int(room_id) if room_id else None

        room_name = ROOM_NAMES.get(room_id_int, f"Pokój #{room_id}")
        room_mention = ROOM_MENTIONS.get(room_id_int, "")

#kanały
        date_str = (data.get("date") or "").strip()
        warsaw_now = datetime.now(ZoneInfo("Europe/Warsaw"))
        today_str = warsaw_now.strftime("%Y-%m-%d")

        if date_str == today_str:
            target_webhook = DISCORD_TODAY_WEBHOOK
            header = f"🚨 {TODAY_ROLE} **REZERWACJA NA DZIŚ!** 🚨"
        else:
            target_webhook = DISCORD_ALL_WEBHOOK
            header = "**NOWA REZERWACJA**"

        time_val = data.get("hour") or "?"
        client = f"{data.get('name', '')} {data.get('surname', '')}".strip() or "?"
        comment = data.get("comment", "").strip()
#info
        msg = (
            f"{header}\n"
            f"{room_mention}\n"
            f"🏠 Pokój: **{room_name}**\n"
            f"📅 Data: {date_str}\n"
            f"🕒 Godzina: {time_val}\n"
            f"👤 Klient: {client}"
        )

        if data.get("people"): msg += f"\n👥 Osoby: {data['people']}"
        if data.get("price"):  msg += f"\n💰 Cena: {data['price']} zł"
        if data.get("source"): msg += f"\n🔗 Źródło: {data['source']}"
        if comment:
            msg += f"\n\n💬 **Komentarz:**\n```{comment}```"

        post_webhook(target_webhook or DISCORD_TODAY_WEBHOOK, msg)

        ack_message(msg_id)
        mark_seen(msg_id)

    except Exception as e:
        discord_alert(f"⚠️ Błąd (msg_id={msg_id}): {e}")
        mark_seen(msg_id)

    return {"ok": True}




