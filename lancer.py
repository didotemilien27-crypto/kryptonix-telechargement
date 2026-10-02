#!/usr/bin/env python3
"""
KRYPTONIX — script maître de lancement
=======================================

    python lancer.py                 # HUD + dashboard web + écoute si micro
    python lancer.py --sans-hud      # dashboard web seul (serveur/headless)
    python lancer.py --sans-web      # HUD seul
    python lancer.py --console       # mode terminal pur, zéro interface graphique
    python lancer.py --muet          # désactive la synthèse vocale
    python lancer.py --ecoute        # force l'écoute vocale au démarrage
    python lancer.py --modele llama3 # surcharge le modèle Ollama
    python lancer.py --aspirer C:\\Ultron   # ingère un dossier puis continue
    python lancer.py --diagnostic    # vérifie l'installation et quitte
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser

from noyau.config import charger_config, initialiser_logs, MODELES_LEGERS, MODELES_LOURDS
from noyau.noyau import Kryptonix
from noyau import premier_lancement

BANNIERE = r"""
 _  __ ____  _   _ ____ _____ ___  _   _ ___ __  __
| |/ /|  _ \| | | |  _ \_   _/ _ \| \ | |_ _|  \/  |
| ' / | |_) | | | | |_) || || | | |  \| || || |\/| |
| . \ |  _ <| |_| |  __/ | || |_| | |\  || || |  | |
|_|\_\|_| \_\\___/|_|    |_| \___/|_| \_|___|_|  |_|
      intelligence locale · souveraine · hors ligne
"""


def _lister_modeles_ollama(config: dict) -> list[str]:
    """Interroge Ollama pour savoir quels modèles sont déjà téléchargés."""
    try:
        import requests
        reponse = requests.get(f"{config['ollama_hote'].rstrip('/')}/api/tags", timeout=5)
        return [m.get("name", "") for m in reponse.json().get("models", [])]
    except Exception:
        return []


# ======================================================================
#  Diagnostic d'installation
# ======================================================================
def diagnostic() -> int:
    """Vérifie chaque dépendance et l'accès à Ollama. Retourne un code de sortie."""
    print(BANNIERE)
    print("Diagnostic de l'installation\n" + "-" * 46)

    obligatoires = {
        "requests": "réseau / Ollama",
        "flask": "dashboard web",
    }
    optionnelles = {
        "customtkinter": "HUD flottant",
        "psutil": "télémétrie matérielle",
        "pypdf": "lecture des PDF",
        "speech_recognition": "écoute vocale",
        "pyttsx3": "voix hors ligne",
        "gtts": "voix en ligne",
        "pygame": "lecture audio (gTTS)",
        "sympy": "calcul symbolique",
    }

    manquantes = []
    for module, role in {**obligatoires, **optionnelles}.items():
        try:
            __import__(module)
            print(f"  [ok]      {module:<20} {role}")
        except ImportError:
            marque = "MANQUE" if module in obligatoires else "absent"
            print(f"  [{marque}]  {module:<20} {role}")
            if module in obligatoires:
                manquantes.append(module)

    print("-" * 46)
    config = charger_config()
    try:
        import requests
        reponse = requests.get(f"{config['ollama_hote'].rstrip('/')}/api/tags", timeout=5)
        modeles = [m.get("name") for m in reponse.json().get("models", [])]
        print(f"  Ollama    : en ligne sur {config['ollama_hote']}")
        print(f"  Modèles   : {', '.join(modeles) if modeles else 'aucun (ollama pull mistral)'}")
        if config["ollama_modele"].split(":")[0] not in {m.split(":")[0] for m in modeles}:
            print(f"  ATTENTION : le modèle « {config['ollama_modele']} » n'est pas installé.")
            print(f"              Lance : ollama pull {config['ollama_modele']}")
    except Exception:
        print(f"  Ollama    : INJOIGNABLE sur {config['ollama_hote']}")
        print("              Ouvre un terminal et lance : ollama serve")

    try:
        import speech_recognition as sr
        micros = sr.Microphone.list_microphone_names()
        print(f"  Micro     : {len(micros)} périphérique(s) détecté(s)")
    except Exception:
        print("  Micro     : indisponible (mode texte uniquement)")

    print("-" * 46)
    if manquantes:
        print("Dépendances critiques absentes : " + ", ".join(manquantes))
        print("Corrige avec : pip install -r requirements.txt")
        return 1
    print("Tout est en place. Lance : python lancer.py")
    return 0


