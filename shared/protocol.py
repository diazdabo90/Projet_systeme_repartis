# ============================================================
#  shared/protocol.py
#  Définition du Protocole de Communication Client ↔ Serveur
#
#  CONCEPT : Protocole Applicatif
#  ─────────────────────────────────────────────────────────
#  Un protocole applicatif définit :
#    1. Le FORMAT des messages (ici : JSON)
#    2. Les TYPES de messages (METRICS, ALERT, COMMAND, ACK...)
#    3. La FRÉQUENCE d'envoi (ici : toutes les 10 secondes)
#    4. La GESTION DES ERREURS (champs manquants, valeurs invalides)
#
#  Format JSON choisi (vs texte brut) car :
#    ✅ Lisible par humain
#    ✅ Facile à parser en Python (json.loads)
#    ✅ Extensible (ajouter des champs sans casser les anciens clients)
#    ✅ Standard industrie (Prometheus, Zabbix utilisent JSON/YAML)
# ============================================================

import json
import time
from datetime import datetime
from typing import Optional


# ─── Constantes du protocole ─────────────────────────────────────────────────

PROTOCOL_VERSION  = "1.0"
SEND_INTERVAL_SEC = 10          # L'agent envoie ses métriques toutes les 10 secondes
TIMEOUT_NODE_DOWN = 90          # Après 90s sans nouvelles → nœud considéré en panne
ALERT_THRESHOLD   = 90.0        # Seuil d'alerte CPU/RAM/Disque (%)
MESSAGE_DELIMITER = "\n"        # Séparateur de messages sur le socket TCP

# Types de messages possibles
MSG_METRICS  = "METRICS"        # Agent → Serveur : données de métriques
MSG_ALERT    = "ALERT"          # Agent → Serveur : dépassement de seuil
MSG_ACK      = "ACK"            # Serveur → Agent : accusé de réception
MSG_COMMAND  = "COMMAND"        # Serveur → Agent : commande (ex: UP_HTTP)
MSG_ERROR    = "ERROR"          # Serveur → Agent : erreur de format

# Services surveillés
NETWORK_SERVICES = ["http", "ssh", "dns"]
USER_APPS        = ["chrome", "firefox", "vscode"]
MONITORED_PORTS  = [80, 443, 22, 3306]


# ─── Fonctions de création de messages ───────────────────────────────────────

def build_metrics_message(node_id: str, os_name: str, cpu_type: str,
                           cpu_load: float, memory_load: float, disk_load: float,
                           uptime: int, services: dict, ports: dict) -> str:
    """
    Construit un message METRICS prêt à envoyer sur le socket.

    Exemple de message produit :
    {
        "version": "1.0",
        "type": "METRICS",
        "node": "server1",
        "timestamp": "2026-02-15T10:30:00",
        "os": "Windows 11",
        "cpu_type": "Intel Core i7",
        "cpu": 45.2,
        "memory": 62.1,
        "disk": 38.5,
        "uptime": 86400,
        "services": {
            "http": "OK", "ssh": "OK", "dns": "DOWN",
            "chrome": "OK", "firefox": "DOWN", "vscode": "OK"
        },
        "ports": {
            "80": "OPEN", "443": "OPEN", "22": "OPEN", "3306": "CLOSED"
        }
    }
    """
    message = {
        "version":   PROTOCOL_VERSION,
        "type":      MSG_METRICS,
        "node":      node_id,
        "timestamp": datetime.now().isoformat(timespec='seconds'),
        "os":        os_name,
        "cpu_type":  cpu_type,
        "cpu":       round(cpu_load, 2),
        "memory":    round(memory_load, 2),
        "disk":      round(disk_load, 2),
        "uptime":    uptime,
        "services":  services,   # {"http": "OK", "ssh": "DOWN", ...}
        "ports":     ports,      # {"80": "OPEN", "443": "CLOSED", ...}
    }
    # json.dumps → convertit le dict Python en chaîne JSON
    # + MESSAGE_DELIMITER → marque la fin du message sur le flux TCP
    return json.dumps(message) + MESSAGE_DELIMITER


def build_ack_message(node_id: str, status: str = "OK") -> str:
    """Construit un accusé de réception du serveur vers le client."""
    ack = {
        "version":   PROTOCOL_VERSION,
        "type":      MSG_ACK,
        "node":      node_id,
        "status":    status,
        "timestamp": datetime.now().isoformat(timespec='seconds'),
    }
    return json.dumps(ack) + MESSAGE_DELIMITER


def build_command_message(node_id: str, command: str) -> str:
    """
    Construit une commande du serveur vers l'agent.
    command : ex "UP_HTTP", "UP_SSH", "UP_DNS"
    """
    cmd = {
        "version":   PROTOCOL_VERSION,
        "type":      MSG_COMMAND,
        "node":      node_id,
        "command":   command,
        "timestamp": datetime.now().isoformat(timespec='seconds'),
    }
    return json.dumps(cmd) + MESSAGE_DELIMITER


def build_error_message(reason: str) -> str:
    """Construit un message d'erreur."""
    err = {
        "version": PROTOCOL_VERSION,
        "type":    MSG_ERROR,
        "reason":  reason,
    }
    return json.dumps(err) + MESSAGE_DELIMITER


# ─── Fonctions de validation/parsing ─────────────────────────────────────────

def parse_message(raw: str) -> Optional[dict]:
    """
    Parse une chaîne JSON reçue sur le socket.
    Retourne le dict Python, ou None si le format est invalide.
    """
    try:
        data = json.loads(raw.strip())
        return data
    except json.JSONDecodeError:
        return None


def validate_metrics(data: dict) -> tuple[bool, str]:
    """
    Valide qu'un message METRICS contient tous les champs obligatoires
    et que les valeurs sont dans les plages acceptables.

    Retourne (True, "") si valide, (False, "raison") sinon.
    """
    required_fields = ["version", "type", "node", "timestamp",
                        "cpu", "memory", "disk", "uptime", "services", "ports"]

    # Vérification des champs obligatoires
    for field in required_fields:
        if field not in data:
            return False, f"Champ manquant : '{field}'"

    # Vérification du type de message
    if data["type"] != MSG_METRICS:
        return False, f"Type inattendu : '{data['type']}'"

    # Vérification des plages de valeurs
    for metric in ["cpu", "memory", "disk"]:
        val = data.get(metric)
        if not isinstance(val, (int, float)) or not (0 <= val <= 100):
            return False, f"Valeur invalide pour '{metric}' : {val} (doit être 0-100)"

    if not isinstance(data.get("uptime"), int) or data["uptime"] < 0:
        return False, f"Uptime invalide : {data.get('uptime')}"

    return True, ""


def check_alerts(data: dict) -> list[str]:
    """
    Vérifie si des métriques dépassent le seuil d'alerte.
    Retourne la liste des alertes détectées.
    """
    alerts = []
    for metric_key, label in [("cpu", "CPU"), ("memory", "Mémoire"), ("disk", "Disque")]:
        val = data.get(metric_key, 0)
        if val > ALERT_THRESHOLD:
            alerts.append(f"⚠️  ALERTE {label} sur {data['node']} : {val}% > {ALERT_THRESHOLD}%")
    return alerts
