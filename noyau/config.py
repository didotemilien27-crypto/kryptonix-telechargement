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
# En développement (python app.py) : racine = dossier parent de /noyau.
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
DOSSIER_MODELES = os.path.join(RACINE, "modeles")      # modèle d'oreille (transcription), téléchargé une fois
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

for _dossier in (DOSSIER_DONNEES, DOSSIER_LOGS, DOSSIER_MODELES):
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
    "modele_auto": True,             # 1er lancement : petit modèle si la machine a < 8 Go de RAM
    "depot_github": "",              # "pseudo/depot" pour les mises à jour automatiques

    # --- Voix ---
    "voix_active": True,
    "moteur_tts": "auto",            # auto | edge (neuronale, en ligne) | pyttsx3 (système) | muet
    "voix_style": "vivienne",        # voix neuronale : alias (vivienne, remy...) ou nom complet Edge
    "voix_systeme": "",              # voix installée sur Windows, utilisée hors ligne ("" = automatique)
    "langue_reconnaissance": "fr-FR",
    "vitesse_voix": 185,             # mots/minute (180 = débit normal)
    "hauteur_voix": 0,               # -50 à +50 Hz (grave à aigu, voix neuronales uniquement)
    "volume_voix": 100,              # 0 à 100
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
    "defilement_infos": 3,            # vitesse du fil d'infos : 0 (arrêté) à 10 (très rapide)

    # --- Agents & système ---
    "telemetrie_intervalle": 2.0,     # secondes entre deux mesures du matériel
    "sandbox_timeout": 12,
    "sandbox_active": True,

    # --- Compréhension des médias (audio, images, vidéos) ---
    "modele_audio": "base",          # précision de l'oreille : tiny | base | small
    "video_images": 6,               # nombre d'images clés regardées dans une vidéo
    "ouverture_sujets": True,        # quand je ne peux pas répondre : proposer un autre sujet
    "ingestion_auto_dossiers": [],   # dossiers aspirés au démarrage (ex: ancien projet Ultron)
    "ville_meteo": "Paris",

    # --- Météo & actualités en direct ---
    "meteo_intervalle": 900,          # secondes entre deux rafraîchissements météo (15 min)
    "actualites_intervalle": 1800,    # secondes entre deux rafraîchissements des flux (30 min)
    "flux_rss": [
        "https://www.francetvinfo.fr/titres.rss",
        "https://www.lemonde.fr/rss/une.xml",
    ],

    # ==================================================================
    #  Modules « Jarvis » (noyau/extensions) — tout est optionnel, rien n'est actif tant
    #  que tu n'as pas renseigné le réglage correspondant (panneau → Réglages des modules)
    # ==================================================================
    # --- Intelligence : local | hybride | qualite ---
    "mode_ia": "local",
    "fournisseur_llm": "anthropic",          # anthropic | openai (toute API compatible)
    "llm_modele_quotidien": "claude-haiku-4-5-20251001",
    "llm_modele_qualite": "claude-opus-5-5",
    "cles_api": {},                          # {"anthropic": "...", "openai": "..."} (ou variables d'environnement)
    "openai_base_url": "https://api.openai.com/v1",
    "budget_mensuel": 5.0,                   # plafond en dollars ; au-delà : retour automatique en local
    "cloud_documents": False,                # False : tes documents ne partent JAMAIS vers le cloud
    "personnalite": "majordome",
    "personnalite_voix": True,
    "sons_actifs": True,
    "sons_volume": 70,
    "sons_reflexion": False,
    "delai_securite_n3": 10,
    # --- Voix locale ---
    "stt_moteur": "auto",                    # auto | whisper | google
    "wakeword_actif": False,
    "wakeword_modele": "hey_jarvis",
    "wakeword_seuil": 0.5,
    # --- Domotique / PC ---
    "hue_pont": "", "hue_cle": "",
    "presence_actif": False, "presence_telephones": [],
    "presence_scene_depart": "eteindre", "presence_scene_retour": "detente",
    "wol_machines": {}, "applications": {},
    # --- Agenda, mails ---
    "agenda_ical": [], "google_calendriers": [],
    "gmail_adresse": "",
    # --- Réseaux, téléphone, OBS ---
    "discord_salons": [], "discord_ecoute": False, "instagram_comptes": [],
    "twilio_contacts": {}, "twilio_repondre_entrants": False,
    "obs_hote": "127.0.0.1", "obs_port": 4455,
    # --- Agent délégué, réseau local, MCP, gestes ---
    "agent_modele": "hermes3", "agent_url": "", "agent_actions": True,
    "reseau_actif": False, "reseau_port": 5055, "reseau_ecoute_lan": True,
    "satellite_tts_serveur": False, "satellite_exige_mot_reveil": True,
    "mcp_actif": True, "gestes_actif": False,
}


