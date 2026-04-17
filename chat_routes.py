"""
Shared chat + notification routes — register on both CRM and LeadGen Flask apps.
Usage:
    from chat_routes import register_chat_routes
    register_chat_routes(app, platform="crm")   # or "leadgen"
"""
from flask import Blueprint, request, jsonify, session
from datetime import datetime, timezone
import team as tm

def register_chat_routes(app, platform: str = "general"):

    bp = Blueprint("chat", __name__)

    def _current_user():
        uid = session.get("team_user_id")
        if not uid:
            return None
        return tm.get_user_by_id(uid)

    # ── Messages ──────────────────────────────────────────────────────────────

    @bp.route("/chat/messages")
    def chat_messages():
        u = _current_user()
        if not u:
            return jsonify({"error": "not logged in"}), 401
        channel  = request.args.get("channel", "general")
        since    = int(request.args.get("since", 0))
        limit    = int(request.args.get("limit", 50))
        msgs     = tm.get_messages(channel=channel, since_id=since, limit=limit)
        # Attach avatar_color to each message
        for m in msgs:
            sender = tm.get_user_by_username(m.get("username","")) or {}
            m["avatar_color"] = sender.get("avatar_color", "#888")
        return jsonify({"messages": msgs, "channel": channel})

    @bp.route("/chat/send", methods=["POST"])
    def chat_send():
        u = _current_user()
        if not u:
            return jsonify({"error": "not logged in"}), 401
        data    = request.get_json() or {}
        channel = data.get("channel", "general")
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "empty"}), 400
        msg_id  = tm.send_message(
            user_id     = u["id"],
            username    = u["username"],
            display_name= u["display_name"],
            channel     = channel,
            message     = message,
            platform    = platform,
        )
        msg = {
            "id"          : msg_id,
            "user_id"     : u["id"],
            "username"    : u["username"],
            "display_name": u["display_name"],
            "avatar_color": u.get("avatar_color", "#888"),
            "channel"     : channel,
            "message"     : message,
            "platform"    : platform,
            "created_at"  : datetime.now(timezone.utc).isoformat(),
        }
        return jsonify({"ok": True, "message": msg})

    @bp.route("/chat/unread")
    def chat_unread():
        u = _current_user()
        if not u:
            return jsonify({"channels": {}})
        # Return unread count per channel (messages not from current user, after last seen)
        last_seen = session.get("chat_last_seen", {})
        result = {}
        for ch in tm.CHANNELS:
            cid     = ch["id"]
            since   = last_seen.get(cid, 0)
            msgs    = tm.get_messages(channel=cid, since_id=since, limit=100)
            unread  = sum(1 for m in msgs if m.get("user_id") != u["id"])
            result[cid] = unread
        return jsonify({"channels": result})

    @bp.route("/chat/online")
    def chat_online():
        u = _current_user()
        if not u:
            return jsonify({"users": []})
        users   = tm.get_all_users()
        now     = datetime.utcnow()
        result  = []
        for usr in users:
            ls      = usr.get("last_seen")
            online  = False
            if ls:
                try:
                    last = datetime.fromisoformat(str(ls).replace("Z",""))
                    online = (now - last).total_seconds() < 300  # 5 min
                except Exception:
                    pass
            result.append({
                "id"          : usr["id"],
                "username"    : usr["username"],
                "display_name": usr["display_name"],
                "avatar_color": usr.get("avatar_color", "#888"),
                "role"        : usr.get("role","viewer"),
                "online"      : online,
            })
        return jsonify({"users": result})

    @bp.route("/chat/mark-read", methods=["POST"])
    def chat_mark_read():
        u = _current_user()
        if not u:
            return jsonify({"ok": False}), 401
        data    = request.get_json() or {}
        channel = data.get("channel", "general")
        last_id = tm.get_latest_message_id()
        if "chat_last_seen" not in session:
            session["chat_last_seen"] = {}
        session["chat_last_seen"][channel] = last_id
        session.modified = True
        return jsonify({"ok": True})

    # ── Notifications ─────────────────────────────────────────────────────────

    @bp.route("/notifications/list")
    def notif_list():
        u = _current_user()
        if not u:
            return jsonify({"notifications": []})
        notifs = tm.get_notifications(u["id"], limit=30)
        return jsonify({"notifications": notifs})

    @bp.route("/notifications/unread")
    def notif_unread():
        u = _current_user()
        if not u:
            return jsonify({"count": 0})
        return jsonify({"count": tm.get_unread_notifications(u["id"])})

    @bp.route("/notifications/read", methods=["POST"])
    def notif_read():
        u = _current_user()
        if not u:
            return jsonify({"ok": False}), 401
        tm.mark_notifications_read(u["id"])
        return jsonify({"ok": True})

    app.register_blueprint(bp)
