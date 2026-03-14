# ============================================================
#  server/api_server.py
#  Serveur HTTP API — Expose les données en temps réel au dashboard
#
#  CONCEPT : Comment ça fonctionne ?
#  ─────────────────────────────────────────────────────────
#  Ce fichier ajoute un DEUXIÈME serveur qui tourne EN PARALLÈLE
#  du serveur TCP principal (server.py), dans le même processus.
#
#  Architecture :
#
#  server.py (TCP :9000)          api_server.py (HTTP :8080)
#       │                               │
#       │ reçoit métriques              │ répond aux requêtes
#       │ des agents Python             │ du dashboard HTML
#       │                               │
#       └──────────► node_tracker ◄─────┘
#                   (mémoire partagée)
#
#  Le dashboard (index.html) fait des requêtes HTTP vers :
#    GET  http://localhost:8080/api/nodes    → état de tous les nœuds
#    GET  http://localhost:8080/api/alerts   → dernières alertes
#    GET  http://localhost:8080/api/history  → historique CPU/RAM
#    POST http://localhost:8080/api/command  → envoyer une commande
#
#  CORS (Cross-Origin Resource Sharing) :
#    Le dashboard (fichier local) et l'API (localhost:8080) ont des
#    "origines" différentes. Sans en-têtes CORS, le navigateur bloque
#    les requêtes. On ajoute donc les en-têtes nécessaires.
# ============================================================

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import logging

logger = logging.getLogger("API")

# Référence vers le node_tracker importé depuis server.py
# (injecté via set_tracker())
_node_tracker = None
_db_pool      = None

def set_tracker(tracker, db):
    """Injecte le node_tracker et db_pool depuis server.py."""
    global _node_tracker, _db_pool
    _node_tracker = tracker
    _db_pool      = db


# ─── Historique des métriques (60 points pour les graphiques) ────────────────
_history_lock = threading.Lock()
_history = {}   # { node_id: { "cpu": [v1,v2,...], "mem": [...], "disk": [...] } }

def record_history(node_id: str, cpu: float, mem: float, disk: float):
    """Appelé à chaque réception de métriques pour mettre à jour l'historique."""
    with _history_lock:
        if node_id not in _history:
            _history[node_id] = {"cpu": [], "mem": [], "disk": [], "timestamps": []}
        h = _history[node_id]
        h["cpu"].append(round(cpu, 1))
        h["mem"].append(round(mem, 1))
        h["disk"].append(round(disk, 1))
        h["timestamps"].append(time.strftime("%H:%M:%S"))
        # Garder seulement les 60 derniers points
        for key in ["cpu", "mem", "disk", "timestamps"]:
            if len(h[key]) > 60:
                h[key] = h[key][-60:]


# ─── Handler HTTP ─────────────────────────────────────────────────────────────

