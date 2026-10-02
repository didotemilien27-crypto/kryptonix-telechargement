"""
KRYPTONIX / noyau/mise_a_jour.py
=================================
Mises à jour automatiques via les « Releases » GitHub.

Tu publies une nouvelle version (tag v1.1.0) -> GitHub fabrique l'installateur
-> chez les utilisateurs, l'application le voit au démarrage, le télécharge
discrètement, puis l'installe quand l'utilisateur ferme l'application.
Mémoire, documents et réglages (dans %APPDATA%\\Kryptonix) sont conservés.

Rien ne plante si GitHub est injoignable ou si la version est déjà à jour.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile

import requests

from .config import DOSSIER_PROGRAMME
from .version import VERSION

LOG = logging.getLogger("mise_a_jour")
NOM_INSTALLATEUR = "Kryptonix-Setup.exe"


def depot_github(config: dict) -> str:
    """'pseudo/depot' : lu dans config.json, sinon dans ressources/depot.txt (injecté au build)."""
    depot = (config.get("depot_github") or "").strip()
    if depot and "TON-PSEUDO" not in depot:
        return depot
    chemin = os.path.join(DOSSIER_PROGRAMME, "ressources", "depot.txt")
    try:
        with open(chemin, encoding="utf-8") as fichier:
            depot = fichier.read().strip()
        return "" if "TON-PSEUDO" in depot else depot
    except OSError:
        return ""


def _version_en_tuple(texte: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", texte)[:4]) or (0,)


def verifier(config: dict) -> dict | None:
    """Retourne {version, url} si une version plus récente existe, sinon None."""
    depot = depot_github(config)
    if not depot or "/" not in depot:
        return None
    try:
        reponse = requests.get(f"https://api.github.com/repos/{depot}/releases/latest",
                               timeout=8, headers={"Accept": "application/vnd.github+json"})
        if reponse.status_code != 200:
            return None
        donnees = reponse.json()
        derniere = donnees.get("tag_name", "")
        if _version_en_tuple(derniere) <= _version_en_tuple(VERSION):
            return None
        for fichier in donnees.get("assets", []):
            if fichier.get("name") == NOM_INSTALLATEUR:
                return {"version": derniere.lstrip("v"), "url": fichier["browser_download_url"]}
    except (requests.RequestException, ValueError) as erreur:
        LOG.info("Vérification de mise à jour impossible : %s", erreur)
    return None


def telecharger(url: str) -> str | None:
    """Télécharge l'installateur dans le dossier temporaire. Retourne son chemin."""
    destination = os.path.join(tempfile.gettempdir(), NOM_INSTALLATEUR)
    try:
        with requests.get(url, stream=True, timeout=30) as reponse:
            reponse.raise_for_status()
            with open(destination + ".part", "wb") as fichier:
                for bloc in reponse.iter_content(chunk_size=1024 * 256):
                    fichier.write(bloc)
        os.replace(destination + ".part", destination)
        return destination
    except (requests.RequestException, OSError) as erreur:
        LOG.info("Téléchargement de la mise à jour impossible : %s", erreur)
        return None


def installer_maintenant(chemin: str) -> None:
    """Lance l'installateur en mode silencieux, détaché, puis rend la main."""
    if os.name != "nt" or not chemin or not os.path.isfile(chemin):
        return
    drapeaux = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([chemin, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=drapeaux, close_fds=True)
