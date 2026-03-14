# ============================================================
#  server/server.py
#  Serveur de Supervision Multi-Clients
#
#  CONCEPT : Serveur Multi-Threads avec Pool
#  ─────────────────────────────────────────────────────────
#  Un serveur séquentiel traite les clients UN PAR UN → trop lent.
#
#  Avec les threads :
#    - Le thread principal accepte les connexions (accept())
#    - Pour chaque client → crée un thread dédié
#    - Les threads s'exécutent en PARALLÈLE
#
#  Comparaison des types de pools (ThreadPoolExecutor) :
#  ┌──────────────────┬───────────────┬──────────────────┐
#  │ Type             │ Avantage      │ Inconvénient     │
#  ├──────────────────┼───────────────┼──────────────────┤
#  │ Thread fixe      │ Prévisible    │ Sous-utilisation │
#  │ (FixedThreadPool)│ Simple        │ si peu de clients│
#  ├──────────────────┼───────────────┼──────────────────┤
#  │ Thread dynamique │ S'adapte à    │ Risque surcharge │
#  │ (CachedPool)     │ la charge     │ si trop de clients│
#  ├──────────────────┼───────────────┼──────────────────┤
#  │ Thread schedulé  │ Tâches        │ Complexe         │
#  │ (Scheduled)      │ périodiques   │                  │
#  └──────────────────┴───────────────┴──────────────────┘
#
#  → CHOIX : ThreadPoolExecutor avec max_workers fixe (20)
#    Raison : supervision = charge régulière et prévisible
#    On veut éviter la surcharge avec trop de threads
# ============================================================

import socket
import threading
import logging
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.protocol import (
    parse_message, validate_metrics, check_alerts,
    build_ack_message, build_error_message, build_command_message,
    MSG_METRICS, TIMEOUT_NODE_DOWN, MESSAGE_DELIMITER
)
from database.db_pool import db_pool
from server.api_server import launch_in_thread, set_tracker, record_history

# ─── Configuration ────────────────────────────────────────────────────────────
SERVER_HOST  = "0.0.0.0"    # Écoute sur toutes les interfaces réseau
SERVER_PORT  = 9000
MAX_WORKERS  = 20            # Taille du pool de threads
LOG_FILE     = "server/supervision.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),                    # Affichage console
        logging.FileHandler(LOG_FILE, encoding="utf-8"),  # Fichier log
    ]
)
logger = logging.getLogger("SERVEUR")


# ─── Gestionnaire d'état des nœuds ───────────────────────────────────────────

class NodeTracker:
    """
    Suit l'état de tous les nœuds connectés.
    
    CONCEPT : Thread-safety
    ─────────────────────────
    Plusieurs threads lisent/écrivent nodes_status en même temps.
    Sans protection → race condition (données corrompues).
    Solution : threading.Lock() → un seul thread à la fois modifie les données.
    
    lock.acquire() → prend le verrou
    lock.release() → libère le verrou
    Ou plus simple : with self.lock:  (acquire + release automatique)
    """

    def __init__(self):
        self.nodes_status = {}   # {node_id: {"last_seen": timestamp, "status": str, ...}}
        self.lock = threading.Lock()

    def update(self, node_id: str, data: dict):
        """Met à jour le dernier contact d'un nœud."""
        with self.lock:
            self.nodes_status[node_id] = {
                "last_seen": time.time(),
                "status":    "ACTIVE",
                "cpu":       data.get("cpu", 0),
                "memory":    data.get("memory", 0),
                "disk":      data.get("disk", 0),
                "uptime":    data.get("uptime", 0),
                "os":        data.get("os", "?"),
                "services":  data.get("services", {}),
                "ports":     data.get("ports", {}),
            }

    def check_timeouts(self):
        """Marque les nœuds comme DOWN si pas de données depuis TIMEOUT_NODE_DOWN secondes."""
        with self.lock:
            now = time.time()
            for node_id, info in self.nodes_status.items():
                elapsed = now - info["last_seen"]
                was_active = info["status"] == "ACTIVE"
                if elapsed > TIMEOUT_NODE_DOWN and was_active:
                    info["status"] = "DOWN"
                    logger.warning(f"🔴 NŒUD EN PANNE : {node_id} (pas de données depuis {elapsed:.0f}s)")
                    # Journaliser l'alerte en base
                    try:
                        db_pool.execute_query(
                            "INSERT INTO alerts (node_id, alert_type, message) VALUES (%s, %s, %s)",
                            params=(node_id, "NODE_DOWN",
                                    f"Nœud {node_id} en panne - timeout {elapsed:.0f}s")
                        )
                    except Exception:
                        pass

    def get_all(self) -> dict:
        """Retourne une copie de l'état de tous les nœuds."""
        with self.lock:
            return dict(self.nodes_status)

    def send_command(self, node_id: str, command: str) -> bool:
        """Enregistre une commande en base pour qu'elle soit envoyée au prochain cycle."""
        try:
            db_pool.execute_query(
                "INSERT INTO commands (node_id, command) VALUES (%s, %s)",
                params=(node_id, command)
            )
            logger.info(f"📝 Commande '{command}' enregistrée pour {node_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur enregistrement commande : {e}")
            return False


