#!/usr/bin/env python3
"""Tempora — pointage des horaires par lien personnel (Flask + SQLite).

Usage :
    pip install flask
    python app.py add "Prénom"      # crée un salarié et affiche son lien
    python app.py list              # liste les salariés et leurs liens
    python app.py disable <id>      # désactive un salarié (lien invalidé)
    python app.py serve             # lance le serveur (défaut)

Variables d'environnement :
    TEMPORA_DB             chemin de la base SQLite (défaut : tempora.db)
    TEMPORA_ADMIN_USER     identifiant admin (défaut : jerome)
    TEMPORA_ADMIN_PASSWORD  mot de passe admin (OBLIGATOIRE pour accéder à /admin)
    TEMPORA_ALLOWED_NETS   réseaux autorisés pour pointer, ex : 192.168.1.0/24
                          (vide = pas de restriction)
    TEMPORA_BASE_URL       URL publique affichée dans l'admin, ex : http://192.168.1.50:8080
    TEMPORA_PORT           port d'écoute (défaut : 8080)
"""
import csv
import io
import ipaddress
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import (Flask, Response, abort, g, redirect, render_template_string,
                   request, url_for)

TZ = ZoneInfo("Europe/Paris")
DB_PATH = os.environ.get("TEMPORA_DB", "tempora.db")
ADMIN_USER = os.environ.get("TEMPORA_ADMIN_USER", "jerome")
ADMIN_PASSWORD = os.environ.get("TEMPORA_ADMIN_PASSWORD", "")
ALLOWED_NETS = [ipaddress.ip_network(n.strip())
                for n in os.environ.get("TEMPORA_ALLOWED_NETS", "").split(",") if n.strip()]
MIN_GAP = 20  # secondes : ignore un second appui trop rapproché

app = Flask(__name__)

