"""
KRYPTONIX / noyau/extensions/domotique.py
==========================================
Domotique et alimentation du PC.

  • Philips Hue (pont local, API v1) : allumer, éteindre, intensité, couleurs, scènes, ambiances.
  • Détection de présence : ping du téléphone → scènes automatiques au départ / au retour.
  • PC : extinction / redémarrage / veille (N3 : confirmation stricte + délai annulable),
    réveil à distance Wake-on-LAN.

Association au pont (une seule fois) : appuie sur le bouton du pont, puis dis
« associe le pont Hue » (config : `hue_pont` = adresse IP du pont).
"""

from __future__ import annotations

import logging
import platform
import re
import socket
import subprocess
import threading
import time

from .base import ErreurModule, Module, Outil, http, liste_parlee, normaliser

LOG = logging.getLogger("domotique")

COULEURS = {   # nom → (teinte Hue 0-65535, saturation 0-254)
    "rouge": (0, 254), "orange": (5500, 254), "jaune": (12750, 254), "vert": (25500, 254),
    "turquoise": (33000, 220), "cyan": (36000, 254), "bleu": (46920, 254), "violet": (50000, 254),
    "mauve": (52000, 200), "rose": (58000, 170), "magenta": (54500, 254),
}
TEMPERATURES = {"blanc": 250, "blanc chaud": 400, "chaud": 400, "blanc froid": 160, "froid": 160,
                "neutre": 300, "blanc neutre": 300}
LIBELLES_AMBIANCE = {"detente": "détente", "cinema": "cinéma", "energie": "énergie", "fete": "fête", "reveil": "réveil"}
AMBIANCES = {  # nom → état Hue
    "detente": {"on": True, "bri": 120, "ct": 400},
    "lecture": {"on": True, "bri": 230, "ct": 300},
    "cinema": {"on": True, "bri": 40, "hue": 8000, "sat": 220},
    "nuit": {"on": True, "bri": 8, "ct": 450},
    "travail": {"on": True, "bri": 254, "ct": 220},
    "energie": {"on": True, "bri": 254, "ct": 200},
    "romantique": {"on": True, "bri": 60, "hue": 58000, "sat": 200},
    "fete": {"on": True, "bri": 254, "effect": "colorloop", "sat": 254},
    "reveil": {"on": True, "bri": 200, "ct": 350},
}


# ---------------------------------------------------------------------------
#  Pont Hue
# ---------------------------------------------------------------------------
class Hue:
    def __init__(self, config: dict):
        self.config = config

    @property
    def pont(self) -> str:
        return str(self.config.get("hue_pont", "")).strip().replace("http://", "").rstrip("/")

    @property
    def cle(self) -> str:
        return str(self.config.get("hue_cle", "")).strip()

    def configure(self) -> bool:
        return bool(self.pont and self.cle)

    def _url(self, chemin: str) -> str:
        if not self.pont:
            raise ErreurModule("le pont Hue n'est pas configuré (hue_pont)")
        if not self.cle:
            raise ErreurModule("le pont Hue n'est pas encore associé : appuie sur son bouton puis "
                               "dis « associe le pont Hue »")
        return f"http://{self.pont}/api/{self.cle}{chemin}"

    def _appel(self, methode: str, chemin: str, json=None):
        r = http(methode, self._url(chemin), json=json, timeout=6)
        try:
            data = r.json()
        except ValueError:
            raise ErreurModule("réponse illisible du pont Hue") from None
        if isinstance(data, list) and data and "error" in data[0]:
            erreur = data[0]["error"]
            if erreur.get("type") == 1:
                raise ErreurModule("l'association avec le pont Hue a expiré, refais « associe le pont Hue »")
            raise ErreurModule(f"le pont Hue refuse : {erreur.get('description', 'erreur')}")
        return data

    def associer(self) -> str:
        if not self.pont:
            raise ErreurModule("indique d'abord l'adresse du pont dans hue_pont")
        r = http("POST", f"http://{self.pont}/api", json={"devicetype": "kryptonix#pc"}, timeout=6)
        data = r.json()
        if data and "success" in data[0]:
            return data[0]["success"]["username"]
        if data and "error" in data[0] and data[0]["error"].get("type") == 101:
            raise ErreurModule("appuie sur le gros bouton du pont Hue, puis redemande dans les 30 secondes")
        raise ErreurModule("association refusée par le pont")

    def lumieres(self) -> dict:
        return self._appel("GET", "/lights")

    def groupes(self, ttl: float = 30.0) -> dict:
        import time as _t
        cache = getattr(self, "_cache_groupes", None)
        if cache and _t.time() - cache[0] < ttl:
            return cache[1]
        donnees = self._appel("GET", "/groups")
        self._cache_groupes = (_t.time(), donnees)
        return donnees

    def scenes(self) -> dict:
        return self._appel("GET", "/scenes")

    def groupe_action(self, groupe: str, etat: dict) -> None:
        self._appel("PUT", f"/groups/{groupe}/action", etat)

    def lumiere_etat(self, lumiere: str, etat: dict) -> None:
        self._appel("PUT", f"/lights/{lumiere}/state", etat)

    def activer_scene(self, scene_id: str, groupe: str = "0") -> None:
        self._appel("PUT", f"/groups/{groupe}/action", {"scene": scene_id})


