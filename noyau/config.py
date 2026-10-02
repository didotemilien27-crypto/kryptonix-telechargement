"""
KRYPTONIX / noyau/config.py
============================
Chargement, fusion et persistance de la configuration.
Tout est pilotable depuis `config.json` : aucun paramètre n'est codé en dur ailleurs.
"""

from __future__ import annotations

import json
import logging
import os
import sys

# ------------------------------------------------------------------
#  Chemins de référence
# ------------------------------------------------------------------
# En développement (python lancer.py) : racine = dossier parent de /noyau.
# Une fois packagé en .exe (PyInstaller) : sys.frozen vaut True, et il faut
# utiliser le dossier où se trouve l'exécutable, pas le dossier temporaire
# d'extraction — sinon la mémoire et les documents seraient perdus à chaque
# redémarrage de l'application.
if getattr(sys, "frozen", False):
    # Application installée : le dossier du programme peut être en lecture seule
    # et il est effacé à chaque mise à jour. Mémoire, documents, réglages et logs
    # vivent donc dans le profil de l'utilisateur, où ils survivent aux mises à jour.
    _base = os.environ.get("APPDATA") or os.path.expanduser("~")
    RACINE = os.path.join(_base, "Kryptonix")
    DOSSIER_PROGRAMME = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DOSSIER_PROGRAMME = RACINE
os.makedirs(RACINE, exist_ok=True)
DOSSIER_DONNEES = os.path.join(RACINE, "donnees")
DOSSIER_LOGS = os.path.join(RACINE, "logs")
DOSSIER_SANDBOX = os.path.join(RACINE, "sandbox")
CHEMIN_CONFIG = os.path.join(RACINE, "config.json")
CHEMIN_DB = os.path.join(DOSSIER_DONNEES, "kryptonix.db")
CHEMIN_LOG = os.path.join(DOSSIER_LOGS, "kryptonix.log")

if getattr(sys, "frozen", False) and not os.path.exists(CHEMIN_CONFIG):
    _modele = os.path.join(DOSSIER_PROGRAMME, "config.json")
    if os.path.exists(_modele):
        try:
            import shutil as _shutil
            _shutil.copyfile(_modele, CHEMIN_CONFIG)
        except OSError:
            pass

for _dossier in (DOSSIER_DONNEES, DOSSIER_LOGS, DOSSIER_SANDBOX):
    os.makedirs(_dossier, exist_ok=True)

# ------------------------------------------------------------------
#  Valeurs par défaut
# ------------------------------------------------------------------
CONFIG_PAR_DEFAUT = {
    "nom_assistant": "Kryptonix",
    "nom_utilisateur": "Monsieur",
    "mot_reveil": "kryptonix",

    # --- Cerveau local (Ollama) ---
    "ollama_hote": "http://localhost:11434",
    "ollama_modele": "mistral",
    "ollama_timeout": 120,
    "temperature": 0.75,
    "contexte_max_messages": 12,
    "num_ctx": 4096,
    "modele_auto": True,             # choisit un petit modèle si la machine a < 8 Go de RAM
    "depot_github": "",              # "pseudo/depot" pour les mises à jour automatiques

    # --- Mode léger (PC portable modeste : peu de RAM, pas de GPU) ---
    "mode_leger": False,

    # --- Voix ---
    "voix_active": True,
    "moteur_tts": "auto",            # auto | edge | piper | pyttsx3 | gtts | muet
    "langue_voix": "fr",
    "voix_style": "vivienne",  # vivienne | remy | denise | henri | eloise | accents régionaux...
    "langue_reconnaissance": "fr-FR",
    "vitesse_voix": 185,             # mots/minute
    "hauteur_voix": 0,               # -30 à +30 (grave à aigu, edge-tts)
    "timeout_ecoute": 6,
    "duree_max_phrase": 12,
    "ecoute_permanente": False,      # True = boucle micro avec mot de réveil

    # --- Apparence de l'interface web ---
    "apparence_palette": "cristal",   # cristal | ambre | magenta | emeraude | violet | mono
    "apparence_forme": "nette",       # nette | douce
    "apparence_taille": "normale",    # compacte | normale | grande

    # --- Interfaces ---
    "web_hote": "127.0.0.1",
    "web_port": 5000,
    "hud_actif": True,
    "hud_opacite": 0.92,
    "hud_position": "bas_droite",    # bas_droite | bas_gauche | haut_droite | haut_gauche

    # --- Agents & système ---
    "telemetrie_intervalle": 2.0,
    "sandbox_timeout": 12,
    "sandbox_active": True,
    "ingestion_auto_dossiers": [],   # dossiers aspirés au démarrage (ex: ancien projet Ultron)
    "ville_meteo": "Paris",

    # --- Météo & actualités en direct ---
    "meteo_intervalle": 900,          # secondes entre deux rafraîchissements météo (15 min)
    "actualites_intervalle": 1800,    # secondes entre deux rafraîchissements des flux (30 min)
    "flux_rss": [
        "https://www.francetvinfo.fr/titres.rss",
        "https://www.lemonde.fr/rss/une.xml",
    ],
}


# Modèles Ollama légers, du plus petit au plus capable — utilisés par --leger
# quand l'utilisateur n'a pas explicitement choisi de modèle.
MODELES_LEGERS = ("gemma:2b", "llama3.2:1b", "llama3.2:3b", "qwen2.5:1.5b", "phi3:mini")

# Modèles considérés "lourds" : --leger les remplace automatiquement.
MODELES_LOURDS = ("mistral", "llama3.1:8b", "llama3", "qwen2.5:14b", "qwen2.5:7b",
                  "mixtral", "gemma2:9b", "gemma2:27b")


def charger_config() -> dict:
    """Charge config.json, complète les clés manquantes, réécrit le fichier si besoin."""
    config = dict(CONFIG_PAR_DEFAUT)
    if os.path.exists(CHEMIN_CONFIG):
        try:
            with open(CHEMIN_CONFIG, "r", encoding="utf-8") as fichier:
                config.update(json.load(fichier))
        except (json.JSONDecodeError, OSError) as erreur:
            logging.warning("config.json illisible (%s) : valeurs par défaut utilisées.", erreur)
    sauvegarder_config(config)
    return config


def sauvegarder_config(config: dict) -> None:
    """Écrit la configuration sur disque (création incluse)."""
    try:
        with open(CHEMIN_CONFIG, "w", encoding="utf-8") as fichier:
            json.dump(config, fichier, indent=4, ensure_ascii=False)
    except OSError as erreur:
        logging.warning("Impossible d'écrire config.json : %s", erreur)


def initialiser_logs(niveau: int = logging.INFO) -> None:
    """Configure le logging global : fichier + console, une seule fois."""
    racine = logging.getLogger()
    if racine.handlers:  # déjà initialisé
        return
    racine.setLevel(niveau)
    format_log = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)-12s | %(message)s", "%H:%M:%S"
    )

    fichier = logging.FileHandler(CHEMIN_LOG, encoding="utf-8")
    fichier.setFormatter(format_log)
    racine.addHandler(fichier)

    console = logging.StreamHandler()
    console.setFormatter(format_log)
    racine.addHandler(console)

    # On fait taire le bavardage des bibliothèques tierces
    for bruyant in ("werkzeug", "urllib3", "comtypes", "PIL"):
        logging.getLogger(bruyant).setLevel(logging.ERROR)
