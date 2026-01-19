import os
import time
import random
import uuid
import json
from flask import Flask, request, jsonify, render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache

app = Flask(__name__)

# --- SYSTEM CONFIG ---
OFFLINE_THRESHOLD = 75
PRUNE_THRESHOLD = 3600
DB_SYNC_INTERVAL = 25
MAX_PLAYERS = 1000
MIN_CLIENT_VERSION = 101

# --- DATABASE ---
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+mysqlconnector://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"
app.config['SQLALCHEMY_POOL_RECYCLE'] = 280
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

cache_dir = os.path.join(app.root_path, 'flask_cache')
if not os.path.exists(cache_dir): os.makedirs(cache_dir)
app.config['CACHE_TYPE'] = 'FileSystemCache'
app.config['CACHE_DIR'] = cache_dir
app.config['CACHE_DEFAULT_TIMEOUT'] = 300
app.config['CACHE_THRESHOLD'] = 2000

db = SQLAlchemy(app)
cache = Cache(app)

# --- MODELS ---
class BannedIP(db.Model):
    __tablename__ = 'banned_ips'
    ip_address = db.Column(db.String(50), primary_key=True)
    reason = db.Column(db.String(255))
    banned_at = db.Column(db.Integer)

class QueueEntry(db.Model):
    __tablename__ = 'queue'
    uid = db.Column(db.String(255), primary_key=True)
    joined_at = db.Column(db.Integer, nullable=False)
    last_seen = db.Column(db.Integer, nullable=False)

class Player(db.Model):
    __tablename__ = 'players'
    uid = db.Column(db.String(255), primary_key=True)
    api_token = db.Column(db.String(64), unique=True, nullable=False)
    last_ip = db.Column(db.String(50))

    x = db.Column(db.Float, default=0.0)
    y = db.Column(db.Float, default=0.0)
    z = db.Column(db.Float, default=0.0)
    location = db.Column(db.Integer, default=0)
    last_seen = db.Column(db.Integer, default=0)
    last_location_id = db.Column(db.Integer, default=0)
    current_hand = db.Column(db.Text, default="")

with app.app_context():
    db.create_all()

# --- HELPER FUNCTIONS ---
def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0]
    return request.remote_addr

def is_banned(ip):
    cache_key = f"ban_check_{ip}"
    cached = cache.get(cache_key)
    if cached is not None: return cached
    banned = BannedIP.query.filter_by(ip_address=ip).first() is not None
    cache.set(cache_key, banned, timeout=60)
    return banned

# --- ROUTES ---

@app.route('/register', methods=['POST'])
def register():
    client_ip = get_client_ip()
    if is_banned(client_ip):
        return jsonify({"error": "Connection Refused: IP Banned"}), 403

    data = request.json
    uid = data.get("uid")
    if not uid: return jsonify({"error": "No UID"}), 400

    existing = Player.query.filter_by(uid=uid).first()
    if existing:
        new_token = str(uuid.uuid4())
        existing.api_token = new_token
        existing.last_ip = client_ip
        db.session.commit()
        cache.set(f"auth_{new_token}", uid, timeout=3600)
        return jsonify({"status": "recovered", "api_token": new_token})

    new_token = str(uuid.uuid4())
    # Set last_seen=0 to force new players through the Queue Gate.
    new_player = Player(uid=uid, api_token=new_token, last_seen=0, last_ip=client_ip)
    db.session.add(new_player)
    db.session.commit()

    # PRE-WARM CACHE
    initial_pdata = {
        "uid": uid,
        "x": 0.0, "y": 0.0, "z": 0.0,
        "location": 0,
        "last_seen": 0,
        "last_db_sync": 0,
        "current_hand": [],
        "last_location_id": 0
    }

    cache.set(f"auth_{new_token}", uid, timeout=3600)
    cache.set(f"pdata_{uid}", initial_pdata, timeout=300)

    return jsonify({"status": "registered", "api_token": new_token})