# ---------------------------------------------------------------------------
#  Présence (ping du téléphone)
# ---------------------------------------------------------------------------
def ping(hote: str, delai_ms: int = 1500) -> bool:
    """True si l'hôte répond. Aucune dépendance : appelle la commande système."""
    if not re.fullmatch(r"[A-Za-z0-9_.\-:]+", hote or ""):
        return False
    if platform.system() == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(delai_ms), hote]
        drapeaux = 0x08000000  # CREATE_NO_WINDOW
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, delai_ms // 1000)), hote]
        drapeaux = 0
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=delai_ms / 1000 + 3, creationflags=drapeaux)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Presence:
    """Surveille un ou plusieurs téléphones ; hystérésis pour ne pas confondre veille Wi-Fi et départ."""

    def __init__(self, hotes: list[str], au_changement, sonde=ping, intervalle=20.0, echecs_max=4):
        self.hotes = hotes
        self.au_changement = au_changement
        self.sonde = sonde
        self.intervalle = intervalle
        self.echecs_max = echecs_max
        self.present: bool | None = None
        self._echecs = 0
        self._arret = threading.Event()
        self._thread: threading.Thread | None = None

    def un_tour(self) -> None:
        vu = any(self.sonde(h) for h in self.hotes)
        if vu:
            self._echecs = 0
            if self.present is not True:
                ancien, self.present = self.present, True
                if ancien is False:                  # un vrai retour (pas le premier relevé)
                    self.au_changement(True)
        else:
            self._echecs += 1
            if self._echecs >= self.echecs_max and self.present is not False:
                ancien, self.present = self.present, False
                if ancien is True:
                    self.au_changement(False)

    def demarrer(self) -> None:
        if self._thread or not self.hotes:
            return

        def boucle():
            while not self._arret.is_set():
                try:
                    self.un_tour()
                except Exception as e:
                    LOG.debug("présence : %s", e)
                self._arret.wait(self.intervalle)
        self._thread = threading.Thread(target=boucle, daemon=True, name="kryptonix-presence")
        self._thread.start()

    def arreter(self) -> None:
        self._arret.set()


# ---------------------------------------------------------------------------
#  Wake-on-LAN
# ---------------------------------------------------------------------------
def paquet_magique(mac: str) -> bytes:
    propre = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(propre) != 12:
        raise ErreurModule("adresse MAC invalide (attendu : AA:BB:CC:DD:EE:FF)")
    return b"\xff" * 6 + bytes.fromhex(propre) * 16


