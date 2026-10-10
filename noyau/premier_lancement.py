"""
KRYPTONIX / noyau/premier_lancement.py
========================================
Préparation 100 % automatique au premier démarrage :

    1. Ollama n'est pas installé  -> on télécharge OllamaSetup.exe et on
       l'installe en silence (aucune fenêtre, aucun clic, aucun droit admin).
    2. Ollama est installé mais éteint -> on le démarre en arrière-plan.
    3. Le modèle n'est pas téléchargé -> on le télécharge avec une progression.

L'utilisateur n'ouvre JAMAIS de terminal et n'a pas besoin de savoir ce
qu'est Ollama. Tout est idempotent : les lancements suivants sautent chaque
étape déjà faite et sont instantanés (et fonctionnent hors ligne).

`preparer()` ne lève jamais d'exception : au pire elle renvoie un rapport
avec un message d'erreur lisible, que l'écran de préparation affiche avec
un bouton « Réessayer ».
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time

import requests

LOG = logging.getLogger("premier_lancement")

EST_WINDOWS = platform.system() == "Windows"
URL_OLLAMA_WINDOWS = "https://ollama.com/download/OllamaSetup.exe"
# Drapeaux silencieux de l'installateur officiel d'Ollama (Inno Setup).
DRAPEAUX_SILENCIEUX = ["/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]

# Place disque approximative nécessaire (Go) : Ollama + modèle.
ESPACE_REQUIS_GO = {"mistral": 6.5, "gemma:2b": 3.5, "llama3.2:3b": 4.5}

# Fenêtre console masquée pour tout sous-processus lancé sous Windows.
SANS_FENETRE = getattr(subprocess, "CREATE_NO_WINDOW", 0) if EST_WINDOWS else 0


# ======================================================================
#  Outils de base
# ======================================================================
def chemin_ollama() -> str | None:
    """Retourne le chemin de l'exécutable ollama, ou None s'il est absent."""
    trouve = shutil.which("ollama")
    if trouve:
        return trouve
    if EST_WINDOWS:
        candidats = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe"),
        ]
        for candidat in candidats:
            if candidat and os.path.isfile(candidat):
                return candidat
    return None


def ollama_installe() -> bool:
    return chemin_ollama() is not None


def ollama_repond(hote: str, tentatives: int = 1) -> bool:
    for _ in range(tentatives):
        try:
            requests.get(f"{hote}/api/tags", timeout=3)
            return True
        except requests.RequestException:
            time.sleep(1)
    return False


def modele_present(hote: str, modele: str) -> bool:
    """Vrai si le modèle (nom complet avec étiquette) est déjà téléchargé."""
    try:
        reponse = requests.get(f"{hote}/api/tags", timeout=5)
        noms = [m.get("name", "") for m in reponse.json().get("models", [])]
    except (requests.RequestException, ValueError):
        return False
    if ":" in modele:
        return modele in noms
    # « mistral » sans étiquette = « mistral:latest »
    return modele in {n.split(":")[0] for n in noms}


def choisir_modele(config: dict) -> str:
    """
    Choisit le modèle selon la mémoire de la machine, sauf si l'utilisateur a
    désactivé `modele_auto`. Moins de 8 Go de RAM : petit modèle (rapide, léger).
    Si un modèle est déjà téléchargé et configuré, on ne change rien.
    """
    modele = config.get("ollama_modele", "mistral")
    if not config.get("modele_auto", True):
        return modele
    try:
        import psutil
        ram_go = psutil.virtual_memory().total / 1024 ** 3
    except Exception:
        return modele
    from .config import MODELES_LOURDS
    if ram_go < 7.5 and modele in MODELES_LOURDS:
        LOG.info("RAM %.1f Go : modèle léger choisi automatiquement.", ram_go)
        return "gemma:2b"
    return modele


# ======================================================================
#  Étape 1 : installation silencieuse d'Ollama
# ======================================================================
def installer_ollama(suivi) -> bool:
    """
    Télécharge puis installe Ollama sans aucune interaction.
    `suivi(pourcentage_0_100, message)` reçoit la progression de cette étape.
    """
    if not EST_WINDOWS:
        LOG.warning("Installation automatique d'Ollama prévue pour Windows uniquement.")
        return False

    destination = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
    try:
        suivi(0, "Téléchargement du moteur d'IA…")
        with requests.get(URL_OLLAMA_WINDOWS, stream=True, timeout=30,
                          allow_redirects=True) as reponse:
            reponse.raise_for_status()
            total = int(reponse.headers.get("content-length", 0))
            recu = 0
            with open(destination, "wb") as fichier:
                for bloc in reponse.iter_content(chunk_size=1024 * 256):
                    if not bloc:
                        continue
                    fichier.write(bloc)
                    recu += len(bloc)
                    if total:
                        suivi(int(recu / total * 85),
                              f"Téléchargement du moteur d'IA… {recu / 1e6:.0f} / {total / 1e6:.0f} Mo")

        suivi(88, "Installation du moteur d'IA (quelques instants)…")
        resultat = subprocess.run([destination] + DRAPEAUX_SILENCIEUX,
                                  creationflags=SANS_FENETRE, timeout=900)
        if resultat.returncode != 0:
            LOG.error("OllamaSetup a retourné le code %s", resultat.returncode)
        # L'installateur peut rendre la main un peu avant la fin des copies.
        for _ in range(30):
            if ollama_installe():
                suivi(100, "Moteur d'IA installé.")
                return True
            time.sleep(1)
        return False
    except (requests.RequestException, OSError, subprocess.SubprocessError) as erreur:
        LOG.error("Installation d'Ollama impossible : %s", erreur)
        return False
    finally:
        try:
            os.remove(destination)
        except OSError:
            pass


# ======================================================================
#  Étape 2 : démarrage du service
# ======================================================================
def demarrer_ollama(hote: str) -> bool:
    """Lance `ollama serve` en arrière-plan (sans fenêtre) si rien ne répond."""
    if ollama_repond(hote):
        return True
    exe = chemin_ollama()
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=SANS_FENETRE)
    except OSError as erreur:
        LOG.warning("Impossible de démarrer Ollama : %s", erreur)
        return False
    return ollama_repond(hote, tentatives=25)


# ======================================================================
#  Étape 3 : téléchargement du modèle
# ======================================================================
def telecharger_modele(hote: str, modele: str, rappel_progression=None) -> bool:
    """
    Télécharge le modèle via /api/pull (reprise automatique si coupure).
    `rappel_progression(pourcentage, message)` est appelé en continu.
    """
    def annoncer(pct: int, message: str) -> None:
        if rappel_progression:
            rappel_progression(pct, message)

    for tentative in range(1, 4):
        couches: dict[str, list[int]] = {}
        try:
            with requests.post(f"{hote}/api/pull", json={"name": modele, "stream": True},
                               stream=True, timeout=(10, 120)) as reponse:
                reponse.raise_for_status()
                for ligne in reponse.iter_lines(decode_unicode=True):
                    if not ligne:
                        continue
                    try:
                        donnees = json.loads(ligne)
                    except ValueError:
                        continue
                    if donnees.get("error"):
                        raise requests.RequestException(donnees["error"])
                    if donnees.get("digest") and donnees.get("total"):
                        couches[donnees["digest"]] = [donnees["total"], donnees.get("completed", 0)]
                    total = sum(c[0] for c in couches.values())
                    fait = sum(c[1] for c in couches.values())
                    pct = int(fait / total * 100) if total else 0
                    if couches:
                        annoncer(pct, f"Téléchargement du cerveau… {fait / 1e9:.1f} / {total / 1e9:.1f} Go")
                    else:
                        annoncer(0, "Préparation du téléchargement…")
                    if donnees.get("status") == "success":
                        annoncer(100, "Cerveau prêt.")
                        return True
            if modele_present(hote, modele):
                return True
        except requests.RequestException as erreur:
            LOG.warning("Téléchargement du modèle, tentative %d/3 : %s", tentative, erreur)
            annoncer(0, f"Connexion interrompue, nouvelle tentative ({tentative}/3)…")
            time.sleep(3)
    return modele_present(hote, modele)


# ======================================================================
#  Point d'entrée
# ======================================================================
def preparer(config: dict, rappel_progression=None, suivi=None) -> dict:
    """
    Prépare tout. `suivi(dict)` reçoit {etape, pourcentage, message} à chaque
    avancée (pourcentage global 0-100). `rappel_progression(pct, msg)` garde la
    compatibilité avec l'ancien lanceur en console.
    Retourne {ollama_ok, modele_ok, modele, action, erreur}.
    """
    hote = config["ollama_hote"].rstrip("/")
    modele = choisir_modele(config)
    rapport = {"ollama_ok": False, "modele_ok": False, "modele": modele,
               "action": "aucune", "erreur": ""}

    def emettre(etape: str, pct: int, message: str) -> None:
        pct = max(0, min(100, pct))
        if suivi:
            suivi({"etape": etape, "pourcentage": pct, "message": message})
        if rappel_progression:
            rappel_progression(pct, message)

    try:
        # ---- 1. Ollama présent ? ----
        if not ollama_repond(hote) and not ollama_installe():
            rapport["action"] = "installation d'Ollama"
            besoin = ESPACE_REQUIS_GO.get(modele, 6.0)
            try:
                libre = shutil.disk_usage(os.path.expanduser("~")).free / 1024 ** 3
                if libre < besoin:
                    rapport["erreur"] = (f"Espace disque insuffisant : {besoin:.0f} Go nécessaires, "
                                         f"{libre:.1f} Go disponibles. Libère de la place puis réessaie.")
                    return rapport
            except OSError:
                pass
            if not installer_ollama(lambda p, m: emettre("installation", int(p * 0.25), m)):
                rapport["erreur"] = (
                    "Le moteur d'IA n'a pas pu être installé automatiquement. "
                    "Vérifie ta connexion internet puis clique sur « Réessayer »."
                    if EST_WINDOWS else
                    "Installe Ollama depuis https://ollama.com/download puis relance l'application.")
                return rapport

        # ---- 2. Service démarré ? ----
        emettre("demarrage", 27, "Démarrage du moteur d'IA…")
        rapport["ollama_ok"] = demarrer_ollama(hote)
        if not rapport["ollama_ok"]:
            rapport["erreur"] = "Le moteur d'IA ne répond pas. Redémarre l'application ou ton PC."
            return rapport

        # ---- 3. Modèle téléchargé ? ----
        if modele_present(hote, modele):
            rapport["modele_ok"] = True
            emettre("pret", 100, "Tout est prêt.")
            return rapport

        if rapport["action"] == "aucune":
            rapport["action"] = f"téléchargement du modèle {modele}"
        rapport["modele_ok"] = telecharger_modele(
            hote, modele, lambda p, m: emettre("modele", 30 + int(p * 0.70), m))
        if not rapport["modele_ok"]:
            rapport["erreur"] = ("Le cerveau n'a pas pu être téléchargé. "
                                 "Vérifie ta connexion internet puis clique sur « Réessayer ».")
            return rapport

        emettre("pret", 100, "Tout est prêt.")
        return rapport
    except Exception as erreur:  # garde-fou : ne jamais faire planter l'application
        LOG.exception("Erreur inattendue pendant la préparation")
        rapport["erreur"] = f"Erreur inattendue : {erreur}"
        return rapport
