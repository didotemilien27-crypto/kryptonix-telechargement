"""
KRYPTONIX / noyau/extensions/telephone.py
==========================================
Appels téléphoniques via Twilio.

  • Message vocal : « appelle Maman et dis-lui que j'arrive en retard » → l'appel est passé, la
    voix lit ton message (voix française Twilio).
  • Conversation en temps réel : Twilio écoute l'interlocuteur (reconnaissance vocale française),
    envoie le texte à Kryptonix, lit sa réponse, et ainsi de suite, jusqu'à la fin de l'appel.

Il faut une adresse PUBLIQUE vers ton PC pour que Twilio joigne `/twilio/voix`. Pointe le tunnel
(ex. `ngrok http 5055`) vers le PETIT serveur réseau (`reseau_actif: true`), JAMAIS vers le tableau de
bord (port 5000) ; renseigne l'adresse dans `twilio_url_publique`.
Réglages : twilio_sid, twilio_token, twilio_numero (ton numéro Twilio), twilio_contacts = {"maman": "+33612345678"},
twilio_repondre_entrants (false par défaut).

Les appels coûtent de l'argent et touchent d'autres personnes : lancer un appel est une action
de niveau 3 (confirmation stricte + délai d'annulation). Les requêtes de Twilio sont vérifiées
par signature (X-Twilio-Signature) : un tiers ne peut pas parler à ton assistant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import threading
import time
from xml.sax.saxutils import escape

from .base import ErreurModule, Module, Outil, http, liste_parlee, normaliser

LOG = logging.getLogger("telephone")

VOIX_TWILIO = "Polly.Lea"        # voix neuronale française


def signature_twilio(jeton: str, url: str, params: dict) -> str:
    """Signature attendue : HMAC-SHA1(jeton, url + clés/valeurs triées), en base64."""
    donnees = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(jeton.encode(), donnees.encode(), hashlib.sha1).digest()).decode()


def twiml(*morceaux: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(morceaux) + "</Response>"


def dire(texte: str) -> str:
    return f'<Say language="fr-FR" voice="{VOIX_TWILIO}">{escape(texte)}</Say>'


class Telephone(Module):
    nom = "telephone"
    description = "Appels sortants et conversation en temps réel (Twilio)"
    exemples = ("appelle Maman et dis-lui que j'arrive", "appelle le +33612345678 pour confirmer le rendez-vous",
                "raccroche")

    def __init__(self, kx):
        super().__init__(kx)
        self._appels: dict[str, dict] = {}        # sid → contexte de la conversation
        self._verrou = threading.Lock()
        self.dernier_appel = ""

    # -- configuration ---------------------------------------------------
    def configure(self) -> bool:
        return all(self.config.get(k) for k in ("twilio_sid", "twilio_token", "twilio_numero"))

    @property
    def _api(self) -> str:
        return (self.config.get("twilio_api_base") or "https://api.twilio.com").rstrip("/")

    def resoudre_numero(self, cible: str) -> tuple[str, str]:
        contacts = self.config.get("twilio_contacts") or {}
        m = re.search(r"(\+?\d[\d .-]{7,})", cible)
        if m:
            n = re.sub(r"[ .-]", "", m.group(1))
            if n.startswith("0") and len(n) == 10:
                n = "+33" + n[1:]
            if re.fullmatch(r"\+\d{8,15}", n):
                return n, n
        c = normaliser(cible)
        for nom, numero in contacts.items():
            if normaliser(nom) in c:
                return numero, nom
        raise ErreurModule(f"je ne connais pas « {cible.strip()} » (ajoute-le dans twilio_contacts)")

    # -- appels sortants ---------------------------------------------------
    def appeler(self, numero: str, message: str = "", conversation: bool = False, objectif: str = "") -> str:
        if not self.configure():
            raise ErreurModule("Twilio n'est pas configuré (twilio_sid, twilio_token, twilio_numero)")
        url_publique = (self.config.get("twilio_url_publique") or "").rstrip("/")
        donnees = {"To": numero, "From": self.config["twilio_numero"]}
        if conversation:
            if not url_publique:
                raise ErreurModule("la conversation en temps réel demande twilio_url_publique (tunnel ngrok)")
            donnees["Url"] = f"{url_publique}/twilio/voix"
        else:
            intro = f"Bonjour, ici l'assistant de {self.config.get('nom_utilisateur', 'votre contact')}. "
            donnees["Twiml"] = twiml(dire(intro + message), '<Pause length="1"/>',
                                     dire("Je répète. " + message), "<Hangup/>")
        r = http("POST", f"{self._api}/2010-04-01/Accounts/{self.config['twilio_sid']}/Calls.json",
                 data=donnees, auth=(self.config["twilio_sid"], self.config["twilio_token"]), timeout=15)
        if r.status_code in (401, 403):
            raise ErreurModule("Twilio refuse les identifiants")
        if r.status_code >= 400:
            try:
                detail = r.json().get("message", "")[:100]
            except ValueError:
                detail = ""
            raise ErreurModule(f"Twilio a refusé l'appel ({r.status_code}) {detail}".strip())
        sid = r.json().get("sid", "")
        with self._verrou:
            self._appels[sid] = {"objectif": objectif or message, "tours": 0, "debut": time.time(), "historique": []}
        self.dernier_appel = sid
        self.kx.extensions.sons.jouer("appel_sortant")
        self.kx.memoire.journaliser("appel", f"sortant vers {numero[-4:].rjust(len(numero), '*')}")
        return f"Appel lancé vers {numero}."

    def raccrocher(self, sid: str | None = None) -> str:
        sid = sid or self.dernier_appel
        if not sid or not self.configure():
            raise ErreurModule("aucun appel en cours")
        r = http("POST", f"{self._api}/2010-04-01/Accounts/{self.config['twilio_sid']}/Calls/{sid}.json",
                 data={"Status": "completed"}, auth=(self.config["twilio_sid"], self.config["twilio_token"]), timeout=10)
        if r.status_code >= 400:
            raise ErreurModule("impossible de raccrocher")
        self.kx.extensions.sons.jouer("raccroche")
        return "J'ai raccroché."

    # -- webhooks (appelés par Twilio) ------------------------------------
    def signature_valide(self, url: str, params: dict, signature: str) -> bool:
        if self.config.get("twilio_verifier_signature", True) is False:
            return True
        jeton = self.config.get("twilio_token", "")
        return bool(jeton and signature and hmac.compare_digest(signature_twilio(jeton, url, params), signature))

    def webhook_debut(self, params: dict) -> str:
        sid = params.get("CallSid", "")
        entrant = params.get("Direction", "").startswith("inbound")
        if entrant and not self.config.get("twilio_repondre_entrants", False):
            return twiml(dire("Cette ligne n'accepte pas les appels. Au revoir."), "<Hangup/>")
        with self._verrou:
            ctx = self._appels.setdefault(sid, {"objectif": "", "tours": 0, "debut": time.time(), "historique": []})
        self.kx.extensions.sons.jouer("appel_entrant" if entrant else "appel_sortant")
        accueil = (f"Bonjour, je suis {self.kx.nom}, l'assistant de {self.config.get('nom_utilisateur', '')}. Je vous écoute."
                   if entrant else
                   f"Bonjour, je suis {self.kx.nom}, l'assistant de {self.config.get('nom_utilisateur', '')}. "
                   + (f"Je vous appelle pour : {ctx['objectif']}." if ctx["objectif"] else "Que puis-je faire pour vous ?"))
        return twiml(self._ecoute(accueil))

    def _ecoute(self, phrase: str) -> str:
        return (f'<Gather input="speech" language="fr-FR" speechTimeout="auto" timeout="6" '
                f'action="{escape(self._chemin_suite())}" method="POST">{dire(phrase)}</Gather>'
                + dire("Je n'ai rien entendu. Au revoir.") + "<Hangup/>")

    def _chemin_suite(self) -> str:
        base = (self.config.get("twilio_url_publique") or "").rstrip("/")
        return f"{base}/twilio/suite"

    def webhook_suite(self, params: dict) -> str:
        sid = params.get("CallSid", "")
        dit = (params.get("SpeechResult") or "").strip()
        with self._verrou:
            ctx = self._appels.setdefault(sid, {"objectif": "", "tours": 0, "debut": time.time(), "historique": []})
            ctx["tours"] += 1
            tours = ctx["tours"]
        if not dit:
            return twiml(self._ecoute("Pardon, je n'ai pas compris. Pouvez-vous répéter ?"))
        maxi = int(self.config.get("twilio_tours_max", 12))
        if tours > maxi or re.search(r"\b(au revoir|bonne journee|a bientot|raccroche|merci beaucoup au revoir)\b", normaliser(dit)):
            return twiml(dire("Merci, bonne journée. Au revoir."), "<Hangup/>")
        ctx["historique"].append(f"Interlocuteur : {dit}")
        contexte = "\n".join(ctx["historique"][-8:])
        reponse = self.kx.cerveau.reflechir(
            "[APPEL TÉLÉPHONIQUE EN DIRECT — réponds en une ou deux phrases courtes et parlées] "
            f"Tu parles au téléphone au nom de {self.config.get('nom_utilisateur', 'ton utilisateur')}. "
            f"Objectif de l'appel : {ctx['objectif'] or 'répondre poliment'}. Tu ne prends aucun engagement financier ni "
            "ne divulgues d'information privée. Échange jusqu'ici :\n" + contexte + "\nTa réponse :",
            registre="neutre")
        reponse = re.sub(r"\s+", " ", reponse).strip()[:400]
        ctx["historique"].append(f"Toi : {reponse}")
        self.kx.memoire.journaliser("appel", f"tour {tours}")
        return twiml(self._ecoute(reponse))

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        try:
            if re.search(r"\braccroche\b|\btermine l'appel\b", c) and self.dernier_appel:
                return self.raccrocher()
            m = re.search(r"(?<![\w'’])(?:appelle|appelez|telephone a|téléphone à|passe un coup de fil à|passe un coup de fil a|contacte par téléphone)\s+(.+)", texte, re.IGNORECASE)
            if not m:
                return None
            reste = m.group(1)
            msg = re.search(r"\b(?:et )?(?:dis|dites|annonce|previens|informe|laisse)(?:[- ]lui| un message)?\s*(?:que |de |:)?\s*(.+)$", reste, re.IGNORECASE)
            cible = reste[:msg.start()] if msg else reste
            cible = re.sub(r"\b(pour|afin de)\b.*$", "", cible, flags=re.IGNORECASE).strip()
            numero, nom = self.resoudre_numero(cible)
            if msg:
                message = msg.group(1).strip(" .")
                return self.securise("telephone_message", {"numero": numero, "message": message},
                                     description=f"appeler {nom} pour lui dire : « {message} »")
            obj = re.search(r"\bpour (.+)$", reste, re.IGNORECASE)
            return self.securise("telephone_conversation", {"numero": numero, "objectif": obj.group(1).strip(" .") if obj else ""},
                                 description=f"appeler {nom} et tenir la conversation à ta place")
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Téléphone : {e}."

    def statut(self) -> dict:
        return {"configure": self.configure(), "public": bool(self.config.get("twilio_url_publique")),
                "contacts": list((self.config.get("twilio_contacts") or {}).keys())}

    def outils(self) -> list[Outil]:
        return [
            Outil("telephone_message", "Appeler et lire un message vocal",
                  lambda a: self.appeler(a["numero"], a["message"]), {"numero": {"type": "string"}, "message": {"type": "string"}},
                  ("numero", "message"), 3, False, "telephone"),
            Outil("telephone_conversation", "Appeler et tenir une conversation",
                  lambda a: self.appeler(a["numero"], "", True, a.get("objectif", "")),
                  {"numero": {"type": "string"}, "objectif": {"type": "string"}}, ("numero",), 3, False, "telephone"),
            Outil("telephone_raccrocher", "Raccrocher l'appel en cours", lambda a: self.raccrocher(), {}, (), 1, False, "telephone"),
        ]