# Modèles volumineux : au tout premier lancement, sur un PC de moins de 8 Go de RAM,
# ils sont remplacés par un petit modèle (sauf si `modele_auto` est désactivé).
MODELES_LOURDS = ("mistral", "llama3.1:8b", "llama3", "qwen2.5:14b", "qwen2.5:7b",
                  "mixtral", "gemma2:9b", "gemma2:27b")

# Clés abandonnées : retirées automatiquement des anciens config.json.
CLES_OBSOLETES = ("mode_leger", "hud_actif", "hud_opacite", "hud_position",
                  "piper_modele", "piper_executable", "langue_voix")


def charger_config() -> dict:
    """Charge config.json, complète les clés manquantes, réécrit le fichier si besoin."""
    config = dict(CONFIG_PAR_DEFAUT)
    if os.path.exists(CHEMIN_CONFIG):
        try:
            with open(CHEMIN_CONFIG, "r", encoding="utf-8") as fichier:
                config.update(json.load(fichier))
            for cle in CLES_OBSOLETES:
                config.pop(cle, None)
            # Moteurs vocaux supprimés (gtts, piper...) : retour au choix automatique.
            if config.get("moteur_tts") not in ("auto", "edge", "pyttsx3", "muet"):
                config["moteur_tts"] = "auto"
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


# ------------------------------------------------------------------
#  Réglages modifiables depuis l'interface (onglet RÉGLAGES)
# ------------------------------------------------------------------
# Chaque entrée : (type, contrainte). Toute valeur reçue est validée et bornée
# ici, côté serveur : un réglage absurde ne peut pas casser l'application.
REGLAGES: dict[str, tuple] = {
    # apparence
    "apparence_palette": ("choix", ("cristal", "ambre", "magenta", "emeraude", "violet", "mono")),
    "apparence_forme":   ("choix", ("nette", "douce")),
    "apparence_taille":  ("choix", ("compacte", "normale", "grande")),
    # voix
    "voix_active":   ("bool", None),
    "moteur_tts":    ("choix", ("auto", "edge", "pyttsx3", "muet")),
    "voix_style":    ("texte", 80),
    "voix_systeme":  ("texte", 160),
    "vitesse_voix":  ("entier", (100, 300)),
    "hauteur_voix":  ("entier", (-50, 50)),
    "volume_voix":   ("entier", (0, 100)),
    # informations en direct
    "defilement_infos":      ("entier", (0, 10)),
    "meteo_intervalle":      ("entier", (60, 86400)),
    "actualites_intervalle": ("entier", (60, 86400)),
    "telemetrie_intervalle": ("decimal", (0.5, 60.0)),
    "ville_meteo":           ("texte", 80),
    "flux_rss":              ("liste_url", 10),
    # assistant
    "nom_utilisateur": ("texte", 40),
    "temperature":     ("decimal", (0.0, 1.5)),
    "ollama_modele":   ("texte", 80),
    # bac à sable
    "sandbox_active":  ("bool", None),
    "sandbox_timeout": ("entier", (2, 60)),
    # médias
    "modele_audio":     ("choix", ("tiny", "base", "small")),
    "video_images":     ("entier", (3, 12)),
    "ouverture_sujets": ("bool", None),
}


def valider_reglage(cle: str, valeur):
    """Retourne la valeur nettoyée et bornée, ou lève ValueError."""
    if cle not in REGLAGES:
        raise ValueError(f"réglage inconnu : {cle}")
    genre, contrainte = REGLAGES[cle]
    if genre == "bool":
        if isinstance(valeur, str):
            return valeur.strip().lower() in ("1", "true", "oui", "on")
        return bool(valeur)
    if genre == "choix":
        if valeur not in contrainte:
            raise ValueError(f"{cle} : valeur non autorisée")
        return valeur
    if genre in ("entier", "decimal"):
        try:
            nombre = float(valeur)
        except (TypeError, ValueError):
            raise ValueError(f"{cle} : nombre attendu") from None
        bas, haut = contrainte
        nombre = max(bas, min(haut, nombre))
        return int(round(nombre)) if genre == "entier" else round(nombre, 2)
    if genre == "texte":
        texte = str(valeur if valeur is not None else "").strip()
        if len(texte) > contrainte:
            raise ValueError(f"{cle} : texte trop long")
        if cle in ("voix_style", "ollama_modele") and not texte:
            raise ValueError(f"{cle} : valeur vide")
        if any(ord(c) < 32 for c in texte):
            raise ValueError(f"{cle} : caractères invalides")
        return texte
    if genre == "liste_url":
        if isinstance(valeur, str):
            valeur = valeur.splitlines()
        urls = []
        for ligne in valeur or []:
            ligne = str(ligne).strip()
            if not ligne:
                continue
            if not ligne.lower().startswith(("http://", "https://")):
                raise ValueError(f"adresse invalide : {ligne}")
            urls.append(ligne)
        if not urls:
            raise ValueError("au moins un flux est nécessaire")
        return urls[:contrainte]
    raise ValueError(f"type inconnu pour {cle}")
