# 🖥️ Système Distribué de Supervision Réseau
**UN-CHK P8 M1 SRIV — Projet Systèmes Répartis 2026**

---

## 📁 Structure du projet

```
supervision_project/
├── client/
│   └── agent.py              ← Agent de supervision (CLIENT)
├── server/
│   ├── server.py             ← Serveur multi-clients (SERVEUR)
│   └── supervision.log       ← Fichier de logs (généré automatiquement)
├── database/
│   ├── schema.sql            ← Script de création de la BD
│   └── db_pool.py            ← Pool de connexions MySQL
├── shared/
│   └── protocol.py           ← Protocole de communication (partagé)
├── tests/
│   └── load_test.py          ← Tests de charge (10, 50, 100 clients)
└── README.md
```

---

## ⚙️ Prérequis & Installation

### 1. Python 3.11+
Télécharger sur https://www.python.org/downloads/
Vérifier : `python --version`

### 2. MySQL 8.0+
Télécharger MySQL Community Server : https://dev.mysql.com/downloads/mysql/
Ou utiliser XAMPP (inclut MySQL) : https://www.apachefriends.org/

### 3. Bibliothèques Python
Ouvrir un terminal (cmd ou PowerShell) et lancer :

```bash
pip install mysql-connector-python psutil
```

| Bibliothèque           | Rôle                                    |
|------------------------|-----------------------------------------|
| mysql-connector-python | Connexion à MySQL depuis Python         |
| psutil                 | Lecture des métriques système (CPU, RAM)|

---

## 🗄️ Création de la base de données

### Méthode 1 : MySQL Workbench
1. Ouvrir MySQL Workbench
2. Se connecter à votre serveur MySQL (root / votre_mot_de_passe)
3. File → Open SQL Script → sélectionner `database/schema.sql`
4. Cliquer sur l'éclair ⚡ (Execute)

### Méthode 2 : Ligne de commande
```bash
mysql -u root -p < database/schema.sql
```

### Vérification
```sql
USE supervision_db;
SHOW TABLES;
-- Doit afficher : nodes, metrics, alerts, commands
```

---

## 🔧 Configuration

### Modifier les paramètres de connexion MySQL
Ouvrir `database/db_pool.py`, ligne ~35 :
```python
def __init__(self, host="localhost", port=3306,
             user="root", password="VOTRE_MOT_DE_PASSE",  # ← Modifier ici
             database="supervision_db",
             pool_size=10):
```

### Modifier l'adresse du serveur (pour l'agent)
Ouvrir `client/agent.py`, ligne ~65 :
```python
SERVER_HOST = "127.0.0.1"   # ← Remplacer par l'IP du serveur si distant
SERVER_PORT = 9000
```

---

## 🚀 Lancement de l'application

### Étape 1 : Démarrer le serveur

```bash
python server/server.py
```
Vous devriez voir :
```
2026-02-15 10:00:00 [INFO] ✅ Pool de connexions créé : 10 connexions
2026-02-15 10:00:00 [INFO] 🚀 Serveur démarré sur 0.0.0.0:9000
2026-02-15 10:00:00 [INFO] 🧵 Pool de threads : 20 threads maximum
```

### Étape 2 : Démarrer un ou plusieurs agents
Ouvrir un **nouveau terminal** pour chaque agent :
```bash
python client/agent.py
```
Vous devriez voir :
```
10:00:05 [AGENT DESKTOP-ABC] 🚀 Agent démarré - Nœud : DESKTOP-ABC
10:00:05 [AGENT DESKTOP-ABC] ✅ Connecté au serveur !
10:00:06 [AGENT DESKTOP-ABC] 📊 Collecte des métriques...
10:00:07 [AGENT DESKTOP-ABC] 📤 Métriques envoyées au serveur
10:00:07 [AGENT DESKTOP-ABC] ✅ ACK reçu du serveur
```

### Étape 3 : Utiliser la console d'administration
Dans le terminal du serveur, vous pouvez taper des commandes :

```
> list
NODE                 STATUS     CPU%     RAM%     DISK%    DERNIÈRE VUE
──────────────────────────────────────────────────────────────────────
DESKTOP-ABC          🟢 ACTIVE   45.2     62.1     38.5     10:00:07

> status DESKTOP-ABC
📊 Nœud : DESKTOP-ABC
   Status    : ACTIVE
   OS        : Windows 11
   CPU       : 45.2%
   ...

> alerts
(Affiche les 20 dernières alertes)

> cmd DESKTOP-ABC UP_HTTP
✅ Commande 'UP_HTTP' programmée pour 'DESKTOP-ABC'
```

---

## 🧪 Tests de charge

```bash
# Test avec 10 clients
python tests/load_test.py --clients 10

# Test avec 50 clients
python tests/load_test.py --clients 50

# Test avec 100 clients
python tests/load_test.py --clients 100
```

Exemple de résultat attendu :
```
══════════════════════════════════════════════════
  TEST DE CHARGE : 100 clients simultanés
══════════════════════════════════════════════════
  📊 RÉSULTATS :
  Messages envoyés  : 500
  Réponses reçues   : 498
  Erreurs           : 2
  Temps total       : 8.34s
  Temps moy/message : 12.3ms
  Débit             : 59.9 msg/s
  Taux de succès    : 99.6%
══════════════════════════════════════════════════
```

---

## 📋 Protocole de Communication

Format JSON avec délimiteur `\n` :

**Agent → Serveur (METRICS) :**
```json
{
  "version": "1.0",
  "type": "METRICS",
  "node": "DESKTOP-ABC",
  "timestamp": "2026-02-15T10:30:00",
  "os": "Windows 11",
  "cpu_type": "Intel Core i7-12700",
  "cpu": 45.2,
  "memory": 62.1,
  "disk": 38.5,
  "uptime": 86400,
  "services": {
    "http": "OK", "ssh": "DOWN", "dns": "OK",
    "chrome": "OK", "firefox": "OK", "vscode": "OK"
  },
  "ports": {
    "80": "OPEN", "443": "OPEN", "22": "CLOSED", "3306": "OPEN"
  }
}
```

**Serveur → Agent (ACK) :**
```json
{"version":"1.0","type":"ACK","node":"DESKTOP-ABC","status":"OK","timestamp":"2026-02-15T10:30:01"}
```

**Serveur → Agent (COMMAND) :**
```json
{"version":"1.0","type":"COMMAND","node":"DESKTOP-ABC","command":"UP_HTTP","timestamp":"2026-02-15T10:30:01"}
```

---

## 🐛 Résolution de problèmes courants

| Problème | Solution |
|----------|----------|
| `ConnectionRefusedError` sur l'agent | Vérifier que le serveur est bien démarré |
| `mysql.connector.errors.InterfaceError` | Vérifier le mot de passe MySQL dans db_pool.py |
| `ModuleNotFoundError: psutil` | Lancer `pip install psutil` |
| `ModuleNotFoundError: mysql.connector` | Lancer `pip install mysql-connector-python` |
| Port 9000 déjà utilisé | Changer `SERVER_PORT` dans server.py ET agent.py |

---

## 👥 Membres du groupe
- Ibrahima DABO
- Moussa DIEME
- Aicha haby SARR


