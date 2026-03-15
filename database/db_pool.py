# ============================================================
#  database/db_pool.py
#  Pool de connexions à la base de données MySQL
#
#  CONCEPT : Pool de connexions
#  ─────────────────────────────────────────────────────────
#  Problème sans pool :
#    Chaque requête SQL ouvre une connexion → ferme une connexion
#    Avec 100 clients, ça fait 100 connexions/sec → très lent !
#
#  Solution avec pool :
#    On crée N connexions à l'avance (ex: 10)
#    Quand un thread a besoin d'une connexion, il en "emprunte" une
#    Après usage, il la "rend" dans le pool (pas de fermeture réelle)
#    → Beaucoup plus rapide, moins de ressources
#
#  On utilise : mysql-connector-python avec pooling intégré
# ============================================================

import mysql.connector
from mysql.connector import pooling
import logging

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Gestionnaire du pool de connexions MySQL.
    
    Usage :
        db = DatabasePool()
        db.initialize()
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ...")
        conn.close()   # Rend la connexion au pool (ne ferme pas vraiment)
    """

    def __init__(self, host="localhost", port=3306,
                 user="root", password="Voyage2000@",
                 database="supervision_db",
                 pool_size=10):
        """
        Paramètres du pool :
        - pool_size : nombre de connexions pré-créées (10 par défaut)
          * Trop petit → les threads attendent une connexion libre
          * Trop grand → gaspillage mémoire côté MySQL
          * Règle pratique : pool_size ≈ nombre de threads / 2
        """
        self.config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "pool_name": "supervision_pool",
            "pool_size": pool_size,
            "pool_reset_session": True,    # Réinitialise l'état de session à chaque emprunt
            "autocommit": True,            # Valide automatiquement les INSERT/UPDATE
            "connection_timeout": 10,
        }
        self._pool = None

    def initialize(self):
        """Crée le pool de connexions. À appeler une seule fois au démarrage."""
        try:
            self._pool = mysql.connector.pooling.MySQLConnectionPool(**self.config)
            logger.info(f"✅ Pool de connexions créé : {self.config['pool_size']} connexions")
        except mysql.connector.Error as e:
            logger.error(f"❌ Impossible de créer le pool : {e}")
            raise

    def get_connection(self):
        """
        Emprunte une connexion du pool.
        Bloque si toutes les connexions sont utilisées (jusqu'à timeout).
        """
        if self._pool is None:
            raise RuntimeError("Pool non initialisé. Appelez initialize() d'abord.")
        try:
            conn = self._pool.get_connection()
            return conn
        except mysql.connector.Error as e:
            logger.error(f"❌ Impossible d'obtenir une connexion : {e}")
            raise

    def execute_query(self, query: str, params: tuple = None, fetch: bool = False):
        """
        Méthode utilitaire : exécute une requête SQL de façon sûre.
        
        - query  : la requête SQL avec %s comme marqueurs
        - params : tuple de valeurs à insérer
        - fetch  : True si on veut récupérer des résultats (SELECT)
        
        Exemple :
            db.execute_query(
                "INSERT INTO metrics (node_id, cpu_load) VALUES (%s, %s)",
                params=("server1", 45.2)
            )
        """
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor(dictionary=True)   # Résultats sous forme de dict
            cursor.execute(query, params)

            if fetch:
                result = cursor.fetchall()
                return result
            else:
                return cursor.lastrowid   # Retourne l'ID du dernier INSERT

        except mysql.connector.Error as e:
            logger.error(f"❌ Erreur SQL : {e}\nRequête : {query}\nParams : {params}")
            raise
        finally:
            # IMPORTANT : toujours libérer la connexion, même en cas d'erreur
            if cursor:
                cursor.close()
            if conn:
                conn.close()   # Rend la connexion au pool

    def close_all(self):
        """Ferme toutes les connexions du pool (à appeler à l'arrêt du serveur)."""
        # mysql-connector gère la fermeture automatiquement via le garbage collector
        logger.info("Pool de connexions fermé.")


# ─── Instance globale (singleton) ────────────────────────────────────────────
# On crée une seule instance réutilisée partout dans le serveur
db_pool = DatabasePool()
