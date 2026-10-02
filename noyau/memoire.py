"""
KRYPTONIX / noyau/memoire.py
=============================
Mémoire persistante SQLite : conversations, profil utilisateur, documents ingérés,
journal des événements. Thread-safe (un verrou unique protège la connexion partagée).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any

from .config import CHEMIN_DB

LOG = logging.getLogger("memoire")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage REAL    NOT NULL,
    role      TEXT     NOT NULL,          -- user | assistant | system
    contenu   TEXT     NOT NULL,
    source    TEXT     DEFAULT 'texte',   -- texte | voix | web | hud | agent
    humeur    TEXT     DEFAULT 'neutre'
);

CREATE TABLE IF NOT EXISTS profil (
    cle        TEXT PRIMARY KEY,
    valeur     TEXT NOT NULL,
    horodatage REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nom        TEXT NOT NULL,
    chemin     TEXT,
    type       TEXT,
    horodatage REAL NOT NULL,
    taille     INTEGER DEFAULT 0,
    resume     TEXT,
    contenu    TEXT
);

CREATE TABLE IF NOT EXISTS evenements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage REAL NOT NULL,
    categorie  TEXT NOT NULL,
    details    TEXT
);

CREATE TABLE IF NOT EXISTS fichiers_generes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nom        TEXT NOT NULL,
    chemin     TEXT NOT NULL,
    format     TEXT NOT NULL,
    sujet      TEXT,
    horodatage REAL NOT NULL,
    taille     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(horodatage);
CREATE INDEX IF NOT EXISTS idx_doc_nom ON documents(nom);
"""


