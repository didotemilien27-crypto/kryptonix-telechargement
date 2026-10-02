"""
KRYPTONIX / noyau/systeme.py
=============================
Agilité système : télémétrie matérielle temps réel (psutil), ouverture de sites,
recherches web ciblées, et inventaire des processus.
"""

from __future__ import annotations

import datetime
import logging
import platform
import shutil
import threading
import time
import urllib.parse
import webbrowser

LOG = logging.getLogger("systeme")

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

# ------------------------------------------------------------------
#  Annuaire web : (accueil, gabarit de recherche)
# ------------------------------------------------------------------
SITES = {
    "youtube": ("https://www.youtube.com", "https://www.youtube.com/results?search_query={}"),
    "google": ("https://www.google.fr", "https://www.google.com/search?q={}"),
    "github": ("https://github.com", "https://github.com/search?q={}"),
    "claude": ("https://claude.ai", "https://claude.ai/new?q={}"),
    "wikipedia": ("https://fr.wikipedia.org", "https://fr.wikipedia.org/w/index.php?search={}"),
    "amazon": ("https://www.amazon.fr", "https://www.amazon.fr/s?k={}"),
    "leboncoin": ("https://www.leboncoin.fr", "https://www.leboncoin.fr/recherche?text={}"),
    "twitch": ("https://www.twitch.tv", "https://www.twitch.tv/search?term={}"),
    "reddit": ("https://www.reddit.com", "https://www.reddit.com/search/?q={}"),
    "maps": ("https://www.google.fr/maps", "https://www.google.com/maps/search/{}"),
    "gmail": ("https://mail.google.com", "https://mail.google.com/mail/u/0/#search/{}"),
    "chatgpt": ("https://chat.openai.com", "https://chat.openai.com/?q={}"),
    "ollama": ("https://ollama.com", "https://ollama.com/search?q={}"),
    "stackoverflow": ("https://stackoverflow.com", "https://stackoverflow.com/search?q={}"),
    "deepl": ("https://www.deepl.com/translator", "https://www.deepl.com/translator#fr/en/{}"),
}


