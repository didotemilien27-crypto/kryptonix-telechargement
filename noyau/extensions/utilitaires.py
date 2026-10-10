"""
KRYPTONIX / noyau/extensions/utilitaires.py
============================================
Utilitaires du quotidien : date et heure, minuteurs (plusieurs en parallèle, nommés),
chronomètre, rappels. La météo existe déjà dans le noyau (noyau/meteo.py).

Les minuteurs sont enregistrés : ils survivent à un redémarrage de l'application.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import threading
import time

from ..config import DOSSIER_DONNEES
from .base import Module, Outil, normaliser
from .stockage import Stockage

LOG = logging.getLogger("utilitaires")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]
NOMBRES = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
           "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "quinze": 15, "vingt": 20, "trente": 30,
           "quarante": 40, "cinquante": 50, "soixante": 60, "demi": 0.5, "quart": 0.25}

SCHEMA = """
CREATE TABLE IF NOT EXISTS minuteurs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT, fin REAL NOT NULL, duree REAL NOT NULL,
    genre TEXT DEFAULT 'minuteur', fait INTEGER DEFAULT 0
);
"""


def lire_duree(c: str) -> float | None:
    """« 10 minutes », « 1 heure 30 », « une demi-heure », « 45 secondes » → secondes."""
    c = normaliser(c)
    total, trouve = 0.0, False
    if re.search(r"\bdemi[- ]heure\b", c):
        total, trouve = total + 1800, True
        c = re.sub(r"\bdemi[- ]heure\b", " ", c)
    if re.search(r"\bquart d'heure\b", c):
        total, trouve = total + 900, True
        c = re.sub(r"\bquart d'heure\b", " ", c)
    for m in re.finditer(r"(\d+(?:[.,]\d+)?|\b(?:" + "|".join(NOMBRES) + r")\b)\s*"
                         r"(heures?|h|minutes?|mins?|secondes?|secs?|s)\b", c):
        brut = m.group(1)
        n = float(brut.replace(",", ".")) if re.match(r"\d", brut) else NOMBRES[brut]
        unite = m.group(2)
        facteur = 3600 if unite.startswith(("h")) else 60 if unite.startswith("m") else 1
        total += n * facteur
        trouve = True
    m = re.search(r"\b(\d+)\s*h\s*(\d{1,2})\b", c)
    if m and not trouve:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    return total if trouve and total > 0 else None


def dire_duree(sec: float) -> str:
    sec = int(round(sec))
    h, reste = divmod(sec, 3600)
    m, s = divmod(reste, 60)
    parts = []
    if h:
        parts.append(f"{h} heure{'s' if h > 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m > 1 else ''}")
    if s and not h:
        parts.append(f"{s} seconde{'s' if s > 1 else ''}")
    return " et ".join(parts) if parts else "0 seconde"


class Utilitaires(Module):
    nom = "utilitaires"
    description = "Date, heure, minuteurs, chronomètre, rappels"
    exemples = ("quelle heure est-il", "minuteur de 10 minutes pour les pâtes", "dans combien de temps sonne le minuteur",
                "rappelle-moi dans 30 minutes d'appeler Paul", "lance un chronomètre")

    def __init__(self, kx):
        super().__init__(kx)
        self.db = Stockage(os.path.join(DOSSIER_DONNEES, "extensions.db"), SCHEMA)
        self._timers: dict[int, threading.Timer] = {}
        self._verrou = threading.Lock()
        self._chrono: float | None = None

    # -- minuteurs --------------------------------------------------------
    def demarrer(self) -> None:
        """Réarme les minuteurs enregistrés avant l'arrêt de l'application."""
        for t in self.db.lignes("SELECT * FROM minuteurs WHERE fait=0"):
            reste = t["fin"] - time.time()
            if reste <= -3600:                       # trop ancien : on l'oublie sans bruit
                self.db.executer("UPDATE minuteurs SET fait=1 WHERE id=?", (t["id"],))
            else:                                    # échu pendant l'arrêt : on le signale juste après le démarrage
                self._armer(t["id"], t["nom"], max(reste, 1.0), t["genre"])

    def arreter(self) -> None:
        with self._verrou:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()

    def _armer(self, tid: int, nom: str, reste: float, genre: str) -> None:
        timer = threading.Timer(reste, self._sonner, args=(tid, nom, genre))
        timer.daemon = True
        with self._verrou:
            self._timers[tid] = timer
        timer.start()

    def _sonner(self, tid: int, nom: str, genre: str) -> None:
        with self._verrou:
            self._timers.pop(tid, None)
        self.db.executer("UPDATE minuteurs SET fait=1 WHERE id=?", (tid,))
        if genre == "rappel":
            self.kx.extensions.sons.jouer("rappel")
            msg = f"Rappel : {nom}." if nom else "C'est l'heure de ton rappel."
        else:
            self.kx.extensions.sons.jouer("minuteur")
            msg = f"Le minuteur « {nom} » est terminé." if nom else "Le minuteur est terminé."
        self.kx._diffuser("alerte", {"message": msg})
        self.kx._diffuser("message", {"role": "assistant", "contenu": msg, "source": "minuteur"})
        self.kx.voix.parler(msg)

    def creer(self, secondes: float, nom: str = "", genre: str = "minuteur") -> int:
        c = self.db.executer("INSERT INTO minuteurs (nom, fin, duree, genre) VALUES (?,?,?,?)",
                             (nom, time.time() + secondes, secondes, genre))
        tid = c.lastrowid
        self._armer(tid, nom, secondes, genre)
        return tid

    def actifs(self) -> list[dict]:
        return [dict(t, reste=max(0, t["fin"] - time.time()))
                for t in self.db.lignes("SELECT * FROM minuteurs WHERE fait=0 ORDER BY fin")]

    def annuler(self, nom: str | None = None) -> int:
        n = 0
        for t in self.actifs():
            if nom is None or normaliser(nom) in normaliser(t["nom"] or ""):
                with self._verrou:
                    timer = self._timers.pop(t["id"], None)
                if timer:
                    timer.cancel()
                self.db.executer("UPDATE minuteurs SET fait=1 WHERE id=?", (t["id"],))
                n += 1
        return n

    # -- date / heure -----------------------------------------------------
    @staticmethod
    def heure_parlee(maintenant: datetime.datetime | None = None) -> str:
        n = maintenant or datetime.datetime.now()
        if n.minute == 0:
            return f"Il est {n.hour} heures pile." if n.hour != 1 else "Il est 1 heure pile."
        return f"Il est {n.hour} heures {n.minute:02d}."

    @staticmethod
    def date_parlee(maintenant: datetime.datetime | None = None) -> str:
        n = maintenant or datetime.datetime.now()
        jour = "1er" if n.day == 1 else str(n.day)
        return f"Nous sommes {JOURS[n.weekday()]} {jour} {MOIS[n.month - 1]} {n.year}."

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if re.search(r"\bquelle heure\b|\bl'heure (?:qu'il est|actuelle)\b|\bil est quelle heure\b", c):
            return self.heure_parlee()
        if re.search(r"\b(quel jour (?:sommes|est)|quelle date|on est quel jour|la date d'aujourd'hui|quel jour on est)\b", c):
            return self.date_parlee()

        # minuteur / rappel
        if re.search(r"\b(annule|arrete|supprime|stoppe) (?:le |les |tous les |mes )?(?:minuteurs?|rappels?|chrono)", c):
            m = re.search(r"minuteurs? (?:de |pour |des |du |d')?(.+)$", c)
            nom = m.group(1).strip() if m and not re.search(r"\d", m.group(1)) else None
            n = self.annuler(nom)
            self.kx.extensions.sons.jouer("annule")
            return f"{n} minuteur{'s' if n > 1 else ''} annulé{'s' if n > 1 else ''}." if n else "Aucun minuteur en cours."
        if re.search(r"\b(combien de temps reste|il reste combien|quels? minuteurs?|mes minuteurs|mes rappels|dans combien de temps)\b", c):
            actifs = self.actifs()
            if not actifs:
                return "Aucun minuteur en cours."
            return " ; ".join(f"{(t['nom'] or t['genre'])} : {dire_duree(t['reste'])}" for t in actifs) + "."
        m = re.search(r"\b(?:rappelle[- ]moi|rappel)\b(.*)", c)
        if m:
            duree = lire_duree(m.group(1))
            if duree:
                objet = re.sub(r"\b(?:dans|de|d')\s*[\w .,]*?(?:heures?|minutes?|secondes?|mins?|h)\b", " ", m.group(1), count=1)
                objet = re.sub(r"^\s*(?:que |de |d')\s*", "", re.sub(r"\s+", " ", objet).strip())
                self.creer(duree, objet, "rappel")
                self.kx.extensions.sons.jouer("confirmation_ok")
                return f"C'est noté, je te rappelle dans {dire_duree(duree)}" + (f" : {objet}." if objet else ".")
        if re.search(r"\b(minuteur|minuterie|timer|compte a rebours|previens[- ]moi dans|reveille[- ]moi dans)\b", c):
            duree = lire_duree(c)
            if not duree:
                return "Pour combien de temps le minuteur ?"
            reste = re.sub(r"\d+\s*(?:heures?|minutes?|secondes?|mins?|h)\b", " ", commande.lower().replace("’", "'"))
            m = re.search(r"\bpour (?:les |le |la |l'|mes |mon |ma )?([a-zà-ÿ' ]+?)\s*$", reste)
            nom = m.group(1).strip() if m else ""
            nom = re.sub(r"\b(minuteur|un|une)\b", "", nom).strip()
            self.creer(duree, nom, "minuteur")
            self.kx.extensions.sons.jouer("confirmation_ok")
            return f"Minuteur lancé pour {dire_duree(duree)}" + (f" : {nom}." if nom else ".")

        # chronomètre
        if re.search(r"\b(lance|demarre|start) (?:un |le )?chrono", c):
            self._chrono = time.time()
            self.kx.extensions.sons.jouer("clic")
            return "Chronomètre lancé."
        if re.search(r"\b(arrete|stoppe|stop|combien sur) (?:le )?chrono|\btemps du chrono\b", c) and self._chrono:
            ecoule = time.time() - self._chrono
            if re.search(r"\b(arrete|stoppe|stop)\b", c):
                self._chrono = None
            return f"Chronomètre : {dire_duree(ecoule)}."
        return None

    def statut(self) -> dict:
        return {"minuteurs": len(self.actifs())}

    def outils(self) -> list[Outil]:
        return [
            Outil("minuteur_creer", "Lancer un minuteur",
                  lambda a: (self.creer(float(a["secondes"]), a.get("nom", "")) and "Minuteur lancé."),
                  {"secondes": {"type": "number"}, "nom": {"type": "string"}}, ("secondes",), 1, True, "utilitaires"),
            Outil("heure", "Donner l'heure et la date", lambda a: f"{self.heure_parlee()} {self.date_parlee()}",
                  {}, (), 1, True, "utilitaires"),
        ]