# Instance globale du tracker
node_tracker = NodeTracker()


# ─── Traitement d'un client (exécuté dans un thread du pool) ─────────────────

def handle_client(client_socket: socket.socket, client_address: tuple):
    """
    Gère la connexion d'un client.
    Cette fonction est exécutée dans un thread du pool.
    
    CONCEPT : Un thread par client
    ────────────────────────────────
    Chaque appel à cette fonction s'exécute dans son propre thread.
    Les threads partagent la mémoire (node_tracker) mais ont leur
    propre pile d'exécution et variables locales.
    """
    ip, port = client_address
    logger.info(f"🔌 Nouveau client connecté : {ip}:{port}")

    buffer = ""
    node_id = None

    try:
        client_socket.settimeout(TIMEOUT_NODE_DOWN + 10)   # Timeout légèrement > 90s

        while True:
            # ── Réception des données ─────────────────────────────────────
            try:
                chunk = client_socket.recv(4096).decode("utf-8")
                if not chunk:
                    logger.info(f"🔌 Client {ip}:{port} déconnecté proprement")
                    break
                buffer += chunk
            except socket.timeout:
                logger.warning(f"⏳ Timeout client {ip}:{port}")
                break
            except OSError:
                break

            # ── Traitement des messages complets ──────────────────────────
            while MESSAGE_DELIMITER in buffer:
                line, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                if not line.strip():
                    continue

                # Parser le JSON
                data = parse_message(line)
                if data is None:
                    error_resp = build_error_message("Format JSON invalide")
                    client_socket.sendall(error_resp.encode("utf-8"))
                    logger.warning(f"⚠️  Message invalide de {ip}:{port} : {line[:50]}...")
                    continue

                # Valider les métriques
                valid, reason = validate_metrics(data)
                if not valid:
                    error_resp = build_error_message(reason)
                    client_socket.sendall(error_resp.encode("utf-8"))
                    logger.warning(f"⚠️  Métriques invalides de {ip}:{port} : {reason}")
                    continue

                node_id = data["node"]
                logger.info(f"📥 Métriques reçues de {node_id} | CPU:{data['cpu']}% RAM:{data['memory']}% Disque:{data['disk']}%")

                # Mettre à jour le tracker
                node_tracker.update(node_id, data)

                # Sauvegarder en base de données
                save_metrics_to_db(data)

                # Vérifier les alertes de seuil
                alerts = check_alerts(data)
                for alert_msg in alerts:
                    logger.warning(alert_msg)
                    save_alert_to_db(node_id, data, alert_msg)

                # Envoyer l'ACK au client
                ack = build_ack_message(node_id)
                client_socket.sendall(ack.encode("utf-8"))

                # Vérifier s'il y a une commande en attente pour ce nœud
                pending_cmd = get_pending_command(node_id)
                if pending_cmd:
                    cmd_msg = build_command_message(node_id, pending_cmd)
                    client_socket.sendall(cmd_msg.encode("utf-8"))
                    logger.info(f"📤 Commande '{pending_cmd}' envoyée à {node_id}")

    except Exception as e:
        logger.error(f"❌ Erreur avec client {ip}:{port} : {e}")
    finally:
        client_socket.close()
        if node_id:
            logger.info(f"🔴 Connexion fermée : {node_id} ({ip}:{port})")
        else:
            logger.info(f"🔴 Connexion fermée : {ip}:{port}")


# ─── Fonctions de persistance ─────────────────────────────────────────────────

