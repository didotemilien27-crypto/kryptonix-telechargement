#!/usr/bin/env python3
"""
KRYPTONIX — point d'entrée de l'APPLICATION (double-clic, aucun terminal)
=========================================================================
C'est ce fichier que PyInstaller transforme en KRYPTONIX.exe.

Ce qui se passe quand l'utilisateur clique sur l'icône :
    1. Une fenêtre native s'ouvre tout de suite sur l'écran de préparation.
    2. En arrière-plan : installation silencieuse d'Ollama si besoin,
       démarrage du moteur, téléchargement du modèle (une seule fois).
    3. Dès que tout est prêt, la fenêtre bascule d'elle-même sur l'IA.
    4. Fermer la fenêtre arrête proprement l'application.

Pour le développement (avec terminal) : `python lancer.py` fonctionne toujours.
"""

import os
import sys

# ----------------------------------------------------------------------
#  0. Crochet du bac à sable : une fois packagé, la sandbox relance
#     l'application elle-même avec ce drapeau pour exécuter un script Python.
#     Doit rester AVANT tout import lourd.
# ----------------------------------------------------------------------
if len(sys.argv) >= 3 and sys.argv[1] == "--kx-python":
    import runpy
    _script = sys.argv[2]
    sys.argv = sys.argv[2:]
    runpy.run_path(_script, run_name="__main__")
    sys.exit(0)

import logging
import socket
import subprocess
import threading
import time
import webbrowser

