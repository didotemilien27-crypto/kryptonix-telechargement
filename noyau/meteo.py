"""
KRYPTONIX / noyau/meteo.py
===========================
Météo en direct, 100 % gratuite et sans clé API : géocodage + prévisions
via Open-Meteo (open-meteo.com). Mise en cache locale pour ne pas
solliciter le réseau à chaque rafraîchissement du dashboard.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

LOG = logging.getLogger("meteo")

URL_GEOCODAGE = "https://geocoding-api.open-meteo.com/v1/search"
URL_PREVISIONS = "https://api.open-meteo.com/v1/forecast"

# Codes météo OMM (WMO) → (description française, symbole)
CODES_MET = {
    0: ("ciel dégagé", "☀"), 1: ("plutôt dégagé", "🌤"), 2: ("partiellement nuageux", "⛅"),
    3: ("couvert", "☁"), 45: ("brouillard", "🌫"), 48: ("brouillard givrant", "🌫"),
    51: ("bruine légère", "🌦"), 53: ("bruine", "🌦"), 55: ("bruine dense", "🌦"),
    56: ("bruine verglaçante", "🌧"), 57: ("bruine verglaçante forte", "🌧"),
    61: ("pluie légère", "🌧"), 63: ("pluie", "🌧"), 65: ("forte pluie", "🌧"),
    66: ("pluie verglaçante", "🌧"), 67: ("forte pluie verglaçante", "🌧"),
    71: ("neige légère", "🌨"), 73: ("neige", "🌨"), 75: ("forte neige", "❄"),
    77: ("grains de neige", "🌨"), 80: ("averses légères", "🌦"), 81: ("averses", "🌧"),
    82: ("averses violentes", "⛈"), 85: ("averses de neige", "🌨"), 86: ("fortes averses de neige", "❄"),
    95: ("orage", "⛈"), 96: ("orage avec grêle", "⛈"), 99: ("orage violent avec grêle", "⛈"),
}


class Meteo:
    """Météo en direct pour la ville configurée (ou une ville précisée à l'oral)."""

    def __init__(self, config: dict):
        self.config = config
        self._verrou = threading.RLock()
        self._coords_cache: dict[str, tuple[float, float, str]] = {}
        self._cache: dict[str, dict] = {}   # ville (clé normalisée) -> {donnees, a}

    # ------------------------------------------------------------------
    def _geocoder(self, ville: str) -> tuple[float, float, str] | None:
        cle = ville.strip().lower()
        if cle in self._coords_cache:
            return self._coords_cache[cle]
        try:
            reponse = requests.get(
                URL_GEOCODAGE,
                params={"name": ville, "count": 1, "language": "fr", "format": "json"},
                timeout=6,
            )
            reponse.raise_for_status()
            resultats = reponse.json().get("results") or []
            if not resultats:
                return None
            premier = resultats[0]
            coords = (premier["latitude"], premier["longitude"], premier.get("name", ville))
            self._coords_cache[cle] = coords
            return coords
        except requests.RequestException as erreur:
            LOG.warning("Géocodage impossible pour %s : %s", ville, erreur)
            return None

    def _telecharger(self, ville: str) -> dict:
        coords = self._geocoder(ville)
        if not coords:
            return {"succes": False, "erreur": f"ville introuvable : {ville}"}
        latitude, longitude, nom_trouve = coords
        try:
            reponse = requests.get(
                URL_PREVISIONS,
                params={
                    "latitude": latitude, "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                              "precipitation,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                    "timezone": "auto", "forecast_days": 1,
                },
                timeout=6,
            )
            reponse.raise_for_status()
            brut = reponse.json()
            courant = brut.get("current", {})
            quotidien = brut.get("daily", {})
            code = int(courant.get("weather_code", 0))
            description, symbole = CODES_MET.get(code, ("temps indéterminé", "🌡"))
            return {
                "succes": True,
                "ville": nom_trouve,
                "temperature": round(courant.get("temperature_2m", 0)),
                "ressenti": round(courant.get("apparent_temperature", 0)),
                "humidite": courant.get("relative_humidity_2m"),
                "vent": round(courant.get("wind_speed_10m", 0)),
                "precipitation": courant.get("precipitation", 0),
                "description": description,
                "symbole": symbole,
                "min_jour": round(quotidien.get("temperature_2m_min", [0])[0]) if quotidien.get("temperature_2m_min") else None,
                "max_jour": round(quotidien.get("temperature_2m_max", [0])[0]) if quotidien.get("temperature_2m_max") else None,
                "maj": time.time(),
            }
        except requests.RequestException as erreur:
            LOG.warning("Prévisions indisponibles pour %s : %s", ville, erreur)
            return {"succes": False, "erreur": "service météo injoignable"}

    # ------------------------------------------------------------------
    def actuel(self, ville: str | None = None) -> dict:
        """Retourne la météo (cache si fraîche, sinon rafraîchit)."""
        ville = (ville or self.config.get("ville_meteo") or "Paris").strip()
        intervalle = float(self.config.get("meteo_intervalle", 900))
        cle = ville.lower()
        with self._verrou:
            entree = self._cache.get(cle)
            if entree and (time.time() - entree["a"]) < intervalle:
                return entree["donnees"]
        donnees = self._telecharger(ville)
        with self._verrou:
            self._cache[cle] = {"donnees": donnees, "a": time.time()}
        return donnees

    def oublier_ville(self, ville: str) -> None:
        with self._verrou:
            self._cache.pop(ville.strip().lower(), None)
            self._coords_cache.pop(ville.strip().lower(), None)

    def resume_parle(self, ville: str | None = None) -> str:
        donnees = self.actuel(ville)
        if not donnees.get("succes"):
            return (f"Impossible de récupérer la météo ({donnees.get('erreur', 'erreur inconnue')})."
                    " Vérifie la connexion internet ou le nom de la ville.")
        extremes = ""
        if donnees.get("min_jour") is not None and donnees.get("max_jour") is not None:
            extremes = f" Minimale {donnees['min_jour']}°, maximale {donnees['max_jour']}° aujourd'hui."
        return (f"À {donnees['ville']} : {donnees['description']}, {donnees['temperature']}° "
                f"(ressenti {donnees['ressenti']}°), vent à {donnees['vent']} km/h, "
                f"humidité {donnees['humidite']} %.{extremes}")
