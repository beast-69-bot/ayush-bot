from flask import Flask, request, jsonify, send_from_directory, Response
import sqlite3
from datetime import datetime
import os
import uuid
import time
import urllib.request
import urllib.parse

app = Flask(__name__)

# Try local file first, then fallback to root path
DB_PATH = 'premium_store.db'
if not os.path.exists(DB_PATH):
    DB_PATH = '/root/premium_bot/premium_store.db'

EXPECTED_SIGNATURE = "469b9bde0dd12e8f3cbaa1f60bb4013e4b9e0978cd86955fdc65c99a6ce14f76"

# Load BOT_TOKEN from .env file
BOT_TOKEN = None
if os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            if line.startswith('BOT_TOKEN='):
                BOT_TOKEN = line.split('=', 1)[1].strip().strip('"').strip("'")
                break
if not BOT_TOKEN:
    BOT_TOKEN = os.environ.get('BOT_TOKEN')

def send_telegram_notification(user_id, message_text):
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing. Cannot send notification.")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": user_id,
            "text": message_text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")

# 🛡️ Signature Verification Filter (Browser Access Block)
@app.before_request
def check_signature():
    # Sirf HTML page (root path '/') ko browser se lock karna hai. 
    # JS API calls ('/verify_key', '/get_ott') ko allow karna hai kyunki Android WebView JS custom headers drop karta hai.
    if request.path == '/':
        sig = request.headers.get('X-Az-Signature', '')
        if sig != EXPECTED_SIGNATURE:
            return Response("<html><body style='color:white;background:black;text-align:center;padding-top:50px;'><h1>Unauthorized Browser</h1><p>Please use the Official Mod APK.</p></body></html>", status=403, content_type='text/html')

@app.route('/')
def login_page():
    return send_from_directory('.', 'login.html')

@app.route('/verify_key', methods=['GET'])
def verify_key():
    key = request.args.get('key')
    device_id = request.args.get('device_id', 'unknown_device')
    
    if not key:
        return jsonify({"status": "error", "message": "Key missing"}), 400
        
    if not os.path.exists(DB_PATH):
        return jsonify({"status": "error", "message": "Database not found"})
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    try:
        c.execute("ALTER TABLE apk_keys ADD COLUMN devices TEXT DEFAULT ''")
        conn.commit()
    except: pass
        
    c.execute("SELECT expiry_date, is_active, devices, user_id FROM apk_keys WHERE key=?", (key,))
    result = c.fetchone()
    
    if result:
        expiry_date, is_active, devices_str, user_id = result
        
        if is_active == 0:
            conn.close()
            return jsonify({"status": "invalid", "message": "Key is blocked"})
            
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if now_str > expiry_date:
            conn.close()
            return jsonify({"status": "expired", "message": "Key expired"})
            
        devices = devices_str.split(',') if devices_str else []
        is_first_activation = len(devices) == 0
        
        if device_id not in devices:
            if len(devices) >= 3:
                conn.close()
                return jsonify({"status": "limit", "message": "Device limit reached (Max 3 devices)"})
            devices.append(device_id)
            c.execute("UPDATE apk_keys SET devices=? WHERE key=?", (",".join(devices), key))
            conn.commit()
            
        # Send dynamic telegram notification to user on first key activation
        if is_first_activation and user_id:
            try:
                expiry_dt = datetime.strptime(expiry_date, '%Y-%m-%d %H:%M:%S')
                delta = expiry_dt - datetime.now()
                plan_days = max(1, round(delta.total_seconds() / (24 * 60 * 60)))
            except Exception:
                plan_days = "Unknown"
                
            notif_text = (
                "🚀 <b>Key Activated Successfully!</b>\n\n"
                "Aapki Faphouse VIP Key ab active aur used ho chuki hai.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 <b>Key:</b> <code>{key}</code>\n"
                f"📅 <b>Duration:</b> {plan_days} Days\n"
                f"🕒 <b>Expiry Date:</b> {expiry_date} UTC\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Enjoy your premium experience!"
            )
            send_telegram_notification(user_id, notif_text)
            
        # 🕒 TIME FIX: UTC timezone explicitly add ki hai
        dt = datetime.strptime(expiry_date, '%Y-%m-%d %H:%M:%S')
        iso_date = dt.isoformat() + "+00:00"
        
        conn.close()
        return jsonify({"status": "valid", "message": "Login Successful", "expiry": iso_date})
    else:
        conn.close()
        return jsonify({"status": "invalid", "message": "Invalid Key"})

@app.route('/get_ott', methods=['GET'])
def get_ott():
    key = request.args.get('key')
    if not key:
        return jsonify({"status": "error", "message": "Key missing"}), 400
        
    if not os.path.exists(DB_PATH):
        return jsonify({"status": "error", "message": "Database not found"})
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS auth_tokens 
                 (token TEXT PRIMARY KEY, key TEXT, expiry REAL)''')
    
    c.execute("SELECT expiry_date FROM apk_keys WHERE key=? AND is_active=1", (key,))
    res = c.fetchone()
    if not res:
        conn.close()
        return jsonify({"status": "error", "message": "Invalid Key"})
        
    ott = str(uuid.uuid4())
    c.execute("INSERT INTO auth_tokens VALUES (?, ?, ?)", (ott, key, time.time() + 15))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success", "token": ott})

@app.route('/verify_ott', methods=['GET'])
def verify_ott():
    token = request.args.get('token')
    if not token:
        return jsonify({"status": "invalid"}), 400
        
    if not os.path.exists(DB_PATH):
        return jsonify({"status": "invalid"}), 500
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT expiry FROM auth_tokens WHERE token=?", (token,))
    res = c.fetchone()
    
    if res:
        c.execute("DELETE FROM auth_tokens WHERE token=?", (token,))
        conn.commit()
        conn.close()
        
        if time.time() < res[0]:
            return jsonify({"status": "valid"})
            
    conn.close()
    return jsonify({"status": "invalid"}), 403

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