@app.route('/update', methods=['POST'])
def update():
    client_ip = get_client_ip()
    current_time = int(time.time())

    if is_banned(client_ip): return jsonify({"error": "Banned"}), 403

    data = request.json
    client_version = data.get("version", 0)
    if client_version < MIN_CLIENT_VERSION: return jsonify({"error": "Upgrade Required"}), 426

    token = request.headers.get("X-Lumis-Token")
    if not token: return jsonify({"error": "Missing Auth Token"}), 401

    uid = cache.get(f"auth_{token}")
    if not uid:
        me_db = Player.query.filter_by(api_token=token).first()
        if not me_db: return jsonify({"error": "Invalid Token"}), 403
        uid = me_db.uid
        cache.set(f"auth_{token}", uid, timeout=3600)

        initial_data = {
            "uid": uid,
            "x": me_db.x, "y": me_db.y, "z": me_db.z,
            "location": me_db.location,
            "last_seen": me_db.last_seen,
            "last_db_sync": 0,
            "current_hand": me_db.current_hand.split(",") if me_db.current_hand else [],
            "last_location_id": me_db.last_location_id
        }
        cache.set(f"pdata_{uid}", initial_data, timeout=300)

    # QUEUE LOGIC
    data_changed = False
    if random.random() < 0.25:
        QueueEntry.query.filter(QueueEntry.last_seen < (current_time - OFFLINE_THRESHOLD)).delete()
        data_changed = True

    if random.random() < 0.25:
        # Prune inactive players regardless of status
        Player.query.filter(Player.last_seen < (current_time - PRUNE_THRESHOLD)).delete()
        data_changed = True

    if data_changed: db.session.commit()

    active_record = Player.query.filter(Player.uid == uid, Player.last_seen > (current_time - OFFLINE_THRESHOLD)).first()
    is_active = (active_record is not None)

    pdata = cache.get(f"pdata_{uid}")
    if not pdata:
        me_db = Player.query.get(uid)
        
        # COLD CACHE PRUNING
        if me_db is None:
            cache.delete(f"auth_{token}")
            return jsonify({"error": "User Pruned"}), 403
        
        pdata = {"uid": uid}

    if not is_active:
        # WARM CACHE PRUNING
        confirm_db = Player.query.get(uid)
        if confirm_db is None:
            cache.delete(f"auth_{token}")
            cache.delete(f"pdata_{uid}")
            return jsonify({"error": "User Pruned"}), 403

        # Universal Queue Logic
        active_count = Player.query.filter(Player.last_seen > (current_time - OFFLINE_THRESHOLD)).count()
        queue_record = QueueEntry.query.get(uid)

        if queue_record:
            if (current_time - queue_record.last_seen) > OFFLINE_THRESHOLD:
                queue_record.joined_at = current_time
            queue_record.last_seen = current_time
            db.session.commit()

            if active_count < MAX_PLAYERS:
                db.session.delete(queue_record)
                me_db = Player.query.get(uid)
                if me_db: me_db.last_seen = current_time
                db.session.commit()
            else:
                cache.set(f"auth_{token}", uid, timeout=3600)
                position = QueueEntry.query.filter(QueueEntry.joined_at < queue_record.joined_at, QueueEntry.last_seen > (current_time - OFFLINE_THRESHOLD)).count() + 1
                return jsonify({"status": "queued", "position": position})
        else:
            if active_count >= MAX_PLAYERS:
                new_q = QueueEntry(uid=uid, joined_at=current_time, last_seen=current_time)
                db.session.add(new_q)
                db.session.commit()
                cache.set(f"auth_{token}", uid, timeout=3600)
                position = QueueEntry.query.filter(QueueEntry.last_seen > (current_time - OFFLINE_THRESHOLD)).count()
                return jsonify({"status": "queued", "position": position})

    new_x = data.get("x")
    new_y = data.get("y")
    new_z = data.get("z")
    new_loc = data.get("location")

    if "uid" not in pdata:
        pdata = {
            "uid": uid,
            "x": new_x, "y": new_y, "z": new_z,
            "location": new_loc,
            "last_seen": current_time,
            "last_db_sync": 0,
            "current_hand": [],
            "last_location_id": new_loc
        }

    old_loc = pdata.get("location")
    if old_loc != new_loc:
        old_cell_key = f"cell_{old_loc}"
        old_cell_list = cache.get(old_cell_key) or []
        if uid in old_cell_list:
            old_cell_list.remove(uid)
            cache.set(old_cell_key, old_cell_list)

    new_cell_key = f"cell_{new_loc}"
    new_cell_list = cache.get(new_cell_key) or []
    if uid not in new_cell_list:
        new_cell_list.append(uid)
        cache.set(new_cell_key, new_cell_list)

    pdata["x"] = new_x
    pdata["y"] = new_y
    pdata["z"] = new_z
    pdata["location"] = new_loc
    pdata["last_seen"] = current_time

    last_sync = pdata.get("last_db_sync", 0)
    if (current_time - last_sync) > DB_SYNC_INTERVAL:
        me_db = Player.query.get(uid)
        if me_db:
            me_db.x = new_x
            me_db.y = new_y
            me_db.z = new_z
            me_db.location = new_loc
            me_db.last_seen = current_time
            me_db.last_ip = client_ip
            me_db.current_hand = ",".join(pdata["current_hand"])
            me_db.last_location_id = pdata["last_location_id"]
            db.session.commit()
            pdata["last_db_sync"] = current_time

    potential_uids = cache.get(new_cell_key) or []
    keys = [f"pdata_{u}" for u in potential_uids if u != uid]
    potential_players_data = []
    if keys: potential_players_data = cache.get_many(*keys)

    nearby_uids = []
    cutoff_sq = 20250000.0
    active_neighbors = []

    for p in potential_players_data:
        if not p: continue
        if (current_time - p["last_seen"]) > OFFLINE_THRESHOLD: continue
        dist_sq = (p["x"] - new_x)**2 + (p["y"] - new_y)**2 + (p["z"] - new_z)**2
        if dist_sq <= cutoff_sq:
            nearby_uids.append(p["uid"])
            active_neighbors.append(p)

    local_count = len(nearby_uids)
    current_hand_uids = pdata.get("current_hand", [])
    last_loc_id = pdata.get("last_location_id", new_loc)
    new_hand_uids = []

    if new_loc != last_loc_id:
        new_hand_uids = random.sample(nearby_uids, min(len(nearby_uids), 8))
        pdata["last_location_id"] = new_loc
    else:
        valid_old = [u for u in current_hand_uids if u in nearby_uids]
        candidates = [u for u in nearby_uids if u not in valid_old]
        needs_count = 8 - len(valid_old)
        fillers = random.sample(candidates, min(len(candidates), needs_count))
        new_hand_uids = valid_old + fillers

    pdata["current_hand"] = new_hand_uids
    cache.set(f"pdata_{uid}", pdata)

    ghost_list = []
    neighbor_map = {p["uid"]: p for p in active_neighbors}
    for g_uid in new_hand_uids:
        g_data = neighbor_map.get(g_uid)
        if g_data:
            ghost_list.append({
                "uid": g_data["uid"],
                "x": g_data["x"],
                "y": g_data["y"],
                "z": g_data["z"],
                "location": g_data["location"]
            })

    global_count = Player.query.filter(Player.last_seen > (current_time - OFFLINE_THRESHOLD)).count()

    return jsonify({
        "status": "active",
        "meta": { "global": global_count, "local": local_count },
        "ghosts": ghost_list
    })

if __name__ == '__main__':
    app.run()