# --------------------------------------------------------------------------- #
# Base de données
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  token TEXT UNIQUE NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  ts INTEGER NOT NULL,                       -- epoch UTC (horodatage serveur)
  kind TEXT NOT NULL CHECK(kind IN ('in','out')),
  source TEXT NOT NULL DEFAULT 'qr'          -- 'qr' ou 'admin'
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events ON events(employee_id, ts);
"""


def init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d is not None:
        d.close()


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def now():
    return int(datetime.now(timezone.utc).timestamp())


def local(ts):
    return datetime.fromtimestamp(ts, TZ)


def fmt_h(sec):
    h, m = divmod(int(sec) // 60, 60)
    return f"{h}h{m:02d}"


def dt(ts):
    return local(ts).strftime("%d/%m/%Y %H:%M")


def audit(action, detail):
    db().execute("INSERT INTO audit(ts, action, detail) VALUES (?,?,?)",
                 (now(), action, detail))


def shifts(events):
    """events triés par ts -> liste de (début, fin). Valeur None = pointage manquant."""
    out, start = [], None
    for e in events:
        if e["kind"] == "in":
            if start is not None:
                out.append((start, None))      # entrée sans sortie
            start = e["ts"]
        else:
            if start is not None:
                out.append((start, e["ts"]))
                start = None
            else:
                out.append((None, e["ts"]))    # sortie sans entrée
    if start is not None:
        out.append((start, None))
    return out


def check_network():
    """Restreint le pointage au réseau local si TEMPORA_ALLOWED_NETS est défini.
    Derrière un reverse proxy, remote_addr sera celui du proxy : à adapter."""
    if not ALLOWED_NETS:
        return
    try:
        ip = ipaddress.ip_address(request.remote_addr)
    except ValueError:
        abort(403)
    if not any(ip in n for n in ALLOWED_NETS):
        abort(403)


def require_admin():
    a = request.authorization
    ok = (ADMIN_PASSWORD and a and a.username == ADMIN_USER
          and secrets.compare_digest(a.password or "", ADMIN_PASSWORD))
    if not ok:
        abort(Response("Authentification requise", 401,
                       {"WWW-Authenticate": 'Basic realm="Tempora"'}))


def employee_by_token(token):
    e = db().execute("SELECT * FROM employees WHERE token=? AND active=1",
                     (token,)).fetchone()
    if e is None:
        abort(404)
    return e


# --------------------------------------------------------------------------- #
# Pages salarié
# --------------------------------------------------------------------------- #
CSS = """
:root{--bg:#f6f5f0;--fg:#1d2a1f;--card:#fff;--go:#2e7d32;--stop:#c62828;--mut:#6b7a6e;--bd:#dcdad0;--warn:#b26a00}
@media(prefers-color-scheme:dark){:root{--bg:#141a15;--fg:#e8efe9;--card:#1d261f;--mut:#9aa89d;--bd:#2c382e;--warn:#e0a040}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:640px;margin:0 auto;padding:1.5rem 1rem 3rem}
main.wide{max-width:960px}
h1{font-size:1.5rem;margin:.2rem 0 1rem}h2{font-size:1.15rem;margin:1.5rem 0 .5rem}
button{font:inherit;cursor:pointer}
.big{width:100%;padding:2.4rem 1rem;font-size:2.2rem;font-weight:700;color:#fff;border:0;border-radius:1rem}
.go{background:var(--go)}.stop{background:var(--stop)}
.state{font-size:1.1rem;margin:0 0 1rem}.on{color:var(--go);font-weight:600}.off{color:var(--mut)}
.ok{background:var(--go);color:#fff;padding:.5rem .8rem;border-radius:.5rem;margin-bottom:1rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:.8rem;padding:1rem;margin:1rem 0}
.mut{color:var(--mut)}.warn{color:var(--warn)}
ul{padding-left:1.1rem}
table{border-collapse:collapse;width:100%;font-size:.95rem}
td,th{padding:.3rem .5rem;border-bottom:1px solid var(--bd);text-align:left}
code{word-break:break-all;font-size:.85rem}
form.inline{display:inline}
.small{font-size:.85rem;padding:.2rem .6rem}
input,select{font:inherit;padding:.3rem;background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:.4rem}
"""

PAGE_ME = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tempora</title><style>{{ css|safe }}</style></head><body><main>
<h1>Bonjour {{ name }}</h1>
{% if ok %}<div class="ok">Enregistré ✓</div>{% endif %}
<p class="state {{ 'on' if inside else 'off' }}">
{% if inside %}En poste depuis {{ since }}{% else %}Hors poste{% endif %}</p>
<form method="post">
  <input type="hidden" name="next" value="{{ 'out' if inside else 'in' }}">
  <button class="big {{ 'stop' if inside else 'go' }}">{{ 'Départ' if inside else 'Arrivée' }}</button>
</form>
<p>Cette semaine : <b>{{ week }}</b></p>
<h2>Derniers pointages</h2>
<ul>{% for x in recent %}<li>{{ x.label }} — {{ x.when }}</li>{% else %}<li class="mut">Aucun pointage</li>{% endfor %}</ul>
</main></body></html>"""


@app.get("/p/<token>")
def me(token):
    check_network()
    e = employee_by_token(token)
    evs = db().execute("SELECT * FROM events WHERE employee_id=? ORDER BY ts",
                       (e["id"],)).fetchall()
    last = evs[-1] if evs else None
    inside = bool(last and last["kind"] == "in")
    monday = local(now()).replace(hour=0, minute=0, second=0, microsecond=0)
    monday -= timedelta(days=monday.weekday())
    ws = int(monday.timestamp())
    week = sum(b - a for a, b in shifts([x for x in evs if x["ts"] >= ws]) if a and b)
    recent = [dict(label="Arrivée" if x["kind"] == "in" else "Départ", when=dt(x["ts"]))
              for x in reversed(evs[-6:])]
    return render_template_string(
        PAGE_ME, css=CSS, name=e["name"], inside=inside,
        since=local(last["ts"]).strftime("%H:%M") if inside else "",
        week=fmt_h(week), recent=recent, ok=request.args.get("ok"))


@app.post("/p/<token>")
def toggle(token):
    check_network()
    e = employee_by_token(token)
    last = db().execute("SELECT * FROM events WHERE employee_id=? ORDER BY ts DESC LIMIT 1",
                        (e["id"],)).fetchone()
    t = now()
    kind = "out" if last and last["kind"] == "in" else "in"
    # Page périmée (état déjà changé) ou double appui : on n'enregistre rien.
    if request.form.get("next") != kind or (last and t - last["ts"] < MIN_GAP):
        return redirect(url_for("me", token=token))
    db().execute("INSERT INTO events(employee_id, ts, kind) VALUES (?,?,?)",
                 (e["id"], t, kind))
    db().commit()
    return redirect(url_for("me", token=token, ok=1))


# --------------------------------------------------------------------------- #
# Admin
# --------------------------------------------------------------------------- #
def get_month():
    m = request.values.get("m", "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m):
        m = local(now()).strftime("%Y-%m")
    return m


def month_bounds(m):
    y, mo = map(int, m.split("-"))
    a = datetime(y, mo, 1, tzinfo=TZ)
    b = datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=TZ)
    return int(a.timestamp()), int(b.timestamp())


def month_nav(m):
    y, mo = map(int, m.split("-"))
    return (f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}",
            f"{y + (mo == 12)}-{mo % 12 + 1:02d}")


def month_report(m):
    a, b = month_bounds(m)
    rows = []
    for e in db().execute("SELECT * FROM employees ORDER BY active DESC, name").fetchall():
        evs = db().execute(
            "SELECT * FROM events WHERE employee_id=? AND ts>=? AND ts<? ORDER BY ts",
            (e["id"], a, b)).fetchall()
        days = {}
        for s, t in shifts(evs):
            days.setdefault(local(s or t).date(), []).append((s, t))
        total = sum(t - s for ss in days.values() for s, t in ss if s and t)
        rows.append(dict(emp=e, evs=evs, days=sorted(days.items()), total=total))
    return rows


def day_rows(days):
    out = []
    for d, ss in days:
        parts, dur, incomplete = [], 0, False
        for s, t in ss:
            if s and t:
                parts.append(f"{local(s):%H:%M}–{local(t):%H:%M}")
                dur += t - s
            else:
                incomplete = True
                parts.append(f"{local(s):%H:%M}–?" if s else f"?–{local(t):%H:%M}")
        out.append(dict(label=f"{d:%d/%m}", plages=", ".join(parts),
                        dur=fmt_h(dur), incomplete=incomplete))
    return out


PAGE_ADMIN = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tempora — Admin</title><style>{{ css|safe }}</style></head><body><main class="wide">
<h1>Tempora — Admin</h1>
<p><a href="?m={{ prev }}">←</a> <b>{{ m }}</b> <a href="?m={{ nxt }}">→</a>
 · <a href="{{ url_for('export', m=m) }}">Export CSV</a></p>
{% for r in rows %}
<section class="card">
  <h2>{{ r.emp.name }}{% if not r.emp.active %} <small class="mut">(désactivé)</small>{% endif %}
      — {{ fmt(r.total) }}</h2>
  <p class="mut">Lien : <code>{{ base }}/p/{{ r.emp.token }}</code></p>
  <table>
    <tr><th>Jour</th><th>Plages</th><th>Durée</th></tr>
    {% for d in r.view %}
    <tr><td>{{ d.label }}</td>
        <td>{{ d.plages }}{% if d.incomplete %} <span class="warn">⚠ incomplet</span>{% endif %}</td>
        <td>{{ d.dur }}</td></tr>
    {% else %}<tr><td colspan="3" class="mut">Aucun pointage ce mois-ci</td></tr>{% endfor %}
  </table>
  <details><summary>Pointages bruts ({{ r.evs|length }}) / correction</summary>
    <table>
    {% for x in r.evs %}
      <tr><td>{{ dt(x.ts) }}</td><td>{{ 'Arrivée' if x.kind == 'in' else 'Départ' }}</td>
          <td class="mut">{{ x.source }}</td>
          <td><form class="inline" method="post" action="{{ url_for('del_event', eid=x.id) }}"
                    onsubmit="return confirm('Supprimer ce pointage ?')">
              <input type="hidden" name="m" value="{{ m }}">
              <button class="small">Supprimer</button></form></td></tr>
    {% endfor %}
    </table>
    <form method="post" action="{{ url_for('add_event') }}" style="margin-top:.8rem">
      <input type="hidden" name="m" value="{{ m }}">
      <input type="hidden" name="employee_id" value="{{ r.emp.id }}">
      <input type="datetime-local" name="when" required>
      <select name="kind"><option value="in">Arrivée</option><option value="out">Départ</option></select>
      <button class="small">Ajouter</button>
    </form>
  </details>
</section>
{% else %}<p class="mut">Aucun salarié. Utiliser : <code>python app.py add "Prénom"</code></p>{% endfor %}
<h2>Journal des modifications</h2>
<ul>{% for a in audit %}<li class="mut">{{ dt(a.ts) }} — {{ a.action }} : {{ a.detail }}</li>
{% else %}<li class="mut">Aucune modification manuelle</li>{% endfor %}</ul>
</main></body></html>"""


@app.get("/admin")
def admin():
    require_admin()
    m = get_month()
    rows = month_report(m)
    for r in rows:
        r["view"] = day_rows(r["days"])
    prev, nxt = month_nav(m)
    base = os.environ.get("TEMPORA_BASE_URL") or request.url_root.rstrip("/")
    audits = db().execute("SELECT * FROM audit ORDER BY id DESC LIMIT 15").fetchall()
    return render_template_string(PAGE_ADMIN, css=CSS, rows=rows, m=m, prev=prev, nxt=nxt,
                                  base=base, audit=audits, fmt=fmt_h, dt=dt)


@app.post("/admin/event")
def add_event():
    require_admin()
    try:
        when = datetime.strptime(request.form["when"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
        eid = int(request.form["employee_id"])
    except (KeyError, ValueError):
        abort(400)
    kind = request.form.get("kind")
    if kind not in ("in", "out"):
        abort(400)
    emp = db().execute("SELECT name FROM employees WHERE id=?", (eid,)).fetchone()
    if emp is None:
        abort(400)
    db().execute("INSERT INTO events(employee_id, ts, kind, source) VALUES (?,?,?, 'admin')",
                 (eid, int(when.timestamp()), kind))
    audit("ajout", f"{emp['name']} {kind} {when:%d/%m/%Y %H:%M}")
    db().commit()
    return redirect(url_for("admin", m=get_month()))


@app.post("/admin/event/<int:eid>/delete")
def del_event(eid):
    require_admin()
    x = db().execute("SELECT ev.*, e.name FROM events ev JOIN employees e ON e.id=ev.employee_id "
                     "WHERE ev.id=?", (eid,)).fetchone()
    if x is None:
        abort(404)
    db().execute("DELETE FROM events WHERE id=?", (eid,))
    audit("suppression", f"{x['name']} {x['kind']} {dt(x['ts'])} (source {x['source']})")
    db().commit()
    return redirect(url_for("admin", m=get_month()))


@app.get("/admin/export.csv")
def export():
    require_admin()
    m = get_month()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Salarié", "Date", "Début", "Fin", "Durée (h)"])
    for r in month_report(m):
        for d, ss in r["days"]:
            for s, t in ss:
                w.writerow([
                    r["emp"]["name"], d.strftime("%d/%m/%Y"),
                    local(s).strftime("%H:%M") if s else "",
                    local(t).strftime("%H:%M") if t else "",
                    f"{(t - s) / 3600:.2f}".replace(".", ",") if s and t else "",
                ])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="tempora-{m}.csv"'})


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #
def cli(argv):
    base = os.environ.get("TEMPORA_BASE_URL", "http://<ip-du-rpi>:8080")
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "serve":
        app.run(host="0.0.0.0", port=int(os.environ.get("TEMPORA_PORT", "8080")))
        return
    c = sqlite3.connect(DB_PATH)
    if cmd == "add" and len(argv) > 2:
        token = secrets.token_urlsafe(16)
        c.execute("INSERT INTO employees(name, token) VALUES (?,?)", (argv[2], token))
        c.commit()
        print(f"{argv[2]} : {base}/p/{token}")
    elif cmd == "list":
        for i, n, tok, act in c.execute("SELECT id, name, token, active FROM employees"):
            print(f"[{i}] {n}{'' if act else ' (désactivé)'} : {base}/p/{tok}")
    elif cmd == "disable" and len(argv) > 2:
        c.execute("UPDATE employees SET active=0 WHERE id=?", (int(argv[2]),))
        c.commit()
    else:
        print(__doc__)
    c.close()


init_db()

if __name__ == "__main__":
    cli(sys.argv)