# ======================================================================
#  Lancement
# ======================================================================
def principal() -> int:
    analyseur = argparse.ArgumentParser(
        description="KRYPTONIX — assistant local, souverain et gratuit.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyseur.add_argument("--sans-hud", action="store_true", help="ne pas ouvrir le HUD flottant")
    analyseur.add_argument("--sans-web", action="store_true", help="ne pas démarrer le dashboard")
    analyseur.add_argument("--console", action="store_true", help="mode terminal uniquement")
    analyseur.add_argument("--muet", action="store_true", help="désactiver la synthèse vocale")
    analyseur.add_argument("--ecoute", action="store_true", help="activer l'écoute au démarrage")
    analyseur.add_argument("--modele", help="modèle Ollama à utiliser (ex: llama3)")
    analyseur.add_argument("--port", type=int, help="port du dashboard web")
    analyseur.add_argument("--aspirer", metavar="DOSSIER", help="ingérer un dossier au démarrage")
    analyseur.add_argument("--diagnostic", action="store_true", help="vérifier l'installation")
    analyseur.add_argument("--leger", action="store_true",
                           help="mode économe : petit modèle, moins de RAM/CPU, "
                                "pour PC portable ou machine modeste")
    analyseur.add_argument("--verbeux", action="store_true", help="journalisation détaillée")
    arguments = analyseur.parse_args()

    if arguments.diagnostic:
        return diagnostic()

    initialiser_logs(logging.DEBUG if arguments.verbeux else logging.INFO)
    print(BANNIERE)

    # --- Configuration, avec surcharges de la ligne de commande ---
    config = charger_config()
    if arguments.modele:
        config["ollama_modele"] = arguments.modele
    if arguments.muet:
        config["voix_active"] = False
    if arguments.port:
        config["web_port"] = arguments.port

    if arguments.leger:
        config["mode_leger"] = True
        # On ne remplace le modèle que si l'utilisateur n'a pas choisi explicitement
        # le sien, et seulement s'il tourne sur un modèle connu pour être gourmand.
        if not arguments.modele and config["ollama_modele"] in MODELES_LOURDS:
            modeles_disponibles = {m for m in _lister_modeles_ollama(config)}
            nouveau_modele = next(
                (m for m in MODELES_LEGERS if m.split(":")[0] in
                 {n.split(":")[0] for n in modeles_disponibles}),
                MODELES_LEGERS[0],
            )
            print(f"  Mode léger : {config['ollama_modele']} -> {nouveau_modele}")
            if nouveau_modele.split(":")[0] not in {n.split(":")[0] for n in modeles_disponibles}:
                print(f"              (pas encore installé : ollama pull {nouveau_modele})")
            config["ollama_modele"] = nouveau_modele
        config["num_ctx"] = min(config.get("num_ctx", 4096), 2048)
        config["contexte_max_messages"] = min(config.get("contexte_max_messages", 12), 6)
        config["ollama_timeout"] = min(config.get("ollama_timeout", 120), 60)
        config["telemetrie_intervalle"] = max(config.get("telemetrie_intervalle", 2.0), 5.0)
        config["moteur_tts"] = "pyttsx3" if config.get("voix_active", True) else "muet"
        config["ecoute_permanente"] = False
        print("  Mode léger activé : contexte réduit, télémétrie espacée, "
              "voix hors ligne uniquement.\n")

    # --- Préparation automatique (installe/démarre Ollama, télécharge le
    #     modèle si c'est la toute première fois) : invisible pour un
    #     utilisateur qui a simplement double-cliqué sur l'application. ---
    print("  Préparation en cours (une seule fois, ensuite ce sera instantané)...")
    rapport_preparation = premier_lancement.preparer(config)
    if rapport_preparation["erreur"]:
        print(f"  [!] {rapport_preparation['erreur']}")
    elif rapport_preparation["action"] != "aucune":
        print(f"  OK — {rapport_preparation['action']} terminé(e).\n")

    # --- Éveil du noyau ---
    kryptonix = Kryptonix(config)

    # config["hud_actif"]=False (ex: via --leger) vaut comme --sans-hud, sauf si
    # l'utilisateur redemande explicitement le HUD sur la ligne de commande.
    sans_hud = arguments.sans_hud or not config.get("hud_actif", True)
    if sans_hud and not arguments.sans_hud:
        print("  HUD       : désactivé automatiquement (mode léger / hud_actif=false)")

    if not kryptonix.cerveau.disponible:
        print("\n  Ollama ne répond pas. KRYPTONIX fonctionne mais ne pourra pas réfléchir.")
        print("  Ouvre un second terminal et lance : ollama serve")
        print(f"  Puis, si besoin : ollama pull {config['ollama_modele']}\n")

    if arguments.aspirer:
        print(f"  Ingestion de {arguments.aspirer}...")
        bilan = kryptonix.documents.aspirer_dossier(arguments.aspirer, resumer=False)
        print(f"  {bilan.get('analyses', 0)} fichier(s) ingéré(s), "
              f"{bilan.get('ignores', 0)} ignoré(s).\n")

    # --- Dashboard web ---
    if not arguments.sans_web and not arguments.console:
        from interfaces.web import lancer_serveur
        lancer_serveur(kryptonix, en_arriere_plan=True)
        url = f"http://{config['web_hote']}:{config['web_port']}"
        print(f"  Dashboard : {url}")
        if not sans_hud:
            webbrowser.open(url)

    # --- Écoute vocale ---
    if arguments.ecoute or config.get("ecoute_permanente"):
        if kryptonix.demarrer_ecoute():
            print(f"  Micro     : actif — dis « {config['mot_reveil']} » pour l'appeler")
        else:
            print("  Micro     : indisponible, mode texte")

    # --- Interface principale ---
    try:
        if arguments.console or (sans_hud and arguments.sans_web):
            boucle_console(kryptonix)
        elif sans_hud:
            print("  HUD       : désactivé. Ctrl+C pour arrêter.\n")
            kryptonix.voix.parler(f"{kryptonix.nom} en ligne.")
            while True:
                time.sleep(1)
        else:
            from interfaces.hud import lancer_hud
            print("  HUD       : ouvert (Échap pour replier, ✕ pour quitter)\n")
            kryptonix.voix.parler(f"{kryptonix.nom} en ligne.")
            lancer_hud(kryptonix)  # bloquant : thread principal, comme l'exige Tkinter
    except KeyboardInterrupt:
        print("\n  Interruption clavier.")
    except ImportError as erreur:
        print(f"\n  Interface graphique indisponible ({erreur}). Bascule en mode console.")
        boucle_console(kryptonix)
    finally:
        kryptonix.arreter()
        print("  KRYPTONIX hors ligne.")
    return 0


def boucle_console(kryptonix) -> None:
    """Mode terminal : utile sur serveur, en SSH, ou si Tkinter manque."""
    print("  Mode console. « quitte » pour sortir, « micro » pour l'écoute vocale.\n")
    while True:
        try:
            texte = input("  ❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not texte:
            continue
        if texte.lower() in ("quitte", "quitter", "exit", "sortie"):
            return
        if texte.lower() == "micro":
            actif = kryptonix.basculer_ecoute()
            print(f"  [écoute {'active' if actif else 'arrêtée'}]")
            continue
        reponse = kryptonix.traiter(texte, source="console")
        print(f"  {reponse}\n")


if __name__ == "__main__":
    sys.exit(principal())
