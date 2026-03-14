# ============================================================
#  tests/load_test.py
#  Test de Charge : Simulation de N clients simultanés
#
#  Usage :
#    python tests/load_test.py --clients 10
#    python tests/load_test.py --clients 50
#    python tests/load_test.py --clients 100
# ============================================================

import socket
import threading
import time
import json
import random
import argparse
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.protocol import build_metrics_message, parse_message, MESSAGE_DELIMITER

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9000

# Statistiques partagées (protégées par lock)
stats = {
    "sent":       0,
    "received":   0,
    "errors":     0,
    "total_time": 0.0,
}
stats_lock = threading.Lock()


def simulate_client(client_id: int, num_messages: int = 5):
    """Simule un agent qui envoie num_messages métriques."""
    node_id = f"load-test-node-{client_id:03d}"
    buffer = ""

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(15)
        sock.connect((SERVER_HOST, SERVER_PORT))

        for i in range(num_messages):
            # Générer des métriques aléatoires
            services = {s: random.choice(["OK", "DOWN"]) for s in
                        ["http", "ssh", "dns", "chrome", "firefox", "vscode"]}
            ports    = {str(p): random.choice(["OPEN", "CLOSED"]) for p in [80, 443, 22, 3306]}

            msg = build_metrics_message(
                node_id=node_id,
                os_name="Windows Test",
                cpu_type="Test CPU",
                cpu_load=round(random.uniform(5, 98), 2),
                memory_load=round(random.uniform(10, 90), 2),
                disk_load=round(random.uniform(20, 80), 2),
                uptime=random.randint(100, 100000),
                services=services,
                ports=ports,
            )

            t0 = time.time()
            sock.sendall(msg.encode("utf-8"))

            # Attendre ACK
            while MESSAGE_DELIMITER not in buffer:
                chunk = sock.recv(4096).decode("utf-8")
                if not chunk:
                    break
                buffer += chunk

            if MESSAGE_DELIMITER in buffer:
                line, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                elapsed = time.time() - t0

                with stats_lock:
                    stats["sent"]      += 1
                    stats["received"]  += 1
                    stats["total_time"] += elapsed

            time.sleep(0.5)   # Petite pause entre envois

        sock.close()

    except Exception as e:
        with stats_lock:
            stats["errors"] += 1


def run_load_test(num_clients: int):
    """Lance num_clients threads simultanément."""
    print(f"\n{'═'*50}")
    print(f"  TEST DE CHARGE : {num_clients} clients simultanés")
    print(f"{'═'*50}")
    print(f"  Serveur : {SERVER_HOST}:{SERVER_PORT}")
    print(f"  Messages par client : 5")
    print(f"  Total messages attendus : {num_clients * 5}")
    print(f"{'─'*50}")

    threads = []
    t_start = time.time()

    # Créer et démarrer tous les threads
    for i in range(num_clients):
        t = threading.Thread(target=simulate_client, args=(i,))
        threads.append(t)

    # Lancer tous les threads quasi-simultanément
    for t in threads:
        t.start()

    # Attendre la fin de tous les threads
    for t in threads:
        t.join(timeout=60)

    t_total = time.time() - t_start

    # Afficher les résultats
    avg_time = stats["total_time"] / max(stats["received"], 1) * 1000

    print(f"\n  📊 RÉSULTATS :")
    print(f"  ─────────────────────────────────")
    print(f"  Messages envoyés  : {stats['sent']}")
    print(f"  Réponses reçues   : {stats['received']}")
    print(f"  Erreurs           : {stats['errors']}")
    print(f"  Temps total       : {t_total:.2f}s")
    print(f"  Temps moy/message : {avg_time:.1f}ms")
    print(f"  Débit             : {stats['sent']/t_total:.1f} msg/s")
    taux = stats["received"] / max(stats["sent"], 1) * 100
    print(f"  Taux de succès    : {taux:.1f}%")
    print(f"{'═'*50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test de charge du serveur de supervision")
    parser.add_argument("--clients", type=int, default=10,
                        help="Nombre de clients simultanés (10, 50, 100)")
    args = parser.parse_args()
    run_load_test(args.clients)