def envoyer_wol(mac: str, diffusion: str = "255.255.255.255", port: int = 9) -> None:
    paquet = paquet_magique(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(paquet, (diffusion, port))


# ---------------------------------------------------------------------------
#  Module
# ---------------------------------------------------------------------------
class Domotique(Module):
    nom = "domotique"
    description = "Hue, présence, extinction et réveil du PC"
    exemples = ("allume le salon", "mets les lumières en bleu à 30 %", "ambiance cinéma",
                "éteins le PC", "réveille le PC du bureau")

    def __init__(self, kx):
        super().__init__(kx)
        self.hue = Hue(self.config)
        self.presence: Presence | None = None
        self.simulation = bool(self.config.get("simulation_systeme", False))
        self.dernieres_commandes: list[str] = []

    # -- résolution -------------------------------------------------------
    def _groupe_cible(self, c: str) -> tuple[str, str]:
        """(id, nom) du groupe cité dans la phrase ; ('0', 'toute la maison') par défaut."""
        try:
            groupes = self.hue.groupes()
        except ErreurModule:
            raise
        meilleur = None
        for gid, g in groupes.items():
            nom = normaliser(g.get("name", ""))
            if nom and re.search(rf"\b{re.escape(nom)}\b", c):
                if meilleur is None or len(nom) > len(normaliser(meilleur[1])):
                    meilleur = (gid, g["name"])
        if meilleur is None and getattr(self.kx.extensions, "contexte_piece", ""):
            piece = self.kx.extensions.contexte_piece          # commande venue d'un satellite : on agit dans cette pièce
            for gid, g in groupes.items():
                if piece and piece in normaliser(g.get("name", "")):
                    return gid, g["name"]
        return meilleur or ("0", "toute la maison")

    @staticmethod
    def _pourcentage(c: str) -> int | None:
        m = re.search(r"(\d{1,3})\s*(?:%|pour ?cent)", c) or re.search(r"\b(?:a|au) (\d{1,3})\b", c)
        if m:
            return max(0, min(100, int(m.group(1))))
        return None

    # -- actions ----------------------------------------------------------
    def allumer(self, groupe: str = "0", bri: int | None = None) -> str:
        etat = {"on": True}
        if bri is not None:
            etat["bri"] = max(1, round(bri * 254 / 100))
        self.hue.groupe_action(groupe, etat)
        self.kx.extensions.sons.jouer("lumiere_on")
        return "Allumé."

    def eteindre(self, groupe: str = "0") -> str:
        self.hue.groupe_action(groupe, {"on": False})
        self.kx.extensions.sons.jouer("lumiere_off")
        return "Éteint."

    def regler(self, groupe: str, **etat) -> None:
        etat.setdefault("on", True)
        self.hue.groupe_action(groupe, etat)

    def ambiance(self, nom_ambiance: str, groupe: str = "0") -> str:
        cle = normaliser(nom_ambiance)
        # 1) une scène portant ce nom existe sur le pont → on l'utilise
        try:
            for sid, sc in self.hue.scenes().items():
                if normaliser(sc.get("name", "")) == cle:
                    self.hue.activer_scene(sid, sc.get("group", groupe) or groupe)
                    self.kx.extensions.sons.jouer("scene_lumiere")
                    return f"Scène « {sc['name']} » activée."
        except ErreurModule:
            raise
        # 2) sinon une ambiance prédéfinie
        if cle in AMBIANCES:
            self.hue.groupe_action(groupe, dict(AMBIANCES[cle]))
            self.kx.extensions.sons.jouer("scene_lumiere")
            return f"Ambiance {LIBELLES_AMBIANCE.get(cle, cle)} installée."
        raise ErreurModule(f"je ne connais pas l'ambiance « {nom_ambiance} » "
                           f"(essaie : {liste_parlee(list(AMBIANCES))})")

    # -- présence ---------------------------------------------------------
    def _hotes_presence(self) -> list[str]:
        hotes = self.config.get("presence_telephones") or self.config.get("presence_hote") or []
        if isinstance(hotes, str):
            hotes = [hotes]
        return [h["hote"] if isinstance(h, dict) else str(h) for h in hotes if h]

    def demarrer(self) -> None:
        hotes = self._hotes_presence()
        if self.config.get("presence_actif", False) and hotes and self.presence is None:
            self.presence = Presence(hotes, self._presence_change,
                                     intervalle=float(self.config.get("presence_intervalle", 20)),
                                     echecs_max=int(self.config.get("presence_echecs", 4)))
            self.presence.demarrer()

    def arreter(self) -> None:
        if self.presence:
            self.presence.arreter()

    def _presence_change(self, present: bool) -> None:
        cle = "presence_scene_retour" if present else "presence_scene_depart"
        scene = self.config.get(cle, "detente" if present else "eteindre")
        sons = self.kx.extensions.sons
        sons.jouer("presence_retour" if present else "presence_depart")
        self.kx.memoire.journaliser("presence", "retour" if present else "départ")
        try:
            if normaliser(str(scene)) in ("eteindre", "off", ""):
                self.eteindre("0")
            else:
                self.ambiance(str(scene), "0")
        except ErreurModule as e:
            LOG.warning("présence : scène non appliquée (%s)", e)
        if present and self.config.get("presence_salutation", True):
            self.kx.voix.parler(f"Bon retour, {self.config.get('nom_utilisateur', '')}.")

    # -- alimentation du PC ----------------------------------------------
    def _commande_alimentation(self, action: str) -> list[str]:
        os_ = platform.system()
        if os_ == "Windows":
            return {"eteindre": ["shutdown", "/s", "/t", "5"], "redemarrer": ["shutdown", "/r", "/t", "5"],
                    "veille": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]}[action]
        if os_ == "Darwin":
            return {"eteindre": ["osascript", "-e", 'tell app "System Events" to shut down'],
                    "redemarrer": ["osascript", "-e", 'tell app "System Events" to restart'],
                    "veille": ["pmset", "sleepnow"]}[action]
        return {"eteindre": ["systemctl", "poweroff"], "redemarrer": ["systemctl", "reboot"],
                "veille": ["systemctl", "suspend"]}[action]

    def alimentation(self, action: str) -> str:
        libelle = {"eteindre": "extinction", "redemarrer": "redémarrage", "veille": "mise en veille"}[action]
        if self.simulation:
            self.dernieres_commandes.append(action)
            return f"(simulation) {libelle} du PC."
        try:
            subprocess.Popen(self._commande_alimentation(action), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ErreurModule(f"la commande système a échoué ({e.__class__.__name__})") from None
        return f"{libelle.capitalize()} en cours."

    def reveiller(self, nom: str | None = None) -> str:
        machines = self.config.get("wol_machines") or {}
        if not machines:
            raise ErreurModule("aucune machine configurée pour le réveil (wol_machines : nom → adresse MAC)")
        if nom:
            cible = next((n for n in machines if normaliser(n) == normaliser(nom)
                          or normaliser(nom) in normaliser(n)), None)
            if not cible:
                raise ErreurModule(f"je ne connais pas la machine « {nom} » ({liste_parlee(list(machines))})")
        elif len(machines) == 1:
            cible = next(iter(machines))
        else:
            raise ErreurModule(f"quelle machine ? {liste_parlee(list(machines))}")
        envoyer_wol(machines[cible], self.config.get("wol_diffusion", "255.255.255.255"),
                    int(self.config.get("wol_port", 9)))
        return f"Signal de réveil envoyé à {cible}."

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)

        # PC : alimentation (toujours sensible)
        if re.search(r"\b(eteins|eteindre|arrete|coupe|shutdown)\b.*\b(le |mon |l')?(pc|ordinateur|machine|systeme)\b", c):
            return self.securise("pc_eteindre", {}, description="éteindre l'ordinateur")
        if re.search(r"\b(redemarre|reboot|relance)\b.*\b(le |mon |l')?(pc|ordinateur|machine)\b", c):
            return self.securise("pc_redemarrer", {}, description="redémarrer l'ordinateur")
        if re.search(r"\b(mets?|passe)\b.*\b(pc|ordinateur)\b.*\bveille\b|\bmise en veille\b", c):
            return self.securise("pc_veille", {}, description="mettre l'ordinateur en veille")
        m = re.search(r"\b(?:reveille|allume|demarre|wake)\b (?:le |la |mon |ma |l')?(?:pc|ordinateur|serveur|machine)?\s*(?:du |de la |de l'|de |d')?(.*)", c)
        if m and re.search(r"\b(pc|ordinateur|serveur|machine)\b", c) and not re.search(r"\b(lumiere|lampe|ampoule)", c):
            nom = m.group(1).strip() or None
            return self.securise("pc_reveiller", {"nom": nom}, description="réveiller une machine")

        if re.search(r"\b(associe|apparie|connecte|configure) (?:le )?pont hue\b", c):
            return self._associer()

        # Présence
        if re.search(r"\b(suis[- ]je|est[- ]ce que je suis) (?:a la maison|chez moi)\b|\betat de la presence\b", c):
            if not self.presence or self.presence.present is None:
                return "La détection de présence n'est pas active ou n'a pas encore de mesure."
            return "Je te vois à la maison." if self.presence.present else "Ton téléphone est hors du réseau : tu es sorti."

        # Hue
        mots_lumiere = r"\b(lumieres?|lampes?|ampoules?|eclairage|luminosite|ambiance|hue)\b"
        if re.search(r"\bscenes?\b", c) and self.hue.configure() and not re.search(r"\b(obs|direct|stream|live)\b", c):
            try:                      # « scène » = scène Hue seulement si une scène du pont porte ce nom
                if any(normaliser(sc.get("name", "")) in c for sc in self.hue.scenes().values()):
                    return self._lumieres(c)
            except ErreurModule:
                pass
        couleur = re.search(r"\b(" + "|".join(sorted(COULEURS, key=len, reverse=True)) + r")\b", c)
        if re.search(mots_lumiere, c) or (couleur and re.search(r"\b(mets?|passe|change)\b", c) and "lumi" in c):
            return self._lumieres(c)
        if re.search(r"\b(eteins|allume) tout\b", c):
            return self._lumieres(c + " lumieres")
        # « allume le salon », « éteins la cuisine » : un nom de pièce Hue suffit
        if re.search(r"\b(allume|rallume|eteins)\b", c) and self.hue.configure():
            try:
                noms = [normaliser(g.get("name", "")) for g in self.hue.groupes().values()]
            except ErreurModule:
                return None
            if any(n and re.search(rf"\b{re.escape(n)}\b", c) for n in noms):
                return self._lumieres(c)
        return None

    def _associer(self) -> str:
        try:
            cle = self.hue.associer()
        except ErreurModule as e:
            return f"Association impossible : {e}."
        self.kx.recharger_config({"hue_cle": cle})
        self.kx.extensions.sons.jouer("succes")
        return "Pont Hue associé. Je peux piloter tes lumières."

    def _lumieres(self, c: str) -> str:
        if not self.hue.pont:
            return ("Je ne connais pas ton pont Hue. Renseigne son adresse IP dans le panneau "
                    "(hue_pont), puis dis « associe le pont Hue ».")
        try:
            gid, gnom = self._groupe_cible(c)
            pct = self._pourcentage(c)
            m = re.search(r"\b(?:ambiance|scene)s? (?:de |du |d')?([a-z0-9 ]+?)(?: (?:dans|du|de la|au|a) .*)?$", c)
            if m and not re.search(r"\b(eteins|allume)\b", c):
                amb = m.group(1).strip()
                for connu in list(AMBIANCES):
                    if connu in amb:
                        amb = connu
                        break
                return self.ambiance(amb, gid)
            for nom in AMBIANCES:
                if re.search(rf"\b{nom}\b", c) and not re.search(r"\b(eteins|allume)\b", c):
                    return self.ambiance(nom, gid)
            couleur = re.search(r"\b(" + "|".join(sorted(COULEURS, key=len, reverse=True)) + r")\b", c)
            temp = next((t for t in sorted(TEMPERATURES, key=len, reverse=True) if re.search(rf"\b{t}\b", c)), None)
            if re.search(r"\b(eteins|eteindre|coupe|ferme)\b", c):
                self.eteindre(gid)
                return f"{gnom.capitalize()} : éteint." if gid != "0" else "Tout est éteint."
            if couleur:
                teinte, sat = COULEURS[couleur.group(1)]
                etat = {"hue": teinte, "sat": sat}
                if pct is not None:
                    etat["bri"] = max(1, round(pct * 254 / 100))
                self.regler(gid, **etat)
                self.kx.extensions.sons.jouer("scene_lumiere")
                return f"{gnom.capitalize()} en {couleur.group(1)}."
            if temp:
                self.regler(gid, ct=TEMPERATURES[temp])
                return f"{gnom.capitalize()} en {temp}."
            if re.search(r"\b(baisse|diminue|reduis|moins)\b", c) and pct is None:
                self.hue.groupe_action(gid, {"bri_inc": -60})
                return "J'ai baissé la luminosité."
            if re.search(r"\b(monte|augmente|plus de|plus fort|eclaircis)\b", c) and pct is None:
                self.hue.groupe_action(gid, {"bri_inc": 60})
                return "J'ai augmenté la luminosité."
            if pct is not None:
                if pct == 0:
                    self.eteindre(gid)
                    return "Éteint."
                self.allumer(gid, pct)
                return f"{gnom.capitalize()} à {pct} pour cent."
            if re.search(r"\b(allume|allumer|rallume|ouvre)\b", c):
                self.allumer(gid)
                return f"{gnom.capitalize()} : allumé." if gid != "0" else "Tout est allumé."
            if re.search(r"\b(quelles|liste|combien)\b", c):
                lum = self.hue.lumieres()
                return f"{len(lum)} lumière(s) : " + liste_parlee([v.get("name", "?") for v in lum.values()]) + "."
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Lumières : {e}."
        return "Dis-moi quoi faire des lumières : allumer, éteindre, une couleur, une intensité ou une ambiance."

    # -- statut / outils --------------------------------------------------
    def statut(self) -> dict:
        return {"hue_configure": self.hue.configure(), "pont": self.hue.pont,
                "presence": (None if not self.presence else self.presence.present),
                "wol": list((self.config.get("wol_machines") or {}).keys()), "simulation": self.simulation}

    def outils(self) -> list[Outil]:
        def lum_on(a):
            g = a.get("groupe", "0")
            return self.allumer(g, a.get("luminosite"))
        return [
            Outil("lumieres_allumer", "Allumer les lumières Hue", lum_on,
                  {"groupe": {"type": "string"}, "luminosite": {"type": "integer", "minimum": 1, "maximum": 100}},
                  (), 1, True, "domotique"),
            Outil("lumieres_eteindre", "Éteindre les lumières Hue",
                  lambda a: self.eteindre(a.get("groupe", "0")), {"groupe": {"type": "string"}}, (), 1, True, "domotique"),
            Outil("lumieres_ambiance", "Appliquer une ambiance ou une scène Hue",
                  lambda a: self.ambiance(a.get("nom", "detente"), a.get("groupe", "0")),
                  {"nom": {"type": "string"}, "groupe": {"type": "string"}}, ("nom",), 1, True, "domotique"),
            Outil("pc_eteindre", "Éteindre le PC", lambda a: self.alimentation("eteindre"), {}, (), 3, True, "pc"),
            Outil("pc_redemarrer", "Redémarrer le PC", lambda a: self.alimentation("redemarrer"), {}, (), 3, True, "pc"),
            Outil("pc_veille", "Mettre le PC en veille", lambda a: self.alimentation("veille"), {}, (), 2, True, "pc"),
            Outil("pc_reveiller", "Réveiller une machine (Wake-on-LAN)",
                  lambda a: self.reveiller(a.get("nom")), {"nom": {"type": "string"}}, (), 2, True, "pc"),
        ]
