"""
KRYPTONIX / noyau/extensions/social.py
=======================================
Réseaux sociaux.

  • Discord (bot) : écoute des mentions (alerte sonore + voix) et récapitulatif des messages du jour.
  • Instagram (API Graph, comptes professionnels) : abonnés et vues des vidéos comparés à la
    veille, multi-comptes.

Réglages Discord  : discord_token (jeton du bot), discord_salons = [identifiants de salons],
                    discord_utilisateur_id (ton identifiant, pour repérer tes mentions),
                    discord_ecoute (true = surveille les mentions en arrière-plan).
Réglages Instagram: instagram_comptes = [{"nom": "perso", "id": "<id compte pro>", "jeton": "<jeton>"}].

Les messages Discord sont résumés par le cerveau LOCAL (confidentialité).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, http, liste_parlee, normaliser
from .stockage import Stockage

LOG = logging.getLogger("social")
DISCORD_EPOCH = 1420070400000

SCHEMA = """
CREATE TABLE IF NOT EXISTS instagram_snap (
    compte TEXT NOT NULL, jour TEXT NOT NULL, abonnes INTEGER, vues INTEGER, publications INTEGER,
    PRIMARY KEY (compte, jour)
);
"""


def snowflake_depuis(ts: float) -> str:
    return str((int(ts * 1000) - DISCORD_EPOCH) << 22)


class Social(Module):
    nom = "social"
    description = "Discord (mentions, récap) et Instagram (abonnés, vues)"
    exemples = ("résume discord aujourd'hui", "ai-je été mentionné sur discord", "combien d'abonnés sur instagram",
                "stats instagram de perso")

    def __init__(self, kx):
        super().__init__(kx)
        self.db = Stockage(os.path.join(DOSSIER_DONNEES, "extensions.db"), SCHEMA)
        self._dernier_vu: dict[str, str] = {}
        self._arret = threading.Event()
        self._thread: threading.Thread | None = None
        self._dernier_snap_jour = ""

    # ======================================================================
    #  Discord
    # ======================================================================
    @property
    def _api_discord(self) -> str:
        return (self.config.get("discord_api_base") or "https://discord.com/api/v10").rstrip("/")

    def discord_configure(self) -> bool:
        return bool(self.config.get("discord_token") and self.config.get("discord_salons"))

    def _discord(self, chemin: str, params: dict | None = None):
        r = http("GET", self._api_discord + chemin, params=params, timeout=12,
                 headers={"Authorization": f"Bot {self.config.get('discord_token', '')}"})
        if r.status_code == 401:
            raise ErreurModule("le jeton du bot Discord est refusé")
        if r.status_code == 403:
            raise ErreurModule("le bot Discord n'a pas accès à ce salon")
        if r.status_code == 429:
            raise ErreurModule("Discord limite les requêtes, réessaie dans un instant")
        if r.status_code >= 400:
            raise ErreurModule(f"Discord a répondu {r.status_code}")
        return r.json()

    def messages_du_jour(self) -> list[dict]:
        if not self.discord_configure():
            raise ErreurModule("Discord n'est pas configuré (discord_token et discord_salons)")
        minuit = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        apres = snowflake_depuis(minuit)
        sortie = []
        for salon in self.config["discord_salons"]:
            nom_salon = str(salon)
            try:
                nom_salon = self._discord(f"/channels/{salon}").get("name", nom_salon)
            except ErreurModule:
                pass
            msgs = self._discord(f"/channels/{salon}/messages", {"limit": 100, "after": apres})
            for m in msgs:
                sortie.append({"salon": nom_salon, "auteur": m.get("author", {}).get("global_name") or m.get("author", {}).get("username", "?"),
                               "texte": m.get("content", ""), "id": m.get("id"),
                               "mentions": [u.get("id") for u in m.get("mentions", [])],
                               "bot": bool(m.get("author", {}).get("bot"))})
        return sortie

    def recap_discord(self) -> str:
        msgs = [m for m in self.messages_du_jour() if m["texte"].strip()]
        if not msgs:
            return "Aucun message Discord aujourd'hui dans les salons suivis."
        par_salon: dict[str, int] = {}
        for m in msgs:
            par_salon[m["salon"]] = par_salon.get(m["salon"], 0) + 1
        entete = f"{len(msgs)} message(s) aujourd'hui : " + ", ".join(f"{n} dans {s}" for s, n in par_salon.items()) + ". "
        mon_id = str(self.config.get("discord_utilisateur_id", ""))
        mentions = [m for m in msgs if mon_id and mon_id in [str(x) for x in m["mentions"]]]
        if mentions:
            entete += f"Tu es mentionné {len(mentions)} fois. "
        if self.kx.cerveau.disponible or self.kx.cerveau.verifier_connexion():
            corps = "\n".join(f"[{m['salon']}] {m['auteur']} : {m['texte'][:200]}" for m in msgs[-60:])
            t = self.kx.cerveau.reflechir("Résume oralement, en français, en 3 à 5 phrases, l'essentiel de ces messages Discord "
                                          "de la journée (sujets, décisions, questions posées). N'invente rien.\n\n" + corps,
                                          registre="technique", prive=True)
            if t and not self.kx.cerveau.derniere_erreur:
                return entete + t
        return entete

    def mentions_recentes(self) -> list[dict]:
        mon_id = str(self.config.get("discord_utilisateur_id", ""))
        if not mon_id:
            raise ErreurModule("indique discord_utilisateur_id pour que je repère tes mentions")
        return [m for m in self.messages_du_jour() if mon_id in [str(x) for x in m["mentions"]]]

    def _surveiller_mentions(self) -> None:
        mon_id = str(self.config.get("discord_utilisateur_id", ""))
        for salon in self.config.get("discord_salons") or []:
            cle = str(salon)
            params = {"limit": 50}
            if cle in self._dernier_vu:
                params["after"] = self._dernier_vu[cle]
            msgs = self._discord(f"/channels/{salon}/messages", params)
            if not msgs:
                continue
            nouveaux = sorted(msgs, key=lambda m: int(m["id"]))
            premiere_fois = cle not in self._dernier_vu
            self._dernier_vu[cle] = nouveaux[-1]["id"]
            if premiere_fois:
                continue                                  # on n'annonce pas tout l'historique au démarrage
            for m in nouveaux:
                if mon_id and mon_id in [str(u.get("id")) for u in m.get("mentions", [])]:
                    qui = m.get("author", {}).get("global_name") or m.get("author", {}).get("username", "quelqu'un")
                    self.kx.extensions.sons.jouer("message_recu")
                    msg = f"Mention Discord de {qui} : {m.get('content', '')[:160]}"
                    self.kx._diffuser("alerte", {"message": msg})
                    self.kx.voix.parler(f"{qui} t'a mentionné sur Discord.")

    # ======================================================================
    #  Instagram
    # ======================================================================
    @property
    def _api_ig(self) -> str:
        return (self.config.get("instagram_api_base") or "https://graph.facebook.com/v21.0").rstrip("/")

    def comptes_instagram(self) -> list[dict]:
        return [c for c in (self.config.get("instagram_comptes") or []) if c.get("id") and c.get("jeton")]

    def _ig(self, chemin: str, compte: dict, params: dict | None = None):
        p = dict(params or {})
        p["access_token"] = compte["jeton"]
        r = http("GET", self._api_ig + chemin, params=p, timeout=15)
        if r.status_code in (400, 401, 403):
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                pass
            raise ErreurModule(f"Instagram refuse l'accès pour {compte.get('nom', 'ce compte')}" + (f" ({msg[:80]})" if msg else ""))
        if r.status_code >= 400:
            raise ErreurModule(f"Instagram a répondu {r.status_code}")
        return r.json()

    def mesurer(self, compte: dict) -> dict:
        info = self._ig(f"/{compte['id']}", compte, {"fields": "username,followers_count,media_count"})
        vues, nb_video = 0, 0
        try:
            medias = self._ig(f"/{compte['id']}/media", compte,
                              {"fields": "id,media_type,media_product_type,timestamp", "limit": 25}).get("data", [])
            for m in medias:
                if m.get("media_type") != "VIDEO" and m.get("media_product_type") != "REELS":
                    continue
                try:
                    ins = self._ig(f"/{m['id']}/insights", compte, {"metric": "views"}).get("data", [])
                    for i in ins:
                        vues += int((i.get("values") or [{}])[0].get("value", 0) or 0)
                    nb_video += 1
                except ErreurModule:
                    continue
        except ErreurModule as e:
            LOG.info("vues instagram indisponibles : %s", e)
        return {"nom": compte.get("nom") or info.get("username", ""), "username": info.get("username", ""),
                "abonnes": int(info.get("followers_count", 0)), "publications": int(info.get("media_count", 0)),
                "vues": vues, "videos": nb_video}

    def bilan_instagram(self, nom: str | None = None) -> str:
        comptes = self.comptes_instagram()
        if not comptes:
            raise ErreurModule("aucun compte Instagram configuré (instagram_comptes)")
        if nom:
            comptes = [c for c in comptes if normaliser(nom) in normaliser(c.get("nom", "") + " " + c.get("username", ""))] or comptes
        phrases = []
        aujourdhui = time.strftime("%Y-%m-%d")
        hier = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        for c in comptes:
            m = self.mesurer(c)
            avant = self.db.une("SELECT * FROM instagram_snap WHERE compte=? AND jour<? ORDER BY jour DESC LIMIT 1", (c["id"], aujourdhui))
            self.db.executer("INSERT OR REPLACE INTO instagram_snap (compte, jour, abonnes, vues, publications) VALUES (?,?,?,?,?)",
                             (c["id"], aujourdhui, m["abonnes"], m["vues"], m["publications"]))
            p = f"{m['nom']} : {m['abonnes']} abonnés"
            if avant:
                d = m["abonnes"] - int(avant["abonnes"] or 0)
                ref = "hier" if avant["jour"] == hier else f"depuis le {avant['jour']}"
                p += f" ({'+' if d >= 0 else ''}{d} {ref})"
                if m["videos"]:
                    dv = m["vues"] - int(avant["vues"] or 0)
                    p += f", {m['vues']} vues sur tes {m['videos']} dernières vidéos ({'+' if dv >= 0 else ''}{dv})"
            else:
                p += " (première mesure, je comparerai à partir de demain)"
                if m["videos"]:
                    p += f", {m['vues']} vues sur tes {m['videos']} dernières vidéos"
            phrases.append(p)
        return ". ".join(phrases) + "."

    def _boucle(self) -> None:
        derniere_ig = 0.0
        while not self._arret.wait(60):
            try:
                if self.config.get("discord_ecoute") and self.discord_configure():
                    self._surveiller_mentions()
            except ErreurModule as e:
                LOG.debug("discord : %s", e)
            except Exception as e:
                LOG.debug("discord (inattendu) : %s", e)
            try:
                heure = int(self.config.get("instagram_heure_snapshot", 23))
                jour = time.strftime("%Y-%m-%d")
                if self.comptes_instagram() and time.localtime().tm_hour >= heure and self._dernier_snap_jour != jour:
                    self._dernier_snap_jour = jour
                    self.bilan_instagram()
            except Exception as e:
                LOG.debug("instagram (snapshot) : %s", e)

    def demarrer(self) -> None:
        if self._thread is None and (self.config.get("discord_ecoute") or self.comptes_instagram()):
            self._thread = threading.Thread(target=self._boucle, daemon=True, name="kryptonix-social")
            self._thread.start()

    def arreter(self) -> None:
        self._arret.set()

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        try:
            if "discord" in c:
                if re.search(r"\bmention", c):
                    ms = self.mentions_recentes()
                    if not ms:
                        return "Personne ne t'a mentionné aujourd'hui sur Discord."
                    return f"{len(ms)} mention(s) aujourd'hui : " + " ; ".join(f"{m['auteur']} dans {m['salon']} : {m['texte'][:80]}" for m in ms[:4]) + "."
                if re.search(r"\b(resume|recap|recapitulatif|messages|aujourd'hui|du jour|quoi de neuf)\b", c):
                    return self.recap_discord()
            if re.search(r"\b(instagram|insta)\b", c) and re.search(r"\b(abonnes?|followers?|vues?|stats?|statistiques?|combien|compte)\b", c):
                m = re.search(r"\b(?:de|du compte|sur le compte|compte)\s+([a-z0-9_.]+)$", c)
                return self.bilan_instagram(m.group(1) if m else None)
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Réseaux sociaux : {e}."
        return None

    def statut(self) -> dict:
        return {"discord": self.discord_configure(), "instagram": [c.get("nom") for c in self.comptes_instagram()]}

    def outils(self) -> list[Outil]:
        return [
            Outil("discord_recap", "Récapitulatif Discord du jour", lambda a: self.recap_discord(), {}, (), 1, False, "social"),
            Outil("instagram_bilan", "Abonnés et vues Instagram vs la veille", lambda a: self.bilan_instagram(a.get("compte")),
                  {"compte": {"type": "string"}}, (), 1, False, "social"),
        ]
