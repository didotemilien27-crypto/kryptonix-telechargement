"""
KRYPTONIX / noyau/extensions/emails.py
=======================================
Gmail (ou toute boîte IMAP) : résumé des courriels non lus et aide à la rédaction.

Connexion simple et sans développeur : IMAP + « mot de passe d'application » Google
(compte Google → Sécurité → Validation en 2 étapes → Mots de passe des applications).
Réglages : gmail_adresse, gmail_mot_de_passe_app (ou variable d'environnement
KRYPTONIX_GMAIL_MDP), imap_hote (défaut imap.gmail.com), smtp_hote (défaut smtp.gmail.com).

CONFIDENTIALITÉ : les courriels sont lus et résumés par le cerveau LOCAL uniquement
(`prive=True`) : leur contenu ne part jamais vers un fournisseur cloud.
L'ENVOI d'un courriel est une action de niveau 3 (confirmation stricte + délai d'annulation).
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import os
import re
import smtplib
from email.message import EmailMessage

from .base import ErreurModule, Module, Outil, liste_parlee, normaliser

LOG = logging.getLogger("emails")


def decoder_entete(valeur: str) -> str:
    morceaux = []
    for texte, jeu in email.header.decode_header(valeur or ""):
        if isinstance(texte, bytes):
            try:
                texte = texte.decode(jeu or "utf-8", errors="replace")
            except LookupError:
                texte = texte.decode("utf-8", errors="replace")
        morceaux.append(texte)
    return re.sub(r"\s+", " ", "".join(morceaux)).strip()


def texte_du_message(msg: email.message.Message, limite: int = 1200) -> str:
    """Corps en texte brut (HTML dépouillé en dernier recours), tronqué."""
    brut = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                brut = _decoder_partie(part)
                break
        if not brut:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    brut = re.sub(r"<[^>]+>", " ", _decoder_partie(part))
                    break
    else:
        brut = _decoder_partie(msg)
        if msg.get_content_type() == "text/html":
            brut = re.sub(r"<[^>]+>", " ", brut)
    return re.sub(r"\s+", " ", brut).strip()[:limite]


def _decoder_partie(part) -> str:
    charge = part.get_payload(decode=True)
    if charge is None:
        return ""
    try:
        return charge.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return charge.decode("utf-8", errors="replace")


class Emails(Module):
    nom = "emails"
    description = "Résumé de la boîte mail et aide à la rédaction"
    exemples = ("résume mes mails", "ai-je des nouveaux mails", "aide-moi à répondre au dernier mail",
                "écris un mail à Paul pour reporter la réunion")

    def __init__(self, kx):
        super().__init__(kx)
        self._derniers: list[dict] = []
        self.brouillon: dict | None = None

    # -- connexion --------------------------------------------------------
    def configure(self) -> bool:
        return bool(self.config.get("gmail_adresse") and self._mdp())

    def _mdp(self) -> str:
        return (self.config.get("gmail_mot_de_passe_app") or os.environ.get("KRYPTONIX_GMAIL_MDP", "")).replace(" ", "")

    def _connexion(self):
        if not self.configure():
            raise ErreurModule("la boîte mail n'est pas configurée (gmail_adresse et mot de passe d'application)")
        try:
            cx = imaplib.IMAP4_SSL(self.config.get("imap_hote", "imap.gmail.com"),
                                   int(self.config.get("imap_port", 993)), timeout=15)
            cx.login(self.config["gmail_adresse"], self._mdp())
            return cx
        except imaplib.IMAP4.error:
            raise ErreurModule("la boîte mail a refusé la connexion (mot de passe d'application ?)") from None
        except (OSError, TimeoutError):
            raise ErreurModule("la boîte mail est injoignable") from None

    # -- lecture ----------------------------------------------------------
    def lire(self, critere: str = "UNSEEN", maxi: int = 8, corps: bool = True) -> list[dict]:
        cx = self._connexion()
        sortie = []
        try:
            cx.select("INBOX", readonly=True)
            typ, data = cx.search(None, critere)
            if typ != "OK":
                return []
            ids = (data[0] or b"").split()[-maxi:][::-1]
            for i in ids:
                typ, morceaux = cx.fetch(i, "(BODY.PEEK[])")       # PEEK : ne marque pas comme lu
                if typ != "OK" or not morceaux or not isinstance(morceaux[0], tuple):
                    continue
                msg = email.message_from_bytes(morceaux[0][1])
                nom, adresse = email.utils.parseaddr(msg.get("From", ""))
                sortie.append({"id": i.decode(), "de": decoder_entete(nom) or adresse, "adresse": adresse,
                               "sujet": decoder_entete(msg.get("Subject", "")) or "(sans objet)",
                               "date": msg.get("Date", ""), "message_id": msg.get("Message-ID", ""),
                               "extrait": texte_du_message(msg) if corps else ""})
        finally:
            try:
                cx.logout()
            except Exception:
                pass
        self._derniers = sortie
        return sortie

    def recherche(self, critere_imap: str, maxi: int = 50) -> list[dict]:
        """Utilisé par le cockpit financier (reçus)."""
        return self.lire(critere_imap, maxi, corps=True)

    def resume(self, maxi: int = 6) -> str:
        mails = self.lire("UNSEEN", maxi)
        if not mails:
            return "Aucun nouveau mail non lu."
        self.kx.extensions.sons.jouer("courriel")
        entete = f"Tu as {len(mails)} mail{'s' if len(mails) > 1 else ''} non lu{'s' if len(mails) > 1 else ''}. "
        if self.kx.cerveau.disponible or self.kx.cerveau.verifier_connexion():
            blocs = "\n\n".join(f"De : {m['de']}\nObjet : {m['sujet']}\n{m['extrait'][:500]}" for m in mails)
            texte = self.kx.cerveau.reflechir(
                "Voici des courriels non lus. Fais un résumé oral très court, en français : pour chaque "
                "courriel, qui écrit et ce qu'il veut, en une phrase. Signale ce qui semble urgent. "
                "N'invente rien.\n\n" + blocs, registre="technique", prive=True)
            if not self.kx.cerveau.derniere_erreur and texte:
                return entete + texte
        return entete + "; ".join(f"{m['de']} : {m['sujet']}" for m in mails) + "."

    # -- rédaction --------------------------------------------------------
    def rediger(self, consigne: str, destinataire: str = "", sujet: str = "") -> dict:
        if not (self.kx.cerveau.disponible or self.kx.cerveau.verifier_connexion()):
            raise ErreurModule("le cerveau local est hors ligne, je ne peux pas rédiger")
        corps = self.kx.cerveau.reflechir(
            "Rédige un courriel en français, poli et naturel, sans objet ni formule d'en-tête superflue. "
            f"Demande : {consigne}\nÉcris uniquement le corps du message, signé « {self.config.get('nom_utilisateur', '')} ».",
            registre="technique", prive=True)
        if self.kx.cerveau.derniere_erreur:
            raise ErreurModule("la rédaction a échoué")
        self.brouillon = {"a": destinataire, "sujet": sujet or consigne[:60].capitalize(), "corps": corps}
        return self.brouillon

    def envoyer(self, a: str, sujet: str, corps: str) -> str:
        if not self.configure():
            raise ErreurModule("la boîte mail n'est pas configurée")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a or ""):
            raise ErreurModule("l'adresse du destinataire est invalide")
        msg = EmailMessage()
        msg["From"] = self.config["gmail_adresse"]
        msg["To"] = a
        msg["Subject"] = sujet
        msg.set_content(corps)
        try:
            with smtplib.SMTP_SSL(self.config.get("smtp_hote", "smtp.gmail.com"),
                                  int(self.config.get("smtp_port", 465)), timeout=20) as s:
                s.login(self.config["gmail_adresse"], self._mdp())
                s.send_message(msg)
        except smtplib.SMTPException as e:
            raise ErreurModule(f"l'envoi a échoué ({e.__class__.__name__})") from None
        except OSError:
            raise ErreurModule("le serveur d'envoi est injoignable") from None
        self.kx.extensions.sons.jouer("message_envoye")
        return f"Mail envoyé à {a}."

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        if not re.search(r"\b(mails?|e-?mails?|courriels?|gmail|boite de reception|messagerie|brouillon|destinataire)\b", c):
            return None
        try:
            if re.search(r"\b(envoie|envoyer)\b.*\b(brouillon|le mail|le message)\b|\benvoie[- ]le\b", c) and self.brouillon:
                b = self.brouillon
                if not b.get("a"):
                    return "Il me faut l'adresse du destinataire : « le brouillon est pour paul@exemple.fr »."
                return self.securise("mail_envoyer", dict(b), description=f"envoyer le mail « {b['sujet']} » à {b['a']}")
            m = re.search(r"\bdestinataire (?:est |:)?\s*([^\s@]+@[^\s@]+\.[^\s@]+)|\bbrouillon est pour ([^\s@]+@[^\s@]+\.[^\s@]+)", c)
            if m and self.brouillon:
                self.brouillon["a"] = m.group(1) or m.group(2)
                return f"Destinataire réglé sur {self.brouillon['a']}. Dis « envoie le brouillon » pour l'envoyer."
            if re.search(r"\b(ecris|redige|prepare|aide[- ]moi a (?:ecrire|rediger)|brouillon)\b.*\b(mail|courriel|message)\b|"
                         r"\b(mail|courriel)\b.*\b(ecris|redige|prepare)\b", c):
                demande = re.sub(r"^(?:\w+ )*?(?:ecris|redige|prepare)(?: moi)? (?:un |une |le )?(?:mail|courriel|message)\s*", "", c).strip() or c
                m_dest = re.search(r"([^\s@]+@[^\s@]+\.[^\s@]+)", texte)
                b = self.rediger(texte, m_dest.group(1) if m_dest else "")
                self.kx.extensions.sons.jouer("document")
                return (f"Voici un brouillon :\n{b['corps']}\n\nDis « le brouillon est pour adresse » puis "
                        "« envoie le brouillon » quand il te convient.")
            if re.search(r"\b(repondre|reponds|reponse)\b", c) and self._derniers:
                m0 = self._derniers[0]
                b = self.rediger(f"Réponds à ce mail de {m0['de']} (objet : {m0['sujet']}) : {m0['extrait'][:600]}. "
                                 f"Précision de l'utilisateur : {texte}", m0["adresse"], "Re: " + m0["sujet"])
                return f"Brouillon de réponse à {m0['de']} :\n{b['corps']}\n\nDis « envoie le brouillon » pour l'envoyer."
            if re.search(r"\b(resume|resumer|lis|lire|nouveaux|non lus?|ai[- ]je|derniers|boite|recu)\b", c):
                return self.resume()
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Mails : {e}."
        return None

    def statut(self) -> dict:
        return {"configure": self.configure(), "adresse": self.config.get("gmail_adresse", "")}

    def outils(self) -> list[Outil]:
        return [
            Outil("mail_resume", "Résumer les mails non lus", lambda a: self.resume(int(a.get("max", 6))),
                  {"max": {"type": "integer"}}, (), 1, True, "mail"),
            Outil("mail_envoyer", "Envoyer un mail", lambda a: self.envoyer(a["a"], a["sujet"], a["corps"]),
                  {"a": {"type": "string"}, "sujet": {"type": "string"}, "corps": {"type": "string"}},
                  ("a", "sujet", "corps"), 3, False, "mail"),
        ]