# ----------------------------------------------------------------------
#  1. Application sans console : aucun sous-processus ne doit faire
#     clignoter une fenêtre noire (Windows).
# ----------------------------------------------------------------------
if os.name == "nt":
    _popen_init = subprocess.Popen.__init__

    def _popen_sans_fenetre(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_sans_fenetre

from noyau.config import (RACINE, DOSSIER_LOGS, DOSSIER_PROGRAMME, charger_config,
                          sauvegarder_config, initialiser_logs)
from noyau.version import VERSION

# Sans console, sys.stdout / sys.stderr valent None : on les envoie dans un
# fichier pour qu'un simple print() ne fasse jamais planter l'application.
if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
    _journal = open(os.path.join(DOSSIER_LOGS, "console.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _journal

initialiser_logs(logging.INFO)
LOG = logging.getLogger("app")

PORT_VERROU = 47653  # verrou « une seule instance » (local uniquement)


# ======================================================================
#  Utilitaires
# ======================================================================
def port_libre(prefere: int) -> int:
    """Retourne `prefere` s'il est libre, sinon un port libre quelconque."""
    for essai in (prefere, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", essai))
                return s.getsockname()[1]
            except OSError:
                continue
    return prefere


def prendre_verrou() -> socket.socket | None:
    """Une seule instance à la fois. Retourne le socket-verrou, ou None si déjà lancée."""
    verrou = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        verrou.bind(("127.0.0.1", PORT_VERROU))
        verrou.listen(1)
        return verrou
    except OSError:
        return None


def creer_mutex_windows() -> None:
    """Mutex nommé : permet à l'installateur de détecter que l'application tourne."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "KryptonixAppMutex")
    except Exception:
        pass


def webview2_present() -> bool:
    """Le composant d'affichage de Windows (WebView2) est-il installé ?"""
    if os.name != "nt":
        return True
    try:
        import winreg
        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for racine, chemin in (
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
        ):
            try:
                with winreg.OpenKey(racine, chemin) as cle:
                    version, _ = winreg.QueryValueEx(cle, "pv")
                    if version and version != "0.0.0.0":
                        return True
            except OSError:
                continue
    except Exception:
        return True  # dans le doute on ne bloque pas
    return False


def installer_webview2() -> None:
    """Télécharge et installe WebView2 en silence (petit composant Microsoft, gratuit)."""
    import tempfile
    import requests
    chemin = os.path.join(tempfile.gettempdir(), "MicrosoftEdgeWebview2Setup.exe")
    try:
        with requests.get("https://go.microsoft.com/fwlink/p/?LinkId=2124703",
                          stream=True, timeout=30, allow_redirects=True) as r:
            r.raise_for_status()
            with open(chemin, "wb") as f:
                for bloc in r.iter_content(chunk_size=262144):
                    f.write(bloc)
        subprocess.run([chemin, "/silent", "/install"], timeout=300)
    except Exception as erreur:
        LOG.warning("Installation de WebView2 impossible : %s", erreur)


# ======================================================================
#  État partagé avec l'écran de préparation
# ======================================================================
ETAT = {
    "etape": "demarrage", "pourcentage": 0, "message": "Démarrage…",
    "erreur": "", "pret": False, "url": "", "version": VERSION, "peut_continuer": False,
}
VERROU_ETAT = threading.Lock()
CONTEXTE: dict = {"kryptonix": None, "mise_a_jour": None, "arret": threading.Event(),
                  "en_cours": False}


def maj_etat(**champs) -> None:
    with VERROU_ETAT:
        ETAT.update(champs)


def suivi_preparation(info: dict) -> None:
    maj_etat(etape=info["etape"], pourcentage=info["pourcentage"], message=info["message"])


# ======================================================================
#  Préparation + démarrage de l'assistant (thread d'arrière-plan)
# ======================================================================
def demarrer_assistant(config: dict) -> None:
    """Crée le noyau, lance le dashboard, puis signale à la fenêtre qu'elle peut basculer."""
    from noyau.noyau import Kryptonix
    from interfaces.web import lancer_serveur

    config["web_hote"] = "127.0.0.1"
    config["web_port"] = port_libre(int(config.get("web_port", 5000)))
    config["hud_actif"] = False  # la fenêtre native remplace le HUD flottant

    maj_etat(etape="assistant", pourcentage=100, message="Réveil de Kryptonix…")
    kryptonix = Kryptonix(config)
    CONTEXTE["kryptonix"] = kryptonix
    lancer_serveur(kryptonix, en_arriere_plan=True)

    import requests
    url = f"http://127.0.0.1:{config['web_port']}"
    for _ in range(60):
        try:
            requests.get(url + "/api/battement", timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.5)

    maj_etat(pret=True, url=url, message="Prêt.")
    threading.Thread(target=lambda: kryptonix.voix.parler(f"{kryptonix.nom} en ligne."),
                     daemon=True).start()
    if config.get("ecoute_permanente"):
        kryptonix.demarrer_ecoute()


def cycle_preparation(continuer_sans_ia: bool = False) -> None:
    """Prépare Ollama + modèle, puis démarre l'assistant. Peut être relancé (bouton Réessayer)."""
    if CONTEXTE["en_cours"]:
        return
    CONTEXTE["en_cours"] = True
    try:
        from noyau import premier_lancement
        config = charger_config()
        maj_etat(erreur="", peut_continuer=False, pourcentage=0, message="Vérification…")

        if not continuer_sans_ia:
            rapport = premier_lancement.preparer(config, suivi=suivi_preparation)
            if rapport["erreur"]:
                maj_etat(erreur=rapport["erreur"], peut_continuer=True,
                         message="Une étape a échoué.")
                return
            if rapport["modele_ok"] and rapport["modele"] != config.get("ollama_modele"):
                config["ollama_modele"] = rapport["modele"]
                sauvegarder_config(config)

        demarrer_assistant(config)

        # Mise à jour : vérifiée en arrière-plan, installée à la fermeture.
        threading.Thread(target=verifier_mise_a_jour, args=(config,), daemon=True).start()
    except Exception as erreur:
        LOG.exception("Échec du démarrage")
        maj_etat(erreur=f"Erreur inattendue : {erreur}", peut_continuer=False)
    finally:
        CONTEXTE["en_cours"] = False


def verifier_mise_a_jour(config: dict) -> None:
    try:
        from noyau import mise_a_jour
        nouvelle = mise_a_jour.verifier(config)
        if nouvelle:
            LOG.info("Mise à jour %s disponible, téléchargement…", nouvelle["version"])
            chemin = mise_a_jour.telecharger(nouvelle["url"])
            if chemin:
                CONTEXTE["mise_a_jour"] = chemin
                LOG.info("Mise à jour prête : elle s'installera à la fermeture.")
    except Exception:
        LOG.exception("Vérification de mise à jour")


# ======================================================================
#  Serveur de l'écran de préparation
# ======================================================================
def serveur_preparation() -> int:
    """Petit serveur Flask local pour l'écran de préparation. Retourne son port."""
    from flask import Flask, jsonify, render_template

    appli = Flask(__name__, template_folder=os.path.join(DOSSIER_PROGRAMME, "interfaces", "templates"))

    @appli.route("/")
    def page():
        return render_template("preparation.html", version=VERSION)

    @appli.route("/api/etat")
    def etat():
        with VERROU_ETAT:
            return jsonify(dict(ETAT))

    @appli.route("/api/reessayer", methods=["POST"])
    def reessayer():
        threading.Thread(target=cycle_preparation, daemon=True).start()
        return jsonify({"ok": True})

    @appli.route("/api/continuer", methods=["POST"])
    def continuer():
        threading.Thread(target=cycle_preparation, kwargs={"continuer_sans_ia": True},
                         daemon=True).start()
        return jsonify({"ok": True})

    port = port_libre(0)
    threading.Thread(
        target=lambda: appli.run(host="127.0.0.1", port=port, debug=False,
                                 use_reloader=False, threaded=True),
        daemon=True, name="kryptonix-preparation").start()
    return port


# ======================================================================
#  Fermeture propre
# ======================================================================
def arreter_tout() -> None:
    kryptonix = CONTEXTE.get("kryptonix")
    try:
        if kryptonix:
            kryptonix.arreter()
    except Exception:
        LOG.exception("Arrêt du noyau")
    chemin = CONTEXTE.get("mise_a_jour")
    if chemin:
        try:
            from noyau import mise_a_jour
            mise_a_jour.installer_maintenant(chemin)
        except Exception:
            LOG.exception("Lancement de la mise à jour")
    logging.shutdown()
    os._exit(0)  # coupe aussi les threads Flask/voix restants


# ======================================================================
#  Programme principal
# ======================================================================
def principal() -> int:
    verrou = prendre_verrou()
    if verrou is None:
        # Déjà lancée : on ne fait rien de plus (la fenêtre existante reste au premier plan).
        return 0
    creer_mutex_windows()

    # Composant d'affichage de Windows : installé en silence s'il manque.
    if not webview2_present():
        installer_webview2()

    port_prepa = serveur_preparation()
    url_prepa = f"http://127.0.0.1:{port_prepa}/"
    threading.Thread(target=cycle_preparation, daemon=True, name="preparation").start()

    try:
        import webview
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True  # liens de téléchargement des documents générés
        except Exception:
            pass
        webview.create_window("KRYPTONIX", url_prepa, width=1320, height=860,
                              min_size=(980, 660), background_color="#06080d")
        webview.start(private_mode=False,
                      storage_path=os.path.join(RACINE, "webview"))
    except Exception:
        # Dernier recours : navigateur par défaut. Fermer l'onglet arrête l'application.
        LOG.exception("Fenêtre native indisponible, bascule sur le navigateur")
        webbrowser.open(url_prepa)
        mode_navigateur()
    arreter_tout()
    return 0


def mode_navigateur() -> None:
    """Sans fenêtre native : on reste actif tant que la page envoie son signal de vie."""
    from interfaces.web import DERNIER_BATTEMENT
    debut_attendu = time.time() + 600  # laisse le temps au premier téléchargement
    while True:
        time.sleep(3)
        if DERNIER_BATTEMENT[0] and time.time() - DERNIER_BATTEMENT[0] > 45:
            return
        if not DERNIER_BATTEMENT[0] and time.time() > debut_attendu and not ETAT["pret"]:
            continue


if __name__ == "__main__":
    sys.exit(principal())