def save_metrics_to_db(data: dict):
    """Sauvegarde les métriques en base de données ET met à jour l'historique API."""
    # Enregistre dans l'historique temps réel pour le dashboard
    record_history(data["node"], data.get("cpu", 0), data.get("memory", 0), data.get("disk", 0))
    try:
        svc = data.get("services", {})
        pts = data.get("ports", {})

        # Créer le nœud s'il n'existe pas encore
        db_pool.execute_query(
            """INSERT INTO nodes (node_id, os, cpu_type)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE last_seen=NOW(), status='ACTIVE'""",
            params=(data["node"], data.get("os"), data.get("cpu_type"))
        )

        # Insérer les métriques
        db_pool.execute_query(
            """INSERT INTO metrics
               (node_id, timestamp, cpu_load, memory_load, disk_load, uptime,
                svc_http, svc_ssh, svc_dns, app_chrome, app_firefox, app_vscode,
                port_80, port_443, port_22, port_3306)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            params=(
                data["node"], data["timestamp"],
                data.get("cpu"), data.get("memory"), data.get("disk"), data.get("uptime"),
                svc.get("http"), svc.get("ssh"), svc.get("dns"),
                svc.get("chrome"), svc.get("firefox"), svc.get("vscode"),
                pts.get("80"), pts.get("443"), pts.get("22"), pts.get("3306"),
            )
        )
    except Exception as e:
        logger.error(f"❌ Erreur sauvegarde BD : {e}")


def save_alert_to_db(node_id: str, data: dict, message: str):
    """Journalise une alerte en base de données."""
    alert_type = "CPU_HIGH" if "CPU" in message else \
                 "MEM_HIGH" if "Mémoire" in message else "DISK_HIGH"
    try:
        db_pool.execute_query(
            "INSERT INTO alerts (node_id, alert_type, message) VALUES (%s, %s, %s)",
            params=(node_id, alert_type, message)
        )
    except Exception as e:
        logger.error(f"❌ Erreur sauvegarde alerte : {e}")


def get_pending_command(node_id: str) -> str | None:
    """Récupère la première commande en attente pour un nœud."""
    try:
        rows = db_pool.execute_query(
            "SELECT id, command FROM commands WHERE node_id=%s AND executed=FALSE ORDER BY sent_at LIMIT 1",
            params=(node_id,), fetch=True
        )
        if rows:
            cmd_id = rows[0]["id"]
            command = rows[0]["command"]
            db_pool.execute_query(
                "UPDATE commands SET executed=TRUE WHERE id=%s", params=(cmd_id,)
            )
            return command
    except Exception:
        pass
    return None


# ─── Thread de surveillance des timeouts ─────────────────────────────────────

def timeout_monitor():
    """
    Thread dédié à la vérification des timeouts.
    Tourne en arrière-plan, vérifie toutes les 30 secondes.
    C'est un daemon thread : s'arrête automatiquement quand le programme principal s'arrête.
    """
    logger.info("🔍 Monitor de timeouts démarré")
    while True:
        time.sleep(30)
        node_tracker.check_timeouts()


# ─── Serveur principal ────────────────────────────────────────────────────────

class SupervisionServer:
    """Serveur TCP multi-clients avec pool de threads."""

    def __init__(self):
        self.server_socket = None
        self.executor = None
        self.running = False

    def start(self):
        """Démarre le serveur."""
        # Initialiser le pool de connexions BD
        try:
            db_pool.initialize()
        except Exception as e:
            logger.error(f"Impossible de se connecter à MySQL : {e}")
            logger.error("Assurez-vous que MySQL tourne et que les paramètres sont corrects dans db_pool.py")
            sys.exit(1)

        # Créer le socket serveur
        # SO_REUSEADDR : permet de réutiliser le port immédiatement après arrêt
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((SERVER_HOST, SERVER_PORT))
        self.server_socket.listen(50)   # File d'attente de 50 connexions max

        logger.info(f"🚀 Serveur TCP démarré sur {SERVER_HOST}:{SERVER_PORT}")
        logger.info(f"🧵 Pool de threads : {MAX_WORKERS} threads maximum")
        logger.info(f"📂 Logs dans : {LOG_FILE}")

        # Démarrer l'API HTTP pour le dashboard
        set_tracker(node_tracker, db_pool)
        launch_in_thread(host="0.0.0.0", port=8080)
        logger.info(f"🌐 Dashboard : ouvrez dashboard/index.html dans votre navigateur")

        # Démarrer le thread de surveillance des timeouts
        monitor_thread = threading.Thread(target=timeout_monitor, daemon=True)
        monitor_thread.start()

        # Démarrer le pool de threads
        # CONCEPT : ThreadPoolExecutor
        # ─────────────────────────────────
        # max_workers=20 → au maximum 20 threads actifs simultanément
        # Si un 21ème client arrive, sa tâche attend dans la queue
        # jusqu'à ce qu'un thread se libère
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS,
                                           thread_name_prefix="client-handler")
        self.running = True

        logger.info("⌨️  Tapez 'help' dans la console pour les commandes disponibles")
        logger.info("─" * 60)

        # Thread pour la console d'administration
        console_thread = threading.Thread(target=self.console_interface, daemon=True)
        console_thread.start()

        # ── Boucle principale : accepter les connexions ───────────────────
        try:
            while self.running:
                try:
                    client_sock, client_addr = self.server_socket.accept()
                    # Soumettre le traitement du client au pool de threads
                    # submit() est NON-BLOQUANT : retourne immédiatement
                    self.executor.submit(handle_client, client_sock, client_addr)
                except OSError:
                    if self.running:
                        raise
        except KeyboardInterrupt:
            print("\n⛔ Arrêt du serveur...")
        finally:
            self.stop()

    def stop(self):
        """Arrête proprement le serveur."""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        if self.executor:
            self.executor.shutdown(wait=False)
        db_pool.close_all()
        logger.info("Serveur arrêté.")

    def console_interface(self):
        """
        Interface console pour l'administration.
        S'exécute dans un thread séparé pour ne pas bloquer le serveur.
        """
        commands_help = """
╔══════════════════════════════════════════════════════╗
║           CONSOLE D'ADMINISTRATION                   ║
╠══════════════════════════════════════════════════════╣
║  list               → Lister tous les nœuds          ║
║  status <node>      → Détail d'un nœud               ║
║  alerts             → Voir les dernières alertes     ║
║  cmd <node> <cmd>   → Envoyer une commande           ║
║                       Ex: cmd server1 UP_HTTP        ║
║  help               → Afficher cette aide            ║
║  quit               → Arrêter le serveur             ║
╚══════════════════════════════════════════════════════╝"""

        print(commands_help)

        while self.running:
            try:
                user_input = input("\n> ").strip()
                if not user_input:
                    continue

                parts = user_input.split()
                cmd = parts[0].lower()

                if cmd == "help":
                    print(commands_help)

                elif cmd == "list":
                    nodes = node_tracker.get_all()
                    if not nodes:
                        print("Aucun nœud connecté.")
                    else:
                        print(f"\n{'NODE':<20} {'STATUS':<10} {'CPU%':<8} {'RAM%':<8} {'DISK%':<8} {'DERNIÈRE VUE'}")
                        print("─" * 70)
                        for nid, info in nodes.items():
                            last = datetime.fromtimestamp(info["last_seen"]).strftime("%H:%M:%S")
                            status_icon = "🟢" if info["status"] == "ACTIVE" else "🔴"
                            print(f"{nid:<20} {status_icon} {info['status']:<8} "
                                  f"{info['cpu']:<8.1f} {info['memory']:<8.1f} "
                                  f"{info['disk']:<8.1f} {last}")

                elif cmd == "status" and len(parts) >= 2:
                    node_id = parts[1]
                    nodes = node_tracker.get_all()
                    if node_id in nodes:
                        info = nodes[node_id]
                        print(f"\n📊 Nœud : {node_id}")
                        print(f"   Status    : {info['status']}")
                        print(f"   OS        : {info['os']}")
                        print(f"   CPU       : {info['cpu']}%")
                        print(f"   RAM       : {info['memory']}%")
                        print(f"   Disque    : {info['disk']}%")
                        print(f"   Uptime    : {info['uptime']}s")
                        print(f"   Services  : {info['services']}")
                        print(f"   Ports     : {info['ports']}")
                    else:
                        print(f"Nœud '{node_id}' non trouvé.")

                elif cmd == "alerts":
                    try:
                        rows = db_pool.execute_query(
                            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 20",
                            fetch=True
                        )
                        if rows:
                            print(f"\n{'DATE':<20} {'NODE':<15} {'TYPE':<15} {'MESSAGE'}")
                            print("─" * 80)
                            for row in rows:
                                print(f"{str(row['created_at']):<20} {row['node_id']:<15} "
                                      f"{row['alert_type']:<15} {row['message'][:50]}")
                        else:
                            print("Aucune alerte.")
                    except Exception as e:
                        print(f"Erreur BD : {e}")

                elif cmd == "cmd" and len(parts) >= 3:
                    target_node = parts[1]
                    command     = parts[2].upper()
                    node_tracker.send_command(target_node, command)
                    print(f"✅ Commande '{command}' programmée pour '{target_node}'")

                elif cmd == "quit":
                    self.stop()
                    break

                else:
                    print(f"Commande inconnue : '{user_input}'. Tapez 'help'.")

            except (EOFError, KeyboardInterrupt):
                break
            except Exception as e:
                logger.error(f"Erreur console : {e}")


# ─── Point d'entrée ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = SupervisionServer()
    server.start()
