# ============================================================
#  client/agent.py
#  Agent de Supervision (CLIENT)
#
#  CONCEPT : Client TCP
#  ─────────────────────────────────────────────────────────
#  Le client se connecte au serveur via un socket TCP.
#  Un socket = point de communication réseau (IP + Port).
#
#  Flux de communication :
#    1. Agent crée un socket TCP
#    2. Se connecte au serveur (IP:PORT)
#    3. Toutes les 10 secondes :
#       a. Collecte les métriques (CPU, RAM, disque...)
#       b. Construit un message JSON
#       c. L'envoie sur le socket
#       d. Attend l'ACK du serveur
#    4. Si connexion perdue → tente de se reconnecter
#
#  BIBLIOTHÈQUES UTILISÉES :
#    - socket   : sockets TCP (bibliothèque standard Python)
#    - psutil   : collecte des métriques système (pip install psutil)
#    - platform : infos OS (bibliothèque standard Python)
# ============================================================

import socket
import time
import platform
import logging
import sys
import os
import random

# psutil : bibliothèque pour lire les ressources système
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("⚠️  psutil non installé. Les métriques seront simulées.")
    print("   Installez-le avec : pip install psutil")

# Ajoute le dossier parent au path pour importer shared/protocol.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.protocol import (
    build_metrics_message, parse_message,
    MSG_COMMAND, MSG_ACK, MSG_ERROR,
    SEND_INTERVAL_SEC, MESSAGE_DELIMITER,
    NETWORK_SERVICES, USER_APPS, MONITORED_PORTS
)

# ─── Configuration ────────────────────────────────────────────────────────────
SERVER_HOST = "127.0.0.1"    # Adresse IP du serveur (localhost pour les tests)
SERVER_PORT = 9000            # Port d'écoute du serveur
NODE_ID     = platform.node() or "agent-default"   # Nom de la machine
RECONNECT_DELAY = 5           # Attente en secondes avant tentative de reconnexion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AGENT %(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(NODE_ID)


# ─── Collecte des métriques ───────────────────────────────────────────────────

def get_cpu_load() -> float:
    """Retourne le % d'utilisation CPU (moyenne sur 1 seconde)."""
    if PSUTIL_AVAILABLE:
        return psutil.cpu_percent(interval=1)
    else:
        return round(random.uniform(10, 95), 2)   # Simulation


def get_memory_load() -> float:
    """Retourne le % d'utilisation de la RAM."""
    if PSUTIL_AVAILABLE:
        return psutil.virtual_memory().percent
    else:
        return round(random.uniform(20, 85), 2)


def get_disk_load() -> float:
    """Retourne le % d'utilisation du disque principal."""
    if PSUTIL_AVAILABLE:
        # Sur Windows, le disque principal est C:\
        disk_path = "C:\\" if platform.system() == "Windows" else "/"
        return psutil.disk_usage(disk_path).percent
    else:
        return round(random.uniform(30, 75), 2)


def get_uptime() -> int:
    """Retourne la durée d'activité du système en secondes."""
    if PSUTIL_AVAILABLE:
        # psutil.boot_time() retourne le timestamp UNIX du démarrage
        return int(time.time() - psutil.boot_time())
    else:
        return random.randint(3600, 86400)


def get_os_info() -> tuple[str, str]:
    """Retourne (nom OS, type CPU)."""
    os_name  = f"{platform.system()} {platform.release()}"
    cpu_type = platform.processor() or "Processeur inconnu"
    return os_name, cpu_type


