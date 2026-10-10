"""
KRYPTONIX / noyau/extensions/stockage.py
=========================================
Base SQLite dédiée aux modules (coûts, agenda local, minuteurs, finances, vault...).
Fichier séparé de la mémoire principale : les finances restent dans leur propre fichier,
strictement local, jamais lu par les outils exposés à l'extérieur.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any


class Stockage:
    def __init__(self, chemin: str, schema: str = ""):
        self.chemin = chemin
        self._verrou = threading.RLock()
        self._cx = sqlite3.connect(chemin, check_same_thread=False)
        self._cx.row_factory = sqlite3.Row
        if schema:
            with self._verrou:
                self._cx.executescript(schema)
                self._cx.commit()

    def executer(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._verrou:
            c = self._cx.execute(sql, params)
            self._cx.commit()
            return c

    def lignes(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._verrou:
            return [dict(r) for r in self._cx.execute(sql, params).fetchall()]

    def une(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        r = self.lignes(sql, params)
        return r[0] if r else None

    def fermer(self) -> None:
        with self._verrou:
            try:
                self._cx.close()
            except sqlite3.Error:
                pass
