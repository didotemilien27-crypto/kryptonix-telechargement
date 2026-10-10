"""
KRYPTONIX / noyau/extensions/pc_controle.py
============================================
Contrôle du PC : applications, médias, volume, stats système en direct (CPU, RAM, GPU, disque,
réseau), verrouillage de session.

Windows : touches média natives (aucune installation) ; volume exact si `pycaw` est présent.
Linux/macOS : pactl/amixer, playerctl, osascript quand ils existent.
Les applications peuvent être déclarées dans `applications` = {"nom": "chemin ou commande"}.
Fermer une application est une action de niveau 2.
"""

from __future__ import annotations

import glob
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time

from .base import ErreurModule, Module, Outil, liste_parlee, normaliser

LOG = logging.getLogger("pc")
OS = platform.system()

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

APPS_WINDOWS = {
    "bloc-notes": "notepad", "notepad": "notepad", "calculatrice": "calc", "paint": "mspaint",
    "explorateur": "explorer", "explorateur de fichiers": "explorer", "gestionnaire des taches": "taskmgr",
    "terminal": "wt", "invite de commandes": "cmd", "powershell": "powershell", "parametres": "ms-settings:",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "chrome": "chrome", "navigateur": "chrome", "edge": "msedge", "firefox": "firefox",
    "spotify": "spotify", "discord": "discord", "steam": "steam", "obs": "obs64", "vlc": "vlc",
    "capture": "snippingtool", "outil capture": "snippingtool",
}
APPS_LINUX = {"calculatrice": "gnome-calculator", "terminal": "x-terminal-emulator", "navigateur": "xdg-open https://",
              "fichiers": "nautilus", "explorateur": "nautilus", "firefox": "firefox", "chrome": "google-chrome",
              "spotify": "spotify", "discord": "discord", "vlc": "vlc", "obs": "obs", "steam": "steam"}
APPS_MAC = {"calculatrice": "Calculator", "navigateur": "Safari", "safari": "Safari", "chrome": "Google Chrome",
            "spotify": "Spotify", "discord": "Discord", "terminal": "Terminal", "notes": "Notes", "vlc": "VLC"}
PROCESSUS = {"chrome": "chrome", "navigateur": "chrome", "edge": "msedge", "firefox": "firefox", "spotify": "spotify",
             "discord": "discord", "steam": "steam", "obs": "obs64", "vlc": "vlc", "word": "winword",
             "excel": "excel", "bloc-notes": "notepad", "calculatrice": "calculator"}

# Touches média Windows
VK = {"volume_haut": 0xAF, "volume_bas": 0xAE, "muet": 0xAD, "suivant": 0xB0, "precedent": 0xB1,
      "stop": 0xB2, "play_pause": 0xB3}


def appuyer_touche_media(nom: str, fois: int = 1) -> None:
    import ctypes
    for _ in range(fois):
        ctypes.windll.user32.keybd_event(VK[nom], 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK[nom], 0, 2, 0)
        time.sleep(0.01)