class APIHandler(BaseHTTPRequestHandler):
    """
    Gestionnaire des requêtes HTTP.
    Pour chaque requête GET/POST reçue, la méthode correspondante est appelée.
    """

    def log_message(self, format, *args):
        """Surcharge pour utiliser notre logger au lieu de stderr."""
        logger.debug(f"HTTP {self.address_string()} - {format % args}")

    def send_json(self, data: dict | list, status: int = 200):
        """Envoie une réponse JSON avec les en-têtes CORS."""
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        # ── En-têtes CORS ──────────────────────────────────────────────
        # Permet au dashboard HTML (fichier local ou autre origine) d'appeler l'API
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Répond aux requêtes preflight CORS (envoyées par le navigateur avant POST)."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Traite les requêtes GET."""
        path = urlparse(self.path).path

        # ── GET /api/nodes ─────────────────────────────────────────────
        if path == "/api/nodes":
            if _node_tracker is None:
                return self.send_json({"error": "Tracker non initialisé"}, 503)

            nodes_raw = _node_tracker.get_all()
            nodes_list = []
            now = time.time()

            for node_id, info in nodes_raw.items():
                elapsed = now - info["last_seen"]
                nodes_list.append({
                    "id":       node_id,
                    "status":   info["status"],
                    "os":       info.get("os", "?"),
                    "cpu":      round(info.get("cpu", 0), 1),
                    "mem":      round(info.get("memory", 0), 1),
                    "disk":     round(info.get("disk", 0), 1),
                    "uptime":   info.get("uptime", 0),
                    "services": info.get("services", {}),
                    "ports":    info.get("ports", {}),
                    "last_seen_ago": round(elapsed),
                })

            self.send_json({
                "nodes":      nodes_list,
                "total":      len(nodes_list),
                "active":     sum(1 for n in nodes_list if n["status"] in ("ACTIVE","alert")),
                "down":       sum(1 for n in nodes_list if n["status"] == "DOWN"),
                "server_time": time.strftime("%H:%M:%S"),
            })

        # ── GET /api/alerts ────────────────────────────────────────────
        elif path == "/api/alerts":
            try:
                rows = _db_pool.execute_query(
                    """SELECT node_id, alert_type, message, created_at
                       FROM alerts
                       ORDER BY created_at DESC
                       LIMIT 50""",
                    fetch=True
                )
                self.send_json(rows or [])
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        # ── GET /api/history?node=xxx ──────────────────────────────────
        elif path == "/api/history":
            qs     = parse_qs(urlparse(self.path).query)
            target = qs.get("node", [None])[0]

            with _history_lock:
                if target and target in _history:
                    self.send_json(_history[target])
                else:
                    # Retourne l'historique agrégé (moyenne de tous les nœuds)
                    if _history:
                        max_len = max(len(v["cpu"]) for v in _history.values())
                        avg_cpu  = []
                        avg_mem  = []
                        avg_disk = []
                        timestamps = []
                        for i in range(max_len):
                            cpus  = [v["cpu"][i]  for v in _history.values() if i < len(v["cpu"])]
                            mems  = [v["mem"][i]  for v in _history.values() if i < len(v["mem"])]
                            disks = [v["disk"][i] for v in _history.values() if i < len(v["disk"])]
                            tss   = [v["timestamps"][i] for v in _history.values() if i < len(v["timestamps"])]
                            avg_cpu.append(round(sum(cpus)/len(cpus), 1) if cpus else 0)
                            avg_mem.append(round(sum(mems)/len(mems), 1) if mems else 0)
                            avg_disk.append(round(sum(disks)/len(disks), 1) if disks else 0)
                            timestamps.append(tss[0] if tss else "")
                        self.send_json({"cpu": avg_cpu, "mem": avg_mem,
                                        "disk": avg_disk, "timestamps": timestamps})
                    else:
                        self.send_json({"cpu": [], "mem": [], "disk": [], "timestamps": []})

        # ── GET /api/stats ─────────────────────────────────────────────
        elif path == "/api/stats":
            try:
                rows = _db_pool.execute_query(
                    """SELECT
                         COUNT(DISTINCT node_id)                    AS total_nodes,
                         SUM(alert_type='CPU_HIGH')                 AS cpu_alerts,
                         SUM(alert_type='MEM_HIGH')                 AS mem_alerts,
                         SUM(alert_type='NODE_DOWN')                AS down_alerts
                       FROM alerts
                       WHERE created_at >= NOW() - INTERVAL 1 HOUR""",
                    fetch=True
                )
                self.send_json(rows[0] if rows else {})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        # ── Route inconnue ─────────────────────────────────────────────
        else:
            self.send_json({"error": f"Route inconnue : {path}"}, 404)

    def do_POST(self):
        """Traite les requêtes POST."""
        path = urlparse(self.path).path

        # ── POST /api/command ──────────────────────────────────────────
        if path == "/api/command":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length).decode("utf-8")
                data   = json.loads(body)

                node_id = data.get("node")
                command = data.get("command", "").upper()

                if not node_id or not command:
                    return self.send_json({"error": "Champs 'node' et 'command' requis"}, 400)

                # Enregistrer la commande via le tracker
                success = _node_tracker.send_command(node_id, command)
                if success:
                    logger.info(f"📤 Commande API : '{command}' → {node_id}")
                    self.send_json({"status": "ok", "message": f"Commande '{command}' programmée pour '{node_id}'"})
                else:
                    self.send_json({"error": "Impossible d'enregistrer la commande"}, 500)

            except json.JSONDecodeError:
                self.send_json({"error": "JSON invalide"}, 400)
            except Exception as e:
                self.send_json({"error": str(e)}, 500)

        else:
            self.send_json({"error": f"Route POST inconnue : {path}"}, 404)


# ─── Démarrage du serveur HTTP dans un thread dédié ──────────────────────────

def start_api_server(host: str = "0.0.0.0", port: int = 8080):
    """
    Lance le serveur HTTP dans un thread daemon.
    Daemon = s'arrête automatiquement quand le programme principal s'arrête.
    """
    server = HTTPServer((host, port), APIHandler)
    logger.info(f"🌐 API HTTP démarrée sur http://{host}:{port}")
    logger.info(f"   Endpoints disponibles :")
    logger.info(f"   GET  http://localhost:{port}/api/nodes")
    logger.info(f"   GET  http://localhost:{port}/api/alerts")
    logger.info(f"   GET  http://localhost:{port}/api/history")
    logger.info(f"   POST http://localhost:{port}/api/command")
    server.serve_forever()


def launch_in_thread(host: str = "0.0.0.0", port: int = 8080):
    """Démarre l'API dans un thread séparé (non-bloquant)."""
    t = threading.Thread(
        target=start_api_server,
        args=(host, port),
        daemon=True,
        name="api-http"
    )
    t.start()
    return t