class Memoire:
    """Couche d'accès unique à la base SQLite de KRYPTONIX."""

    def __init__(self, chemin: str = CHEMIN_DB):
        self.chemin = chemin
        self._verrou = threading.RLock()
        # check_same_thread=False : la connexion est partagée entre le HUD,
        # Flask et la boucle vocale. Le RLock garantit la sérialisation.
        self._cx = sqlite3.connect(chemin, check_same_thread=False)
        self._cx.row_factory = sqlite3.Row
        with self._verrou:
            self._cx.executescript(SCHEMA)
            self._cx.commit()
        LOG.info("Mémoire persistante prête (%s).", chemin)

    # ------------------------------------------------------------------
    #  Utilitaire interne
    # ------------------------------------------------------------------
    def _executer(self, requete: str, parametres: tuple = ()) -> sqlite3.Cursor:
        with self._verrou:
            curseur = self._cx.execute(requete, parametres)
            self._cx.commit()
            return curseur

    # ------------------------------------------------------------------
    #  Conversations
    # ------------------------------------------------------------------
    def ajouter_message(self, role: str, contenu: str, source: str = "texte",
                        humeur: str = "neutre") -> None:
        self._executer(
            "INSERT INTO conversations (horodatage, role, contenu, source, humeur)"
            " VALUES (?, ?, ?, ?, ?)",
            (time.time(), role, contenu, source, humeur),
        )

    def historique(self, limite: int = 12) -> list[dict[str, Any]]:
        """Retourne les N derniers messages, du plus ancien au plus récent."""
        with self._verrou:
            lignes = self._cx.execute(
                "SELECT role, contenu, horodatage, source FROM conversations"
                " ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(ligne) for ligne in reversed(lignes)]

    def rechercher_conversations(self, terme: str, limite: int = 10) -> list[dict[str, Any]]:
        with self._verrou:
            lignes = self._cx.execute(
                "SELECT role, contenu, horodatage FROM conversations"
                " WHERE contenu LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{terme}%", limite),
            ).fetchall()
        return [dict(ligne) for ligne in lignes]

    def compter_messages(self) -> int:
        with self._verrou:
            return self._cx.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]

    def purger_conversations(self) -> None:
        self._executer("DELETE FROM conversations")

    # ------------------------------------------------------------------
    #  Profil utilisateur (mémoire longue « factuelle »)
    # ------------------------------------------------------------------
    def definir_profil(self, cle: str, valeur: Any) -> None:
        if not isinstance(valeur, str):
            valeur = json.dumps(valeur, ensure_ascii=False)
        self._executer(
            "INSERT INTO profil (cle, valeur, horodatage) VALUES (?, ?, ?)"
            " ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur,"
            " horodatage=excluded.horodatage",
            (cle.strip().lower(), valeur, time.time()),
        )

    def obtenir_profil(self, cle: str, defaut: Any = None) -> Any:
        with self._verrou:
            ligne = self._cx.execute(
                "SELECT valeur FROM profil WHERE cle = ?", (cle.strip().lower(),)
            ).fetchone()
        return ligne["valeur"] if ligne else defaut

    def tout_le_profil(self) -> dict[str, str]:
        with self._verrou:
            lignes = self._cx.execute("SELECT cle, valeur FROM profil ORDER BY cle").fetchall()
        return {ligne["cle"]: ligne["valeur"] for ligne in lignes}

    def oublier_profil(self, cle: str) -> None:
        self._executer("DELETE FROM profil WHERE cle = ?", (cle.strip().lower(),))

    # ------------------------------------------------------------------
    #  Documents ingérés
    # ------------------------------------------------------------------
    def ajouter_document(self, nom: str, chemin: str, type_doc: str,
                         contenu: str, resume: str = "") -> int:
        curseur = self._executer(
            "INSERT INTO documents (nom, chemin, type, horodatage, taille, resume, contenu)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nom, chemin, type_doc, time.time(), len(contenu), resume, contenu),
        )
        return int(curseur.lastrowid)

    def maj_resume_document(self, doc_id: int, resume: str) -> None:
        self._executer("UPDATE documents SET resume = ? WHERE id = ?", (resume, doc_id))

    def documents(self, limite: int = 50) -> list[dict[str, Any]]:
        with self._verrou:
            lignes = self._cx.execute(
                "SELECT id, nom, type, horodatage, taille, resume FROM documents"
                " ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(ligne) for ligne in lignes]

    def document(self, doc_id: int) -> dict[str, Any] | None:
        with self._verrou:
            ligne = self._cx.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return dict(ligne) if ligne else None

    def rechercher_documents(self, terme: str, limite: int = 5) -> list[dict[str, Any]]:
        """Recherche plein-texte simple (LIKE) dans le contenu ingéré."""
        with self._verrou:
            lignes = self._cx.execute(
                "SELECT id, nom, resume, contenu FROM documents"
                " WHERE contenu LIKE ? OR nom LIKE ? OR resume LIKE ?"
                " ORDER BY id DESC LIMIT ?",
                (f"%{terme}%", f"%{terme}%", f"%{terme}%", limite),
            ).fetchall()
        return [dict(ligne) for ligne in lignes]

    def document_existe(self, chemin: str) -> bool:
        with self._verrou:
            return self._cx.execute(
                "SELECT 1 FROM documents WHERE chemin = ? LIMIT 1", (chemin,)
            ).fetchone() is not None

    # ------------------------------------------------------------------
    #  Journal d'événements (agents, erreurs, démarrages)
    # ------------------------------------------------------------------
    def journaliser(self, categorie: str, details: str = "") -> None:
        self._executer(
            "INSERT INTO evenements (horodatage, categorie, details) VALUES (?, ?, ?)",
            (time.time(), categorie, details[:2000]),
        )

    def evenements(self, limite: int = 30) -> list[dict[str, Any]]:
        with self._verrou:
            lignes = self._cx.execute(
                "SELECT horodatage, categorie, details FROM evenements"
                " ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(ligne) for ligne in lignes]

    # ------------------------------------------------------------------
    #  Fichiers générés (rédaction : docx, pdf, xlsx, csv, html, md, txt)
    # ------------------------------------------------------------------
    def ajouter_fichier_genere(self, nom: str, chemin: str, format_fichier: str,
                               sujet: str = "", taille: int = 0) -> int:
        curseur = self._executer(
            "INSERT INTO fichiers_generes (nom, chemin, format, sujet, horodatage, taille)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (nom, chemin, format_fichier, sujet, time.time(), taille),
        )
        return int(curseur.lastrowid)

    def fichiers_generes(self, limite: int = 30) -> list[dict[str, Any]]:
        with self._verrou:
            lignes = self._cx.execute(
                "SELECT id, nom, chemin, format, sujet, horodatage, taille"
                " FROM fichiers_generes ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(ligne) for ligne in lignes]

    def fichier_genere(self, fichier_id: int) -> dict[str, Any] | None:
        with self._verrou:
            ligne = self._cx.execute(
                "SELECT * FROM fichiers_generes WHERE id = ?", (fichier_id,)
            ).fetchone()
        return dict(ligne) if ligne else None

    # ------------------------------------------------------------------
    def statistiques(self) -> dict[str, int]:
        with self._verrou:
            return {
                "messages": self._cx.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
                "documents": self._cx.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "faits_profil": self._cx.execute("SELECT COUNT(*) FROM profil").fetchone()[0],
                "evenements": self._cx.execute("SELECT COUNT(*) FROM evenements").fetchone()[0],
                "fichiers_generes": self._cx.execute(
                    "SELECT COUNT(*) FROM fichiers_generes").fetchone()[0],
            }

    def fermer(self) -> None:
        with self._verrou:
            try:
                self._cx.close()
            except sqlite3.Error:
                pass