def check_port(host: str, port: int, timeout: float = 0.5) -> str:
    """
    Vérifie si un port TCP est ouvert.
    Retourne "OPEN" ou "CLOSED".
    
    CONCEPT : On essaie de se connecter au port.
    Si la connexion réussit → port OPEN
    Si connexion refusée ou timeout → port CLOSED
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "OPEN"
    except (ConnectionRefusedError, OSError, socket.timeout):
        return "CLOSED"


def check_service_by_process(process_names: list) -> str:
    """
    Vérifie si un service est actif en cherchant son processus.
    Retourne "OK" si le processus tourne, "DOWN" sinon.
    """
    if not PSUTIL_AVAILABLE:
        return random.choice(["OK", "OK", "OK", "DOWN"])   # Simulation (biais vers OK)

    running = {p.name().lower() for p in psutil.process_iter(['name'])}
    for name in process_names:
        if name.lower() in running:
            return "OK"
    return "DOWN"


def collect_services() -> dict:
    """
    Collecte le statut des 6 services/applications.
    
    Services réseau : HTTP, SSH, DNS (vérification par port)
    Applications    : Chrome, Firefox, VS Code (vérification par processus)
    """
    services = {}

    # Services réseau : on vérifie les ports locaux
    service_ports = {
        "http":  80,
        "ssh":   22,
        "dns":   53,
    }
    for svc, port in service_ports.items():
        services[svc] = check_port("127.0.0.1", port)

    # Applications : on cherche leurs processus
    app_processes = {
        "chrome":  ["chrome.exe", "google-chrome", "chromium"],
        "firefox": ["firefox.exe", "firefox"],
        "vscode":  ["code.exe", "code"],
    }
    for app, proc_names in app_processes.items():
        services[app] = check_service_by_process(proc_names)

    return services


def collect_ports() -> dict:
    """Vérifie le statut des 4 ports prédéfinis."""
    ports = {}
    for port in MONITORED_PORTS:
        ports[str(port)] = check_port("127.0.0.1", port)
    return ports


# ─── Gestion du socket TCP ────────────────────────────────────────────────────

class SupervisionAgent:
    """
    Agent de supervision : collecte et envoie les métriques au serveur.
    
    CONCEPT : socket TCP en mode connecté
    ─────────────────────────────────────
    TCP = Transmission Control Protocol
    - Connexion persistante (contrairement à UDP)
    - Garantit la livraison des messages dans l'ordre
    - Détecte les pertes de connexion
    """

    def __init__(self):
        self.sock = None
        self.buffer = ""        # Buffer pour les données reçues partiellement
        self.running = False

    def connect(self) -> bool:
        """
        Crée un socket TCP et se connecte au serveur.
        Retourne True si succès, False sinon.
        """
        try:
            # socket.AF_INET  = IPv4
            # socket.SOCK_STREAM = TCP (flux continu, fiable)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            # Timeout de 10s sur les opérations de lecture/écriture
            self.sock.settimeout(10)

            logger.info(f"Connexion au serveur {SERVER_HOST}:{SERVER_PORT}...")
            self.sock.connect((SERVER_HOST, SERVER_PORT))
            logger.info("✅ Connecté au serveur !")
            return True

        except (ConnectionRefusedError, OSError) as e:
            logger.error(f"❌ Connexion échouée : {e}")
            self.sock = None
            return False

    def send_message(self, message: str) -> bool:
        """
        Envoie un message sur le socket.
        
        CONCEPT : encode() convertit str Python → bytes
        Le réseau transmet des octets (bytes), pas du texte.
        utf-8 est l'encodage standard.
        """
        try:
            self.sock.sendall(message.encode("utf-8"))
            return True
        except (BrokenPipeError, OSError) as e:
            logger.error(f"❌ Envoi échoué : {e}")
            return False

    def receive_message(self) -> str | None:
        """
        Reçoit un message du serveur.
        
        CONCEPT : Lecture en flux (stream)
        TCP est un flux continu. recv(4096) peut recevoir :
        - Un message complet
        - Un fragment de message
        - Plusieurs messages en une fois
        
        On utilise un buffer + délimiteur '\n' pour séparer les messages.
        """
        try:
            while MESSAGE_DELIMITER not in self.buffer:
                chunk = self.sock.recv(4096).decode("utf-8")
                if not chunk:
                    return None     # Connexion fermée proprement
                self.buffer += chunk

            # Extraire le premier message complet
            line, self.buffer = self.buffer.split(MESSAGE_DELIMITER, 1)
            return line

        except socket.timeout:
            return None     # Pas de réponse dans le délai imparti
        except OSError:
            return None

    def handle_command(self, data: dict):
        """
        Traite une commande reçue du serveur.
        Ex: {"type": "COMMAND", "command": "UP_HTTP"}
        """
        cmd = data.get("command", "")
        logger.info(f"📩 Commande reçue du serveur : {cmd}")

        # Analyse de la commande
        if cmd.startswith("UP_"):
            service = cmd[3:].lower()   # "UP_HTTP" → "http"
            logger.info(f"  → Activation du service : {service}")
            # Ici on pourrait vraiment démarrer le service (subprocess.run...)
            # Pour simplifier, on logue juste l'action
        else:
            logger.warning(f"  → Commande inconnue : {cmd}")

    def run(self):
        """
        Boucle principale de l'agent.
        Se connecte, collecte et envoie les métriques en continu.
        """
        self.running = True
        logger.info(f"🚀 Agent démarré - Nœud : {NODE_ID}")
        os_name, cpu_type = get_os_info()
        logger.info(f"   OS : {os_name} | CPU : {cpu_type}")

        while self.running:
            # ── Connexion / reconnexion ───────────────────────────────────
            if self.sock is None:
                if not self.connect():
                    logger.info(f"⏳ Nouvelle tentative dans {RECONNECT_DELAY}s...")
                    time.sleep(RECONNECT_DELAY)
                    continue

            # ── Collecte des métriques ───────────────────────────────────
            logger.info("📊 Collecte des métriques...")
            cpu     = get_cpu_load()
            memory  = get_memory_load()
            disk    = get_disk_load()
            uptime  = get_uptime()
            services = collect_services()
            ports    = collect_ports()

            logger.info(f"   CPU: {cpu}%  RAM: {memory}%  Disque: {disk}%  Uptime: {uptime}s")

            # ── Construction et envoi du message ──────────────────────────
            message = build_metrics_message(
                node_id=NODE_ID, os_name=os_name, cpu_type=cpu_type,
                cpu_load=cpu, memory_load=memory, disk_load=disk,
                uptime=uptime, services=services, ports=ports
            )

            if not self.send_message(message):
                logger.error("Connexion perdue. Reconnexion...")
                self.sock.close()
                self.sock = None
                continue

            logger.info("📤 Métriques envoyées au serveur")

            # ── Attente de l'ACK ──────────────────────────────────────────
            response_raw = self.receive_message()
            if response_raw:
                response = parse_message(response_raw)
                if response:
                    if response.get("type") == MSG_ACK:
                        logger.info(f"✅ ACK reçu du serveur")
                    elif response.get("type") == MSG_COMMAND:
                        self.handle_command(response)
                    elif response.get("type") == MSG_ERROR:
                        logger.warning(f"⚠️  Erreur du serveur : {response.get('reason')}")

            # ── Attente avant prochain envoi ──────────────────────────────
            logger.info(f"⏱️  Prochain envoi dans {SEND_INTERVAL_SEC}s...\n")
            time.sleep(SEND_INTERVAL_SEC)

    def stop(self):
        """Arrête proprement l'agent."""
        self.running = False
        if self.sock:
            self.sock.close()
        logger.info("Agent arrêté.")


# ─── Point d'entrée ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    agent = SupervisionAgent()
    try:
        agent.run()
    except KeyboardInterrupt:
        print("\n⛔ Arrêt demandé par l'utilisateur.")
        agent.stop()