def _lancer(cmd, shell=False) -> None:
    subprocess.Popen(cmd, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def raccourcis_menu_demarrer() -> list[str]:
    if OS != "Windows":
        return []
    racines = [os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
               os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs")]
    trouves = []
    for r in racines:
        trouves += glob.glob(os.path.join(r, "**", "*.lnk"), recursive=True)
    return trouves


def gpu() -> dict | None:
    """Stats GPU NVIDIA via nvidia-smi (livré avec le pilote). None si absent."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout.strip()
        if not out:
            return None
        nom, usage, temp, mem_u, mem_t = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"nom": nom, "usage": float(usage), "temperature": float(temp),
                "vram_utilisee_mo": float(mem_u), "vram_totale_mo": float(mem_t)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class PCControle(Module):
    nom = "pc"
    description = "Applications, médias, volume, stats système (GPU/CPU/RAM)"
    exemples = ("lance la calculatrice", "ouvre l'application spotify", "monte le volume", "mets le volume à 30",
                "pause", "morceau suivant", "utilisation du GPU", "ferme spotify", "verrouille le PC")

    def __init__(self, kx):
        super().__init__(kx)
        self.simulation = bool(self.config.get("simulation_systeme", False))
        self.journal: list[str] = []

    # -- applications -----------------------------------------------------
    def _table_apps(self) -> dict[str, str]:
        base = APPS_WINDOWS if OS == "Windows" else APPS_MAC if OS == "Darwin" else APPS_LINUX
        table = {normaliser(k): v for k, v in base.items()}
        table.update({normaliser(k): v for k, v in (self.config.get("applications") or {}).items()})
        return table

    def trouver_application(self, nom: str) -> tuple[str, str] | None:
        n = normaliser(nom).strip()
        table = self._table_apps()
        if n in table:
            return n, table[n]
        for k, v in table.items():
            if n and (n in k or k in n) and len(k) > 3:
                return k, v
        for lnk in raccourcis_menu_demarrer():
            base = normaliser(os.path.splitext(os.path.basename(lnk))[0])
            if n and (n == base or n in base):
                return base, lnk
        return None

    def lancer_application(self, nom: str) -> str:
        trouve = self.trouver_application(nom)
        if not trouve:
            raise ErreurModule(f"je ne connais pas l'application « {nom} » (ajoute-la dans le réglage `applications`)")
        libelle, cible = trouve
        self.journal.append(f"lancer:{libelle}")
        if self.simulation:
            return f"(simulation) {libelle} lancé."
        try:
            if OS == "Windows":
                if cible.lower().endswith(".lnk") or ":" in cible and not os.path.isabs(cible):
                    os.startfile(cible)                      # raccourci ou protocole (ms-settings:)
                else:
                    _lancer(f'start "" "{cible}"' if os.path.isabs(cible) else f"start {cible}", shell=True)
            elif OS == "Darwin":
                _lancer(["open", "-a", cible])
            else:
                _lancer(cible.split())
        except OSError:
            raise ErreurModule(f"impossible de lancer {libelle}") from None
        self.kx.extensions.sons.jouer("ouverture_app")
        return f"{libelle.capitalize()} lancé."

    def fermer_application(self, nom: str) -> str:
        if psutil is None:
            raise ErreurModule("psutil est absent")
        cle = PROCESSUS.get(normaliser(nom), normaliser(nom)).lower()
        cle = cle[:-4] if cle.endswith(".exe") else cle
        protege = {"explorer", "system", "csrss", "winlogon", "svchost", "python", "kryptonix", "systemd", "init"}
        if cle in protege or len(cle) < 3:
            raise ErreurModule("je ne ferme pas ce processus système")
        n = 0
        for p in psutil.process_iter(["name"]):
            nom_p = (p.info.get("name") or "").lower()
            nom_p = nom_p[:-4] if nom_p.endswith(".exe") else nom_p
            if nom_p == cle or (len(cle) > 4 and cle in nom_p):
                if self.simulation:
                    n += 1
                    continue
                try:
                    p.terminate()
                    n += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        if not n:
            raise ErreurModule(f"{nom} ne semble pas ouvert")
        return f"{nom.capitalize()} fermé."

    # -- médias / volume --------------------------------------------------
    def media(self, action: str) -> str:
        libelle = {"play_pause": "lecture / pause", "suivant": "morceau suivant", "precedent": "morceau précédent",
                   "stop": "arrêt", "muet": "son coupé / rétabli"}[action]
        self.journal.append(f"media:{action}")
        if self.simulation:
            return f"(simulation) {libelle}."
        try:
            if OS == "Windows":
                appuyer_touche_media(action)
            elif OS == "Darwin":
                code = {"play_pause": "playpause", "suivant": "next track", "precedent": "previous track",
                        "stop": "pause", "muet": ""}[action]
                if action == "muet":
                    _lancer(["osascript", "-e", "set volume with output muted"])
                else:
                    _lancer(["osascript", "-e", f'tell application "Spotify" to {code}'])
            else:
                if action == "muet":
                    _lancer(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
                else:
                    _lancer(["playerctl", {"play_pause": "play-pause", "suivant": "next", "precedent": "previous", "stop": "stop"}[action]])
        except (OSError, KeyError):
            raise ErreurModule("la commande multimédia n'est pas disponible sur ce système") from None
        self.kx.extensions.sons.jouer("clic")
        return libelle.capitalize() + "."

    def volume(self, valeur: int | None = None, delta: int = 0) -> str:
        self.journal.append(f"volume:{valeur if valeur is not None else delta}")
        if self.simulation:
            return f"(simulation) volume {valeur if valeur is not None else delta:+}".replace("+", "") if valeur is None else f"(simulation) volume à {valeur} %."
        try:
            if OS == "Windows":
                try:
                    from ctypes import POINTER, cast
                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                    ep = AudioUtilities.GetSpeakers().Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    vol = cast(ep, POINTER(IAudioEndpointVolume))
                    courant = vol.GetMasterVolumeLevelScalar() * 100
                    cible = valeur if valeur is not None else courant + delta
                    cible = max(0, min(100, cible))
                    vol.SetMasterVolumeLevelScalar(cible / 100, None)
                    self.kx.extensions.sons.jouer("volume")
                    return f"Volume à {round(cible)} pour cent."
                except ImportError:
                    pass
                if valeur is not None:                          # sans pycaw : 50 pas = 100 %, on repart de zéro
                    appuyer_touche_media("volume_bas", 50)
                    appuyer_touche_media("volume_haut", round(valeur / 2))
                    self.kx.extensions.sons.jouer("volume")
                    return f"Volume à environ {valeur} pour cent."
                appuyer_touche_media("volume_haut" if delta > 0 else "volume_bas", max(1, abs(delta) // 2))
                self.kx.extensions.sons.jouer("volume")
                return "Volume monté." if delta > 0 else "Volume baissé."
            if OS == "Darwin":
                if valeur is not None:
                    _lancer(["osascript", "-e", f"set volume output volume {max(0, min(100, valeur))}"])
                else:
                    _lancer(["osascript", "-e", f"set volume output volume ((output volume of (get volume settings)) + {delta})"])
            else:
                if valeur is not None:
                    _lancer(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(150, valeur))}%"])
                else:
                    _lancer(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{delta:+d}%"])
        except OSError:
            raise ErreurModule("je n'arrive pas à régler le volume ici") from None
        self.kx.extensions.sons.jouer("volume")
        return f"Volume à {valeur} pour cent." if valeur is not None else ("Volume monté." if delta > 0 else "Volume baissé.")

    def verrouiller(self) -> str:
        if self.simulation:
            return "(simulation) session verrouillée."
        try:
            if OS == "Windows":
                import ctypes
                ctypes.windll.user32.LockWorkStation()
            elif OS == "Darwin":
                _lancer(["pmset", "displaysleepnow"])
            else:
                _lancer(["loginctl", "lock-session"])
        except OSError:
            raise ErreurModule("verrouillage impossible ici") from None
        return "Session verrouillée."

    # -- stats ------------------------------------------------------------
    def stats(self) -> dict:
        d: dict = {"os": OS}
        if psutil:
            d["cpu"] = psutil.cpu_percent(interval=0.3)
            m = psutil.virtual_memory()
            d["ram"] = m.percent
            d["ram_utilisee_go"] = round(m.used / 1e9, 1)
            d["ram_totale_go"] = round(m.total / 1e9, 1)
            try:
                d["disque"] = psutil.disk_usage(os.path.abspath(os.sep)).percent
            except OSError:
                pass
            try:
                t = psutil.sensors_temperatures()
                if t:
                    d["cpu_temperature"] = round(next(iter(t.values()))[0].current)
            except (AttributeError, IndexError, OSError):
                pass
        d["gpu"] = gpu()
        return d

    def stats_parlees(self, focus: str = "tout") -> str:
        s = self.stats()
        g = s.get("gpu")
        if focus == "gpu":
            if not g:
                return "Je ne détecte pas de carte NVIDIA (nvidia-smi absent) : je ne peux pas lire l'usage du GPU."
            return (f"{g['nom']} : {g['usage']:.0f} pour cent d'utilisation, {g['temperature']:.0f} degrés, "
                    f"{g['vram_utilisee_mo'] / 1024:.1f} sur {g['vram_totale_mo'] / 1024:.1f} gigaoctets de mémoire vidéo.")
        morceaux = []
        if "cpu" in s:
            morceaux.append(f"processeur à {s['cpu']:.0f} pour cent")
        if "ram" in s:
            morceaux.append(f"mémoire à {s['ram']:.0f} pour cent ({s['ram_utilisee_go']} sur {s['ram_totale_go']} gigaoctets)")
        if g:
            morceaux.append(f"GPU à {g['usage']:.0f} pour cent, {g['temperature']:.0f} degrés")
        if "disque" in s:
            morceaux.append(f"disque rempli à {s['disque']:.0f} pour cent")
        return ("Maintenant : " + liste_parlee(morceaux) + ".") if morceaux else "psutil est absent, je ne peux pas lire les stats."

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        try:
            if re.search(r"\b(gpu|carte graphique|vram)\b", c) and re.search(r"\b(utilisation|usage|charge|temperature|chauffe|combien|stats?|taux|%)\b", c):
                return self.stats_parlees("gpu")
            if re.search(r"\bstats? (?:systeme|du pc|en direct)\b|\bmonitoring\b|\bcomment va le pc\b", c):
                return self.stats_parlees()
            if re.search(r"\bverrouille\b.*\b(pc|ordinateur|session|ecran)\b|\bverrouille la session\b", c):
                return self.securise("pc_verrouiller", {}, description="verrouiller la session")
            if re.search(r"\b(mets? en pause|pause|reprends?|lecture|play|joue)\b", c) and not re.search(r"\b(minuteur|chrono|enregistrement|direct)\b", c) \
                    and re.fullmatch(r"(?:\w+ ){0,3}(?:pause|play|lecture|la musique|le morceau|la video|la chanson|la lecture)(?: \w+){0,2}", c):
                return self.media("play_pause")
            if re.search(r"\b(morceau|chanson|piste|titre) suivant(?:e)?\b|\bpasse (?:au|le) (?:suivant|morceau suivant)\b|\bsuivant\b$", c):
                return self.media("suivant")
            if re.search(r"\b(morceau|chanson|piste|titre) precedent(?:e)?\b|\brevenir au morceau precedent\b|\bprecedent\b$", c):
                return self.media("precedent")
            if re.search(r"\b(coupe le son|mute|mets? en sourdine|reactive le son|remets? le son)\b|\bmuet\b", c) and "sons" not in c:
                return self.media("muet")
            m = re.search(r"\bvolume\b.*?\b(?:a|au|sur|de)\s*(\d{1,3})\b", c) or re.search(r"\b(\d{1,3})\s*(?:%|pour ?cent)\b.*\bvolume\b", c)
            if m and "volume" in c:
                return self.volume(valeur=int(m.group(1)))
            if re.search(r"\b(monte|augmente|plus fort|hausse)\b.*\b(volume|son)\b|\bvolume (?:plus )?(?:fort|haut)\b", c):
                return self.volume(delta=+10)
            if re.search(r"\b(baisse|diminue|moins fort|reduis)\b.*\b(volume|son)\b|\bvolume (?:moins|plus bas)\b", c):
                return self.volume(delta=-10)
            m = re.search(r"\b(?:ferme|quitte|arrete|tue)\s+(?:l'application |le logiciel |le programme |l'appli )?([a-z0-9' .-]{3,30})$", c)
            if m and self.trouver_application(m.group(1)) and not re.search(r"\b(onglet|fenetre|direct|enregistrement|minuteur)\b", c):
                return self.securise("pc_fermer_app", {"nom": m.group(1).strip()}, description=f"fermer {m.group(1).strip()}")
            m = re.search(r"\b(?:ouvre|lance|demarre|execute)\s+(?:l'application |l'appli |le logiciel |le programme |la |le |l')?([a-z0-9' .-]{3,40})$", c)
            if m:
                explicite = bool(re.search(r"\b(application|appli|logiciel|programme)\b", c))
                nom = m.group(1).strip()
                if explicite or (self.trouver_application(nom) and normaliser(nom) not in {"youtube", "google", "twitch", "reddit", "github"}):
                    return self.lancer_application(nom)
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"PC : {e}."
        return None

    def statut(self) -> dict:
        return {"os": OS, "gpu": bool(gpu()) if shutil.which("nvidia-smi") else False, "simulation": self.simulation}

    def outils(self) -> list[Outil]:
        return [
            Outil("pc_lancer_app", "Lancer une application", lambda a: self.lancer_application(a["nom"]),
                  {"nom": {"type": "string"}}, ("nom",), 1, True, "pc"),
            Outil("pc_fermer_app", "Fermer une application", lambda a: self.fermer_application(a["nom"]),
                  {"nom": {"type": "string"}}, ("nom",), 2, True, "pc"),
            Outil("pc_volume", "Régler le volume (0-100)", lambda a: self.volume(valeur=int(a["valeur"])),
                  {"valeur": {"type": "integer", "minimum": 0, "maximum": 100}}, ("valeur",), 1, True, "pc"),
            Outil("pc_media", "Contrôle média (play_pause, suivant, precedent, stop, muet)",
                  lambda a: self.media(a["action"]), {"action": {"type": "string", "enum": ["play_pause", "suivant", "precedent", "stop", "muet"]}},
                  ("action",), 1, True, "pc"),
            Outil("pc_stats", "Stats système : CPU, RAM, GPU", lambda a: self.stats_parlees(), {}, (), 1, True, "pc"),
            Outil("pc_verrouiller", "Verrouiller la session", lambda a: self.verrouiller(), {}, (), 2, True, "pc"),
        ]
