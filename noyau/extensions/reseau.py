"""
KRYPTONIX / noyau/extensions/reseau.py
=======================================
Accès réseau LOCAL (désactivé par défaut) : pont mobile (iPhone / Raccourcis / Siri) et satellites
multi-pièces (Raspberry Pi, etc.).

Il tourne sur son propre petit serveur (port `reseau_port`, 5055) pour que le tableau de bord
principal reste, lui, strictement limité à ta machine. Activation explicite : `reseau_actif: true`.
Toutes les requêtes exigent le jeton (`Authorization: Bearer <reseau_jeton>`), comparé en temps
constant ; trop d'essais ratés depuis une même adresse = blocage temporaire.

Routes
  GET  /sante
  POST /mobile/commande   {"texte": "..."}      → {"reponse": "..."}   (à dire par Siri)
  POST /mobile/note       {"texte": "..."}      → note enregistrée
  POST /mobile/idee       {"texte": "..."}      → idée ajoutée au pipeline vidéo
  POST /satellite/enregistrer {"piece": "cuisine"}
  POST /satellite/commande {"piece": "...", "texte": "..."}  → {"reponse": "...", "audio_b64": "..."?}
  POST /satellite/audio?piece=cuisine   (corps = WAV 16 bits)  → idem après transcription locale
  GET  /satellite/annonces?piece=cuisine  → messages à dire dans la pièce
  POST /mcp               JSON-RPC MCP (jeton requis)
  POST /twilio/voix, /twilio/suite   webhooks Twilio (signature vérifiée, pas de jeton)
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, normaliser

LOG = logging.getLogger("reseau")
TAILLE_MAX = 25 * 1024 * 1024


class Satellites:
    """Registre des pièces et file d'annonces."""

    def __init__(self):
        self._verrou = threading.Lock()
        self.pieces: dict[str, dict] = {}
        self._annonces: dict[str, list[str]] = {}

    def enregistrer(self, piece: str) -> None:
        piece = normaliser(piece)[:30] or "salon"
        with self._verrou:
            self.pieces[piece] = {"vu": time.time()}
            self._annonces.setdefault(piece, [])

    def vu(self, piece: str) -> None:
        with self._verrou:
            piece = normaliser(piece)
            self.pieces.setdefault(piece, {})["vu"] = time.time()
            self._annonces.setdefault(piece, [])

    def annoncer(self, piece: str, texte: str) -> bool:
        piece = normaliser(piece)
        with self._verrou:
            if piece == "partout":
                for p in self._annonces:
                    self._annonces[p].append(texte)
                return bool(self._annonces)
            if piece in self._annonces:
                self._annonces[piece].append(texte)
                return True
        return False

    def prendre(self, piece: str) -> list[str]:
        piece = normaliser(piece)
        with self._verrou:
            self.pieces.setdefault(piece, {})["vu"] = time.time()
            msgs, self._annonces[piece] = self._annonces.get(piece, []), []
            return msgs

    def liste(self) -> list[dict]:
        with self._verrou:
            return [{"piece": p, "vu_il_y_a_s": round(time.time() - d.get("vu", 0)) if d.get("vu") else None,
                     "en_ligne": bool(d.get("vu")) and time.time() - d["vu"] < 30} for p, d in self.pieces.items()]


