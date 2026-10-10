"""
KRYPTONIX / noyau/extensions/obs.py
====================================
Contrôle d'OBS Studio via obs-websocket v5 (intégré à OBS 28+ : Outils → Paramètres du serveur WebSocket).
Direct, enregistrements, scènes, tampon de replay, état en direct.

Réglages : obs_hote (127.0.0.1), obs_port (4455), obs_mot_de_passe.
Aller en direct et l'arrêter ont un impact public : niveau 2 (confirmation vocale).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import uuid

from .base import ErreurModule, Module, Outil, liste_parlee, normaliser
from .ws_mini import ClientWS, ErreurWS

LOG = logging.getLogger("obs")


def authentification(mot_de_passe: str, sel: str, defi: str) -> str:
    secret = base64.b64encode(hashlib.sha256((mot_de_passe + sel).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + defi).encode()).digest()).decode()


class ClientOBS:
    """Connexion courte, ouverte à la demande (robuste aux redémarrages d'OBS)."""

    def __init__(self, config: dict):
        self.config = config
        self.ws: ClientWS | None = None
        self._verrou = threading.Lock()

    def _ouvrir(self) -> None:
        ws = ClientWS(self.config.get("obs_hote", "127.0.0.1"), int(self.config.get("obs_port", 4455)),
                      "/", "obswebsocket.json", timeout=4.0)
        try:
            ws.connecter()
            hello = json.loads(ws.recevoir(4))
            ident = {"rpcVersion": 1, "eventSubscriptions": 0}
            auth = hello.get("d", {}).get("authentication")
            if auth:
                mdp = self.config.get("obs_mot_de_passe", "")
                if not mdp:
                    raise ErreurModule("OBS demande un mot de passe (obs_mot_de_passe)")
                ident["authentication"] = authentification(mdp, auth["salt"], auth["challenge"])
            ws.envoyer(json.dumps({"op": 1, "d": ident}))
            etape = "identification"
            rep = json.loads(ws.recevoir(4))
            if rep.get("op") != 2:
                raise ErreurModule("OBS a refusé l'identification (mot de passe incorrect ?)")
        except ErreurWS:
            ws.fermer()
            if locals().get("etape") == "identification":      # OBS coupe la connexion quand le mot de passe est faux
                raise ErreurModule("OBS a refusé l'identification (mot de passe incorrect ?)") from None
            raise ErreurModule("OBS ne répond pas : est-il ouvert, avec le serveur WebSocket activé") from None
        except (ValueError, KeyError):
            ws.fermer()
            raise ErreurModule("réponse inattendue d'OBS") from None
        self.ws = ws

    def requete(self, type_: str, donnees: dict | None = None) -> dict:
        with self._verrou:
            for essai in (1, 2):
                if self.ws is None:
                    self._ouvrir()
                rid = uuid.uuid4().hex[:8]
                try:
                    self.ws.envoyer(json.dumps({"op": 6, "d": {"requestType": type_, "requestId": rid,
                                                                "requestData": donnees or {}}}))
                    while True:
                        m = json.loads(self.ws.recevoir(6))
                        if m.get("op") == 7 and m["d"].get("requestId") == rid:
                            break
                except (ErreurWS, ValueError):
                    self.fermer()
                    if essai == 2:
                        raise ErreurModule("la connexion à OBS a été perdue") from None
                    continue
                statut = m["d"].get("requestStatus", {})
                if not statut.get("result"):
                    raise ErreurModule(statut.get("comment") or f"OBS a refusé « {type_} » (code {statut.get('code')})")
                return m["d"].get("responseData") or {}
        return {}

    def fermer(self) -> None:
        if self.ws:
            self.ws.fermer()
            self.ws = None


class OBS(Module):
    nom = "obs"
    description = "Direct, enregistrement, scènes et replays OBS"
    exemples = ("lance le direct", "arrête l'enregistrement", "passe à la scène jeu", "sauvegarde le replay",
                "quelles sont mes scènes")

    def __init__(self, kx):
        super().__init__(kx)
        self.client = ClientOBS(self.config)

    def arreter(self) -> None:
        self.client.fermer()

    # -- actions ----------------------------------------------------------
    def demarrer_direct(self) -> str:
        self.client.requete("StartStream")
        self.kx.extensions.sons.jouer("direct_on_air")
        return "Tu es en direct."

    def arreter_direct(self) -> str:
        self.client.requete("StopStream")
        self.kx.extensions.sons.jouer("direct_fin")
        return "Le direct est terminé."

    def demarrer_enregistrement(self) -> str:
        self.client.requete("StartRecord")
        self.kx.extensions.sons.jouer("enregistrement_debut")
        return "Enregistrement lancé."

    def arreter_enregistrement(self) -> str:
        d = self.client.requete("StopRecord")
        self.kx.extensions.sons.jouer("enregistrement_fin")
        chemin = d.get("outputPath", "")
        return "Enregistrement terminé" + (f" : {chemin.replace(chr(92), '/').split('/')[-1]}." if chemin else ".")

    def scenes(self) -> tuple[list[str], str]:
        d = self.client.requete("GetSceneList")
        noms = [s["sceneName"] for s in sorted(d.get("scenes", []), key=lambda s: -s.get("sceneIndex", 0))]
        return noms, d.get("currentProgramSceneName", "")

    def changer_scene(self, nom: str) -> str:
        noms, _ = self.scenes()
        cible = next((s for s in noms if normaliser(s) == normaliser(nom)), None) or \
            next((s for s in noms if normaliser(nom) in normaliser(s)), None)
        if not cible:
            raise ErreurModule(f"je ne trouve pas la scène « {nom} » (scènes : {liste_parlee(noms)})")
        self.client.requete("SetCurrentProgramScene", {"sceneName": cible})
        self.kx.extensions.sons.jouer("scene_lumiere")
        return f"Scène « {cible} » à l'antenne."

    def replay(self, action: str) -> str:
        if action == "demarrer":
            self.client.requete("StartReplayBuffer")
            return "Tampon de replay activé."
        if action == "arreter":
            self.client.requete("StopReplayBuffer")
            return "Tampon de replay arrêté."
        self.client.requete("SaveReplayBuffer")
        self.kx.extensions.sons.jouer("capture_ecran")
        return "Replay sauvegardé."

    def etat(self) -> dict:
        s = self.client.requete("GetStreamStatus")
        r = self.client.requete("GetRecordStatus")
        try:
            sc = self.client.requete("GetSceneList").get("currentProgramSceneName", "")
        except ErreurModule:
            sc = ""
        return {"direct": bool(s.get("outputActive")), "duree_direct_s": round(s.get("outputDuration", 0) / 1000),
                "enregistre": bool(r.get("outputActive")), "scene": sc}

    def etat_parle(self) -> str:
        e = self.etat()
        morceaux = [("Tu es en direct depuis " + f"{e['duree_direct_s'] // 60} minutes" if e["direct"] else "Pas de direct en cours"),
                    ("l'enregistrement tourne" if e["enregistre"] else "pas d'enregistrement")]
        if e["scene"]:
            morceaux.append(f"scène actuelle : {e['scene']}")
        return ", ".join(morceaux) + "."

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if not re.search(r"\b(obs|direct|stream|streaming|live|enregistrement|enregistre|replay|scenes?)\b", c):
            return None
        try:
            if re.search(r"\b(lance|demarre|commence|start|passe en|va en)\b.*\b(direct|stream|streaming|live)\b", c):
                return self.securise("obs_direct_demarrer", {}, description="lancer le direct")
            if re.search(r"\b(arrete|stoppe|coupe|termine|stop)\b.*\b(direct|stream|streaming|live)\b", c):
                return self.securise("obs_direct_arreter", {}, description="arrêter le direct")
            if re.search(r"\b(lance|demarre|commence|start)\b.*\b(enregistrement|record)\b|\benregistre (?:l'ecran|ca|la scene)\b", c):
                return self.securise("obs_enregistrer", {})
            if re.search(r"\b(arrete|stoppe|termine|stop)\b.*\b(enregistrement|record)\b", c):
                return self.securise("obs_enregistrer_stop", {})
            if re.search(r"\b(sauvegarde|sauve|enregistre|garde|clip)\b.*\breplay\b|\breplay\b.*\b(sauvegarde|sauve|clip)\b", c):
                return self.replay("sauver")
            if re.search(r"\b(active|lance|demarre)\b.*\breplay\b", c):
                return self.replay("demarrer")
            if re.search(r"\b(desactive|arrete|coupe)\b.*\breplay\b", c):
                return self.replay("arreter")
            m = re.search(r"\b(?:passe|bascule|mets?|va|change)\b.*\bscene\b\s*(?:sur |a |de |:)?\s*(.+)", c) or \
                re.search(r"\bscene\s+(.+)", c)
            if m and not re.search(r"\b(quelles|liste)\b", c):
                return self.changer_scene(m.group(1).strip(" ."))
            if re.search(r"\b(quelles|liste|mes) scenes\b", c):
                noms, courante = self.scenes()
                return f"{len(noms)} scène(s) : {liste_parlee(noms)}. Actuellement : {courante}."
            if re.search(r"\b(etat|statut|ou en est)\b.*\b(obs|direct|stream|live)\b|\bsuis[- ]je en direct\b|\best[- ]ce que (?:je suis|ca) (?:en direct|enregistre)", c):
                return self.etat_parle()
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"OBS : {e}."
        return None

    def statut(self) -> dict:
        try:
            return {"connecte": True, **self.etat()}
        except ErreurModule as e:
            return {"connecte": False, "erreur": str(e)}

    def outils(self) -> list[Outil]:
        return [
            Outil("obs_direct_demarrer", "Démarrer le direct OBS", lambda a: self.demarrer_direct(), {}, (), 2, True, "obs"),
            Outil("obs_direct_arreter", "Arrêter le direct OBS", lambda a: self.arreter_direct(), {}, (), 2, True, "obs"),
            Outil("obs_enregistrer", "Démarrer l'enregistrement", lambda a: self.demarrer_enregistrement(), {}, (), 1, True, "obs"),
            Outil("obs_enregistrer_stop", "Arrêter l'enregistrement", lambda a: self.arreter_enregistrement(), {}, (), 1, True, "obs"),
            Outil("obs_scene", "Changer de scène OBS", lambda a: self.changer_scene(a["nom"]),
                  {"nom": {"type": "string"}}, ("nom",), 1, True, "obs"),
            Outil("obs_replay", "Sauvegarder le replay", lambda a: self.replay("sauver"), {}, (), 1, True, "obs"),
            Outil("obs_etat", "État du direct et de l'enregistrement", lambda a: self.etat_parle(), {}, (), 1, True, "obs"),
        ]