class Systeme:
    """Capteur matériel + bras web de KRYPTONIX."""

    def __init__(self, config: dict, memoire=None):
        self.config = config
        self.memoire = memoire
        self.intervalle = float(config.get("telemetrie_intervalle", 2.0))
        self._derniere_mesure: dict = {}
        self._verrou = threading.Lock()
        self._actif = threading.Event()
        self._thread: threading.Thread | None = None
        if psutil is not None:
            psutil.cpu_percent(interval=None)  # amorce le compteur différentiel
        self.rafraichir()

    # ------------------------------------------------------------------
    #  Télémétrie
    # ------------------------------------------------------------------
    def rafraichir(self) -> dict:
        """Prend une mesure instantanée du matériel. Jamais bloquant."""
        mesure = {
            "horodatage": time.time(),
            "heure": datetime.datetime.now().strftime("%H:%M:%S"),
            "os": f"{platform.system()} {platform.release()}",
            "cpu": 0.0, "ram": 0.0, "ram_utilisee_go": 0.0, "ram_totale_go": 0.0,
            "disque": 0.0, "disque_libre_go": 0.0,
            "batterie": None, "temperature": None, "processus": 0,
            "uptime_h": 0.0, "coeurs": 0,
        }
        if psutil is None:
            mesure["os"] += " (psutil absent)"
            with self._verrou:
                self._derniere_mesure = mesure
            return mesure

        try:
            mesure["cpu"] = psutil.cpu_percent(interval=None)
            mesure["coeurs"] = psutil.cpu_count(logical=True) or 0

            memoire_vive = psutil.virtual_memory()
            mesure["ram"] = memoire_vive.percent
            mesure["ram_utilisee_go"] = round(memoire_vive.used / 1_073_741_824, 1)
            mesure["ram_totale_go"] = round(memoire_vive.total / 1_073_741_824, 1)

            racine = "C:\\" if platform.system() == "Windows" else "/"
            usage = shutil.disk_usage(racine)
            mesure["disque"] = round(usage.used / usage.total * 100, 1)
            mesure["disque_libre_go"] = round(usage.free / 1_073_741_824, 1)

            mesure["processus"] = len(psutil.pids())
            mesure["uptime_h"] = round((time.time() - psutil.boot_time()) / 3600, 1)

            if hasattr(psutil, "sensors_battery"):
                batterie = psutil.sensors_battery()
                if batterie is not None:
                    mesure["batterie"] = round(batterie.percent)
                    mesure["branchee"] = bool(batterie.power_plugged)

            if hasattr(psutil, "sensors_temperatures"):
                capteurs = psutil.sensors_temperatures() or {}
                for lectures in capteurs.values():
                    if lectures:
                        mesure["temperature"] = round(lectures[0].current)
                        break
        except Exception as erreur:  # un capteur absent ne doit rien casser
            LOG.debug("Télémétrie partielle : %s", erreur)

        with self._verrou:
            self._derniere_mesure = mesure
        return mesure

    @property
    def telemetrie(self) -> dict:
        with self._verrou:
            return dict(self._derniere_mesure)

    def resume_court(self) -> str:
        mesure = self.telemetrie or self.rafraichir()
        morceaux = [
            f"CPU {mesure['cpu']:.0f}%",
            f"RAM {mesure['ram']:.0f}% ({mesure['ram_utilisee_go']}/{mesure['ram_totale_go']} Go)",
            f"disque {mesure['disque']:.0f}% occupé",
            f"{mesure['processus']} processus",
        ]
        if mesure.get("batterie") is not None:
            morceaux.append(f"batterie {mesure['batterie']}%")
        if mesure.get("temperature") is not None:
            morceaux.append(f"{mesure['temperature']}°C")
        return ", ".join(morceaux)

    def diagnostic(self) -> str:
        """Bilan parlé, avec alertes si la machine souffre."""
        mesure = self.rafraichir()
        phrases = [
            f"Système {mesure['os']}, éveillé depuis {mesure['uptime_h']} heures. "
            f"Processeur à {mesure['cpu']:.0f}% sur {mesure['coeurs']} cœurs logiques, "
            f"mémoire à {mesure['ram']:.0f}%, disque occupé à {mesure['disque']:.0f}% "
            f"avec {mesure['disque_libre_go']} Go libres."
        ]
        if mesure["cpu"] > 85:
            phrases.append("Le processeur est saturé, quelque chose le dévore.")
        if mesure["ram"] > 88:
            phrases.append("La mémoire vive est au bord de l'asphyxie.")
        if mesure["disque"] > 92:
            phrases.append("Le disque est presque plein, il faudra faire le ménage.")
        if mesure.get("batterie") is not None and mesure["batterie"] < 20 \
                and not mesure.get("branchee"):
            phrases.append(f"Batterie à {mesure['batterie']}% et non branchée : je te préviens.")
        if len(phrases) == 1:
            phrases.append("Tout est nominal.")
        return " ".join(phrases)

    def composants(self) -> dict:
        """
        Inventaire matériel détaillé : nom exact du processeur, carte graphique,
        disques physiques, barrettes de RAM. Contrairement à la télémétrie
        (qui bouge en continu), ceci décrit *ce que la machine est*.
        """
        inventaire = {
            "processeur": platform.processor() or "inconnu",
            "architecture": platform.machine(),
            "systeme": f"{platform.system()} {platform.release()} ({platform.version()})",
            "cartes_graphiques": [],
            "disques": [],
            "ram_barrettes": [],
            "ram_totale_go": 0.0,
        }

        if psutil is not None:
            memoire = psutil.virtual_memory()
            inventaire["ram_totale_go"] = round(memoire.total / 1_073_741_824, 1)

        # --- Windows : WMI donne des informations bien plus riches ---
        if platform.system() == "Windows":
            try:
                import subprocess
                sortie = subprocess.run(
                    ["wmic", "cpu", "get", "name"], capture_output=True, text=True,
                    timeout=5, creationflags=0x08000000,
                ).stdout
                lignes = [l.strip() for l in sortie.splitlines() if l.strip() and "Name" not in l]
                if lignes:
                    inventaire["processeur"] = lignes[0]
            except Exception:
                pass

            try:
                sortie = subprocess.run(
                    ["wmic", "path", "win32_VideoController", "get", "name"],
                    capture_output=True, text=True, timeout=5, creationflags=0x08000000,
                ).stdout
                inventaire["cartes_graphiques"] = [
                    l.strip() for l in sortie.splitlines() if l.strip() and "Name" not in l
                ]
            except Exception:
                pass

            try:
                sortie = subprocess.run(
                    ["wmic", "diskdrive", "get", "model,size"],
                    capture_output=True, text=True, timeout=5, creationflags=0x08000000,
                ).stdout
                for ligne in sortie.splitlines()[1:]:
                    if ligne.strip():
                        inventaire["disques"].append(ligne.strip())
            except Exception:
                pass

        # --- Linux : lecture directe de /proc et lspci ---
        elif platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as fichier:
                    for ligne in fichier:
                        if "model name" in ligne:
                            inventaire["processeur"] = ligne.split(":", 1)[1].strip()
                            break
            except Exception:
                pass
            try:
                import subprocess
                sortie = subprocess.run(["lspci"], capture_output=True, text=True,
                                        timeout=5).stdout
                inventaire["cartes_graphiques"] = [
                    l.split(":", 2)[-1].strip() for l in sortie.splitlines()
                    if "VGA" in l or "3D controller" in l
                ]
            except Exception:
                pass

        if psutil is not None and hasattr(psutil, "disk_partitions"):
            try:
                for partition in psutil.disk_partitions():
                    if not inventaire["disques"]:  # évite le doublon si WMI a déjà répondu
                        usage = shutil.disk_usage(partition.mountpoint)
                        inventaire["disques"].append(
                            f"{partition.device} ({round(usage.total/1_073_741_824)} Go, "
                            f"{partition.fstype})"
                        )
            except Exception:
                pass

        return inventaire

    def composants_parle(self) -> str:
        """Version parlée / lisible de l'inventaire matériel."""
        inventaire = self.composants()
        phrases = [
            f"Processeur : {inventaire['processeur']}.",
            f"Mémoire vive : {inventaire['ram_totale_go']} Go.",
        ]
        if inventaire["cartes_graphiques"]:
            phrases.append("Carte graphique : " + ", ".join(inventaire["cartes_graphiques"]) + ".")
        if inventaire["disques"]:
            phrases.append("Stockage : " + " ; ".join(inventaire["disques"][:3]) + ".")
        phrases.append(f"Système : {inventaire['systeme']}.")
        return " ".join(phrases)

    def processus_gourmands(self, nombre: int = 5) -> list[dict]:
        if psutil is None:
            return []
        liste = []
        for processus in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                info = processus.info
                liste.append({
                    "nom": info.get("name") or "?",
                    "cpu": round(info.get("cpu_percent") or 0.0, 1),
                    "ram": round(info.get("memory_percent") or 0.0, 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        liste.sort(key=lambda p: (p["ram"], p["cpu"]), reverse=True)
        return liste[:nombre]

    # ------------------------------------------------------------------
    #  Surveillance continue (agent autonome)
    # ------------------------------------------------------------------
    def demarrer_surveillance(self, alerte=None) -> None:
        """Boucle de fond : mesure régulière + alerte unique en cas de surchauffe."""
        if self._thread and self._thread.is_alive():
            return
        self._actif.set()

        def boucle():
            deja_alerte = {"cpu": False, "ram": False, "disque": False}
            while self._actif.is_set():
                mesure = self.rafraichir()
                for cle, seuil, message in (
                    ("cpu", 92, "Le processeur tourne au maximum depuis un moment."),
                    ("ram", 92, "La mémoire vive est presque entièrement consommée."),
                    ("disque", 95, "Le disque est saturé, l'écriture va devenir instable."),
                ):
                    if mesure.get(cle, 0) >= seuil:
                        if not deja_alerte[cle]:
                            deja_alerte[cle] = True
                            if self.memoire:
                                self.memoire.journaliser("alerte_materielle", message)
                            if alerte:
                                alerte(message)
                    else:
                        deja_alerte[cle] = False
                time.sleep(self.intervalle)

        self._thread = threading.Thread(target=boucle, daemon=True, name="kryptonix-telemetrie")
        self._thread.start()
        LOG.info("Surveillance matérielle active (%.1f s).", self.intervalle)

    def arreter_surveillance(self) -> None:
        self._actif.clear()

    # ------------------------------------------------------------------
    #  Bras web
    # ------------------------------------------------------------------
    def ouvrir_site(self, cible: str, requete: str = "") -> str:
        """Ouvre un site connu (avec recherche si fournie) ou devine le domaine."""
        cible = cible.strip().lower().strip(".,;:!?")
        if not cible:
            return "Quel site, exactement ?"

        for cle, (accueil, gabarit) in SITES.items():
            if cle in cible:
                url = gabarit.format(urllib.parse.quote_plus(requete)) if requete else accueil
                webbrowser.open(url)
                return (f"{cle.capitalize()} ouvert sur « {requete} ». Lien : {url}" if requete
                        else f"{cle.capitalize()} est à l'écran. Lien : {url}")

        domaine = cible.replace(" ", "")
        if "." not in domaine:
            domaine += ".com"
        url = f"https://{domaine}"
        webbrowser.open(url)
        return f"J'ouvre {domaine}. Lien : {url}. Si ce n'est pas le bon, dis-le et je corrige."

    def rechercher(self, terme: str, moteur: str = "google") -> str:
        if not terme.strip():
            return "Chercher quoi ?"
        _accueil, gabarit = SITES.get(moteur, SITES["google"])
        url = gabarit.format(urllib.parse.quote_plus(terme))
        webbrowser.open(url)
        return f"Recherche lancée sur « {terme} ». Lien : {url}"