class Reseau(Module):
    nom = "reseau"
    description = "Pont mobile (Siri/Raccourcis) et satellites multi-pièces"
    exemples = ("dis à la cuisine que le repas est prêt", "quelles pièces sont connectées", "active le réseau local")

    def __init__(self, kx):
        super().__init__(kx)
        self.satellites = Satellites()
        self._serveur: ThreadingHTTPServer | None = None
        self._echecs: dict[str, list[float]] = {}
        self._verrou = threading.Lock()
        self.port = 0

    # -- jeton / sécurité -------------------------------------------------
    def jeton(self) -> str:
        j = self.config.get("reseau_jeton")
        if not j:
            j = secrets.token_urlsafe(24)
            self.kx.recharger_config({"reseau_jeton": j})
        return j

    def verifier(self, entete_auth: str, ip: str) -> tuple[bool, str]:
        with self._verrou:
            tentatives = [t for t in self._echecs.get(ip, []) if time.time() - t < 300]
            if len(tentatives) >= 8:
                return False, "trop d'essais, réessaie dans 5 minutes"
        recu = entete_auth[7:] if entete_auth.lower().startswith("bearer ") else ""
        if recu and hmac.compare_digest(recu.encode(), self.jeton().encode()):
            return True, ""
        with self._verrou:
            tentatives.append(time.time())
            self._echecs[ip] = tentatives
        return False, "jeton invalide"

    # -- actions des routes ----------------------------------------------
    def mobile_commande(self, texte: str) -> str:
        return self.kx.traiter(texte, source="mobile", vocaliser=False)

    def mobile_note(self, texte: str) -> str:
        texte = texte.strip()[:2000]
        if not texte:
            raise ErreurModule("note vide")
        chemin = os.path.join(DOSSIER_DONNEES, "notes_mobile.md")
        with open(chemin, "a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} — {texte}\n")
        self.kx.memoire.definir_profil(f"note_mobile_{int(time.time())}", texte[:300])
        self.kx.extensions.sons.jouer("notification")
        return "Note enregistrée."

    def mobile_idee(self, texte: str) -> str:
        hub = self.kx.extensions.module("hub")
        texte = texte.strip()[:200]
        if not texte:
            raise ErreurModule("idée vide")
        if hub:
            hub.ajouter_idee(texte)
            self.kx.extensions.sons.jouer("notification")
            return "Idée ajoutée au pipeline."
        return self.mobile_note("IDÉE : " + texte)

    def satellite_commande(self, piece: str, texte: str) -> dict:
        self.satellites.vu(piece)
        mot = self.config.get("mot_reveil", "kryptonix").lower()
        t = texte.strip()
        if self.config.get("satellite_exige_mot_reveil", True) and mot not in t.lower():
            return {"ignore": True, "reponse": ""}
        t = re.sub(rf"\b{re.escape(mot)}\b", "", t, count=1, flags=re.IGNORECASE).strip(" ,.:!?")
        if not t:
            return {"reponse": "Je t'écoute."}
        self.kx.extensions.contexte_piece = normaliser(piece)
        try:
            reponse = self.kx.traiter(t, source=f"satellite:{piece}", vocaliser=False)
        finally:
            self.kx.extensions.contexte_piece = ""
        out = {"reponse": reponse, "piece": piece}
        if self.config.get("satellite_tts_serveur", False):
            audio = self._synthese_mp3(reponse)
            if audio:
                out["audio_b64"] = base64.b64encode(audio).decode()
        return out

    def _synthese_mp3(self, texte: str) -> bytes | None:
        try:
            import asyncio
            import edge_tts
            from ..texte_parle import nettoyer_pour_voix
            chemin = tempfile.mktemp(suffix=".mp3")
            asyncio.run(edge_tts.Communicate(nettoyer_pour_voix(texte), self.kx.voix.voix_edge).save(chemin))
            with open(chemin, "rb") as f:
                data = f.read()
            os.remove(chemin)
            return data
        except Exception as e:
            LOG.debug("synthèse serveur impossible : %s", e)
            return None

    def satellite_audio(self, piece: str, wav_octets: bytes) -> dict:
        trans = self.kx.comprehension.transcripteur
        chemin = tempfile.mktemp(suffix=".wav")
        try:
            with open(chemin, "wb") as f:
                f.write(wav_octets)
            with wave.open(chemin, "rb") as w:
                duree = w.getnframes() / float(w.getframerate() or 16000)
                if w.getframerate() != 16000 or w.getnchannels() != 1:
                    raise ErreurModule("le son doit être en WAV 16 kHz mono")
            from ..medias import ErreurComprehension
            try:
                texte = trans.transcrire(chemin, duree)["brut"]
            except ErreurComprehension:
                return {"ignore": True, "reponse": ""}
        except wave.Error:
            raise ErreurModule("fichier audio invalide") from None
        finally:
            try:
                os.remove(chemin)
            except OSError:
                pass
        return {**self.satellite_commande(piece, texte), "transcription": texte}

    # -- serveur ----------------------------------------------------------
    def demarrer(self) -> None:
        if not self.config.get("reseau_actif", False) or self._serveur is not None:
            return
        self.jeton()
        reseau = self

        class Gestionnaire(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "Kryptonix"

            def log_message(self, *a):
                pass

            def _repondre(self, code: int, donnees: dict) -> None:
                corps = json.dumps(donnees, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

            def _twilio(self, url) -> None:
                tel = reseau.kx.extensions.module("telephone")
                n = int(self.headers.get("Content-Length") or 0)
                if not tel or n > 200_000:
                    return self._repondre(404, {"erreur": "indisponible"})
                params = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True).items()}
                public = (reseau.config.get("twilio_url_publique") or "").rstrip("/")
                if not tel.signature_valide(public + url.path, params, self.headers.get("X-Twilio-Signature", "")):
                    reseau.verifier("", self.client_address[0])          # compte l'échec
                    return self._repondre(403, {"erreur": "signature invalide"})
                xml = (tel.webhook_debut if url.path.endswith("voix") else tel.webhook_suite)(params).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(xml)))
                self.end_headers()
                self.wfile.write(xml)

            def _traiter(self, methode: str) -> None:
                url = urlparse(self.path)
                if url.path == "/sante":
                    return self._repondre(200, {"ok": True, "nom": reseau.kx.nom})
                if methode == "POST" and url.path in ("/twilio/voix", "/twilio/suite"):
                    return self._twilio(url)
                ok, motif = reseau.verifier(self.headers.get("Authorization", ""), self.client_address[0])
                if not ok:
                    return self._repondre(429 if "essais" in motif else 401, {"erreur": motif})
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                corps = b""
                if methode == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > TAILLE_MAX:
                        return self._repondre(413, {"erreur": "trop volumineux"})
                    corps = self.rfile.read(n) if n else b""
                try:
                    if methode == "POST" and url.path == "/mcp":
                        mcp = reseau.kx.extensions.module("mcp")
                        rep = mcp.traiter_json(corps.decode("utf-8", "replace")) if mcp else None
                        if rep is None:
                            self.send_response(202)
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        data = rep.encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    if methode == "POST" and url.path == "/satellite/audio":
                        return self._repondre(200, reseau.satellite_audio(q.get("piece", "salon"), corps))
                    try:
                        j = json.loads(corps.decode("utf-8")) if corps else {}
                    except ValueError:
                        return self._repondre(400, {"erreur": "JSON invalide"})
                    texte = str(j.get("texte", ""))
                    if methode == "POST" and url.path == "/mobile/commande":
                        return self._repondre(200, {"reponse": reseau.mobile_commande(texte)})
                    if methode == "POST" and url.path == "/mobile/note":
                        return self._repondre(200, {"reponse": reseau.mobile_note(texte)})
                    if methode == "POST" and url.path == "/mobile/idee":
                        return self._repondre(200, {"reponse": reseau.mobile_idee(texte)})
                    if methode == "POST" and url.path == "/satellite/enregistrer":
                        reseau.satellites.enregistrer(str(j.get("piece", "salon")))
                        return self._repondre(200, {"ok": True})
                    if methode == "POST" and url.path == "/satellite/commande":
                        return self._repondre(200, reseau.satellite_commande(str(j.get("piece", "salon")), texte))
                    if methode == "GET" and url.path == "/satellite/annonces":
                        return self._repondre(200, {"annonces": reseau.satellites.prendre(q.get("piece", "salon"))})
                    return self._repondre(404, {"erreur": "route inconnue"})
                except ErreurModule as e:
                    return self._repondre(400, {"erreur": str(e)})
                except Exception:
                    LOG.exception("erreur réseau")
                    return self._repondre(500, {"erreur": "erreur interne"})

            def do_GET(self):
                self._traiter("GET")

            def do_POST(self):
                self._traiter("POST")

        hote = "0.0.0.0" if self.config.get("reseau_ecoute_lan", True) else "127.0.0.1"
        try:
            self._serveur = ThreadingHTTPServer((hote, int(self.config.get("reseau_port", 5055))), Gestionnaire)
        except OSError as e:
            LOG.warning("réseau local non démarré : %s", e)
            return
        self.port = self._serveur.server_address[1]
        threading.Thread(target=self._serveur.serve_forever, daemon=True, name="kryptonix-reseau").start()
        LOG.info("Réseau local à l'écoute sur %s:%d", hote, self.port)

    def arreter(self) -> None:
        if self._serveur:
            self._serveur.shutdown()
            self._serveur.server_close()
            self._serveur = None

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        m = re.search(r"\b(?:dis|annonce|previens|préviens|previens|informe)\s+(?:a |à |dans |au |aux )?(?:la |le |l')?([a-z]+(?: [a-z]+)?)\s+(?:que|de|d')\s*(.+)", c)
        if m and (m.group(1).split()[0] in {p for p in self.satellites.pieces} or m.group(1) in ("partout", "tout le monde")
                  or any(p in m.group(1) for p in self.satellites.pieces)):
            piece = next((p for p in self.satellites.pieces if p in m.group(1)), m.group(1))
            if m.group(1) in ("partout", "tout le monde"):
                piece = "partout"
            if self.satellites.annoncer(piece, m.group(2).strip(" .")):
                self.kx.extensions.sons.jouer("satellite")
                return f"Annonce envoyée {'partout' if piece == 'partout' else 'dans ' + piece}."
        if re.search(r"\b(quelles pieces|pieces connectees|mes satellites|satellites)\b", c):
            l = self.satellites.liste()
            return ("Satellites : " + ", ".join(f"{x['piece']} ({'en ligne' if x['en_ligne'] else 'hors ligne'})" for x in l) + ".") if l \
                else "Aucun satellite connecté. Active `reseau_actif` et lance le script satellite dans une pièce."
        if re.search(r"\b(active|lance|demarre) (?:le )?reseau (?:local)?\b", c):
            self.kx.recharger_config({"reseau_actif": True})
            self.demarrer()
            return f"Réseau local actif sur le port {self.port}. Le jeton est dans le panneau." if self.port else "Le réseau local n'a pas pu démarrer."
        return None

    def statut(self) -> dict:
        return {"actif": self._serveur is not None, "port": self.port, "pieces": self.satellites.liste()}

    def outils(self) -> list[Outil]:
        return [Outil("annonce_piece", "Dire un message dans une pièce (satellite)",
                      lambda a: "Annonce envoyée." if self.satellites.annoncer(a["piece"], a["texte"]) else "Pièce inconnue.",
                      {"piece": {"type": "string"}, "texte": {"type": "string"}}, ("piece", "texte"), 1, True, "domotique")]
