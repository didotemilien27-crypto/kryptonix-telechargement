"""
KRYPTONIX / noyau/actualites.py
================================
Fil d'actualités en direct : lecture de flux RSS publics (aucune clé API,
aucune dépendance supplémentaire — juste `requests` et `xml.etree`, déjà
utilisés ailleurs dans le projet). Résultat mis en cache pour épargner
le réseau et les sites sources.
"""

from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET

import requests

LOG = logging.getLogger("actualites")

FLUX_PAR_DEFAUT = [
    "https://www.francetvinfo.fr/titres.rss",
    "https://www.lemonde.fr/rss/une.xml",
]


class Actualites:
    """Agrège quelques flux RSS d'actualité en un digest simple."""

    def __init__(self, config: dict):
        self.config = config
        self._verrou = threading.RLock()
        self._cache: list[dict] = []
        self._derniere_maj = 0.0
        self._erreur: str = ""

    # ------------------------------------------------------------------
    def _flux(self) -> list[str]:
        flux = self.config.get("flux_rss") or FLUX_PAR_DEFAUT
        return [f for f in flux if f]

    def _lire_flux(self, url: str, par_flux: int = 6) -> list[dict]:
        titres: list[dict] = []
        try:
            reponse = requests.get(url, timeout=6, headers={"User-Agent": "Kryptonix/1.0"})
            reponse.raise_for_status()
            racine = ET.fromstring(reponse.content)
            for item in racine.iter("item"):
                titre = (item.findtext("title") or "").strip()
                lien = (item.findtext("link") or "").strip()
                if not titre:
                    continue
                titres.append({"titre": titre, "lien": lien, "source": _domaine(url)})
                if len(titres) >= par_flux:
                    break
        except (requests.RequestException, ET.ParseError) as erreur:
            LOG.warning("Flux RSS illisible (%s) : %s", url, erreur)
        return titres

    def _rafraichir(self) -> None:
        agrege: list[dict] = []
        echecs = 0
        for url in self._flux():
            lot = self._lire_flux(url)
            if not lot:
                echecs += 1
            agrege.extend(lot)
        with self._verrou:
            if agrege:
                self._cache = agrege
                self._erreur = ""
            elif not self._cache:
                self._erreur = "aucun flux d'actualité n'a répondu"
            self._derniere_maj = time.time()

    # ------------------------------------------------------------------
    def titres(self, limite: int = 12) -> list[dict]:
        intervalle = float(self.config.get("actualites_intervalle", 1800))
        with self._verrou:
            perime = (time.time() - self._derniere_maj) > intervalle
        if perime or not self._cache:
            self._rafraichir()
        with self._verrou:
            return list(self._cache[:limite])

    def resume_parle(self, nombre: int = 5) -> str:
        lot = self.titres(nombre)
        if not lot:
            with self._verrou:
                erreur = self._erreur or "le fil d'actualité est vide pour l'instant"
            return f"Je ne peux pas récupérer les actualités : {erreur}."
        phrases = "; ".join(item["titre"] for item in lot[:nombre])
        return f"Les titres du moment — {phrases}."


def _domaine(url: str) -> str:
    sans_schema = url.split("//", 1)[-1]
    hote = sans_schema.split("/", 1)[0]
    return hote.replace("www.", "")
