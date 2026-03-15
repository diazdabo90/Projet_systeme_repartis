-- ============================================================
--  SCRIPT DE CRÉATION DE LA BASE DE DONNÉES
--  Système Distribué de Supervision Réseau
--  UN-CHK P8 M1 SRIV 2026
-- ============================================================

-- Créer la base de données
CREATE DATABASE IF NOT EXISTS supervision_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE supervision_db;

-- ------------------------------------------------------------
-- TABLE : nodes
-- Stocke les informations de chaque nœud supervisé
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    node_id     VARCHAR(100) NOT NULL UNIQUE,   -- Ex: "server1", "vm-ubuntu-01"
    os          VARCHAR(100),                    -- Système d'exploitation
    cpu_type    VARCHAR(200),                    -- Type de processeur
    first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status      ENUM('ACTIVE', 'DOWN', 'UNKNOWN') DEFAULT 'UNKNOWN'
);

-- ------------------------------------------------------------
-- TABLE : metrics
-- Stocke les métriques collectées périodiquement par les agents
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    node_id         VARCHAR(100) NOT NULL,
    timestamp       DATETIME NOT NULL,
    cpu_load        FLOAT,          -- % utilisation CPU (0-100)
    memory_load     FLOAT,          -- % utilisation RAM (0-100)
    disk_load       FLOAT,          -- % utilisation disque (0-100)
    uptime          BIGINT,         -- Durée active en secondes

    -- Services réseau (OK / DOWN)
    svc_http        VARCHAR(10),    -- Service HTTP (port 80)
    svc_ssh         VARCHAR(10),    -- Service SSH (port 22)
    svc_dns         VARCHAR(10),    -- Service DNS (port 53)

    -- Applications grand public (OK / DOWN)
    app_chrome      VARCHAR(10),    -- Navigateur Chrome
    app_firefox     VARCHAR(10),    -- Navigateur Firefox
    app_vscode      VARCHAR(10),    -- VS Code

    -- Statut des ports (OPEN / CLOSED)
    port_80         VARCHAR(10),    -- HTTP
    port_443        VARCHAR(10),    -- HTTPS
    port_22         VARCHAR(10),    -- SSH
    port_3306       VARCHAR(10),    -- MySQL

    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    INDEX idx_node_ts (node_id, timestamp)  -- Index pour accélérer les requêtes
);

-- ------------------------------------------------------------
-- TABLE : alerts
-- Journalise les alertes (dépassement de seuil, pannes)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    node_id     VARCHAR(100) NOT NULL,
    alert_type  ENUM('CPU_HIGH', 'MEM_HIGH', 'DISK_HIGH', 'NODE_DOWN', 'NODE_UP') NOT NULL,
    message     TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    acknowledged BOOLEAN DEFAULT FALSE,

    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        ON DELETE CASCADE,
    INDEX idx_node_alerts (node_id, created_at)
);

-- ------------------------------------------------------------
-- TABLE : commands
-- Commandes envoyées par le serveur vers les clients
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS commands (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    node_id     VARCHAR(100) NOT NULL,
    command     VARCHAR(50) NOT NULL,   -- Ex: "UP_HTTP", "UP_SSH"
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    executed    BOOLEAN DEFAULT FALSE,

    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        ON DELETE CASCADE
);

-- ------------------------------------------------------------
-- Données de test initiales (optionnel)
-- ------------------------------------------------------------
-- INSERT INTO nodes (node_id, os, cpu_type) VALUES
--     ('test-node-1', 'Windows 11', 'Intel Core i7'),
--     ('test-node-2', 'Ubuntu 22.04', 'AMD Ryzen 5');

SHOW TABLES;
