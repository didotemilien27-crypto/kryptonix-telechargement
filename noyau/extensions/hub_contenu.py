"""
KRYPTONIX / noyau/extensions/hub_contenu.py
============================================
Hub de contenu pour créateurs.

  • VAULT d'inspirations : colle un lien TikTok / Instagram / YouTube → la vidéo (ou ses
    sous-titres) est téléchargée, TRANSCRITE en local (Whisper), puis indexée : tes documents et
    tes idées peuvent s'en inspirer. Nécessite `yt-dlp` (pip install yt-dlp) et ffmpeg.
  • IDÉES et SCRIPTS : générés par le cerveau, nourris de ton vault et de ta mémoire (projets).
  • YOUTUBE : ingestion d'une vidéo (sous-titres si disponibles, sinon audio transcrit).
  • PIPELINE vidéo : Idée → Script → Tournage → Montage → Publié, croisé avec l'agenda
    (« où j'en suis ? »).

Respecte les conditions d'utilisation des plateformes : le vault sert à l'étude personnelle.
"""

from __future__ import annotations

import datetime as dt
import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import time

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, liste_parlee, normaliser, suite_originale
from .stockage import Stockage

LOG = logging.getLogger("hub")

ETAPES = ["idee", "script", "tournage", "montage", "publie"]
LIBELLES = {"idee": "Idée", "script": "Script", "tournage": "Tournage", "montage": "Montage", "publie": "Publié"}
ALIAS_ETAPES = {"idee": "idee", "idees": "idee", "script": "script", "ecriture": "script", "scenario": "script",
                "tournage": "tournage", "tourne": "tournage", "filme": "tournage", "montage": "montage", "monte": "montage",
                "publie": "publie", "publiee": "publie", "poste": "publie", "en ligne": "publie", "termine": "publie"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS vault (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, titre TEXT, auteur TEXT, plateforme TEXT,
    doc_id INTEGER, tags TEXT DEFAULT '', ajoute REAL
);
CREATE TABLE IF NOT EXISTS pipeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT, titre TEXT NOT NULL, etape TEXT NOT NULL DEFAULT 'idee',
    date_cible TEXT, notes TEXT DEFAULT '', cree REAL, maj REAL
);
"""


def plateforme_de(url: str) -> str:
    u = url.lower()
    for nom, motifs in (("tiktok", ("tiktok.com",)), ("instagram", ("instagram.com",)),
                        ("youtube", ("youtube.com", "youtu.be")), ("twitter", ("twitter.com", "x.com"))):
        if any(m in u for m in motifs):
            return nom
    return "web"


def vtt_vers_texte(vtt: str) -> str:
    lignes, vu = [], set()
    for l in vtt.splitlines():
        l = l.strip()
        if not l or l.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")) or "-->" in l or re.fullmatch(r"\d+", l):
            continue
        l = re.sub(r"<[^>]+>", "", l)
        if l and l not in vu:
            vu.add(l)
            lignes.append(l)
    return " ".join(lignes)


class HubContenu(Module):
    nom = "hub"
    description = "Vault d'inspirations, idées, scripts, YouTube et pipeline vidéo"
    exemples = ("ajoute ce lien au vault https://...", "donne-moi 5 idées de vidéos sur le fitness",
                "écris un script de 60 secondes sur ...", "où j'en suis sur mes vidéos",
                "passe la vidéo X en montage")

    def __init__(self, kx):
        super().__init__(kx)
        self.db = Stockage(os.path.join(DOSSIER_DONNEES, "extensions.db"), SCHEMA)
        self.dossier = os.path.join(DOSSIER_DONNEES, "vault")
        os.makedirs(self.dossier, exist_ok=True)
        self._telecharger = self._telecharger_ytdlp          # remplaçable (tests)

    # -- outil de téléchargement ------------------------------------------
    @staticmethod
    def ytdlp_disponible() -> bool:
        try:
            import yt_dlp  # noqa: F401
            return True
        except ImportError:
            return bool(shutil.which("yt-dlp"))

    def _commande_ytdlp(self) -> list[str]:
        try:
            import yt_dlp  # noqa: F401
            return [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            exe = shutil.which("yt-dlp")
            if exe:
                return [exe]
        raise ErreurModule("yt-dlp n'est pas installé (pip install yt-dlp)")

    def _telecharger_ytdlp(self, url: str, sous_titres_seulement: bool = False) -> dict:
        """→ {titre, auteur, fichier (média ou .vtt)}"""
        base = self._commande_ytdlp()
        racine = os.path.join(self.dossier, f"dl_{int(time.time())}")
        meta = subprocess.run(base + ["--no-playlist", "--print", "%(title)s\n%(uploader)s", "--skip-download", url],
                              capture_output=True, text=True, timeout=60)
        if meta.returncode != 0:
            raise ErreurModule("impossible de lire ce lien (privé, supprimé ou non pris en charge)")
        titre, _, auteur = (meta.stdout.strip() + "\n").partition("\n")
        if sous_titres_seulement or plateforme_de(url) == "youtube":
            subprocess.run(base + ["--no-playlist", "--skip-download", "--write-auto-subs", "--write-subs",
                                   "--sub-langs", "fr.*,en.*", "--sub-format", "vtt", "-o", racine + ".%(ext)s", url],
                           capture_output=True, text=True, timeout=120)
            subs = sorted(glob.glob(racine + "*.vtt"))
            if subs:
                return {"titre": titre, "auteur": auteur.strip(), "fichier": subs[0]}
            if sous_titres_seulement:
                raise ErreurModule("cette vidéo n'a pas de sous-titres")
        sortie = subprocess.run(base + ["--no-playlist", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
                                        "--max-filesize", "150M", "-o", racine + ".%(ext)s", url],
                                capture_output=True, text=True, timeout=300)
        fichiers = [f for f in glob.glob(racine + ".*") if not f.endswith((".part", ".vtt"))]
        if sortie.returncode != 0 or not fichiers:
            raise ErreurModule("le téléchargement a échoué (ffmpeg est-il installé ?)")
        return {"titre": titre, "auteur": auteur.strip(), "fichier": fichiers[0]}

    # -- vault ------------------------------------------------------------
    def ajouter_au_vault(self, url: str, tags: str = "") -> str:
        if not re.match(r"^https?://", url):
            raise ErreurModule("lien invalide")
        if self.db.une("SELECT id FROM vault WHERE url=?", (url,)):
            return "Ce lien est déjà dans ton vault."
        self.kx._definir_etat("ingestion")
        try:
            res = self._telecharger(url)
            chemin = res["fichier"]
            if chemin.endswith(".vtt"):
                with open(chemin, "r", encoding="utf-8", errors="replace") as f:
                    texte = vtt_vers_texte(f.read())
                cible = chemin[:-4] + ".txt"
                with open(cible, "w", encoding="utf-8") as f:
                    f.write(f"{res['titre']} — {res['auteur']}\n\n{texte}")
                chemin = cible
            ing = self.kx.documents.ingerer_fichier(chemin, resumer=False)
        finally:
            self.kx._definir_etat("veille")
        if not ing["succes"]:
            raise ErreurModule("la transcription a échoué : " + (ing.get("erreur") or "contenu vide"))
        self.db.executer("INSERT INTO vault (url, titre, auteur, plateforme, doc_id, tags, ajoute) VALUES (?,?,?,?,?,?,?)",
                         (url, res["titre"], res["auteur"], plateforme_de(url), ing["id"], tags, time.time()))
        self.kx.extensions.sons.jouer("document")
        return f"« {res['titre'][:70]} » est dans ton vault ({ing.get('caracteres', 0)} caractères indexés)."

    def vault(self, limite: int = 20) -> list[dict]:
        return self.db.lignes("SELECT * FROM vault ORDER BY ajoute DESC LIMIT ?", (limite,))

    def inspirations_pour(self, sujet: str, n: int = 3) -> str:
        ids = [v["doc_id"] for v in self.db.lignes("SELECT doc_id FROM vault")]
        if not ids:
            return ""
        blocs = []
        for d in self.kx.memoire.rechercher_documents(sujet, 8):
            if d["id"] in ids and len(blocs) < n:
                doc = self.kx.memoire.document(d["id"])
                blocs.append(f"[{doc['nom']}] {(doc.get('contenu') or '')[:700]}")
        return "\n\n".join(blocs)

    # -- idées / scripts --------------------------------------------------
    def _cerveau_pret(self) -> None:
        if not (self.kx.cerveau.disponible or self.kx.cerveau.verifier_connexion()
                or self.config.get("mode_ia", "local") != "local"):
            raise ErreurModule("le cerveau est hors ligne")

    def idees(self, sujet: str, n: int = 5) -> str:
        self._cerveau_pret()
        insp = self.inspirations_pour(sujet)
        projets = self.kx.extensions.module("memoire_longue").projets() if self.kx.extensions.module("memoire_longue") else {}
        contexte = (f"\n\nInspirations de mon vault (ne les copie pas, inspire-t'en) :\n{insp}" if insp else "") + \
                   (f"\n\nMes projets : {projets}" if projets else "")
        rep = self.kx.cerveau.reflechir(
            f"Propose {n} idées de vidéos courtes originales sur : {sujet}. Pour chacune : un titre accrocheur et une "
            f"phrase d'accroche (le hook des 3 premières secondes). Numérote-les, sans markdown.{contexte}",
            registre="technique", prive=bool(insp))
        if self.kx.cerveau.derniere_erreur:
            raise ErreurModule("la génération d'idées a échoué")
        return rep

    def script(self, sujet: str, secondes: int = 60) -> str:
        self._cerveau_pret()
        insp = self.inspirations_pour(sujet, 2)
        rep = self.kx.cerveau.reflechir(
            f"Écris le script parlé d'une vidéo de {secondes} secondes sur : {sujet}. Structure : HOOK (3 s), "
            f"DÉVELOPPEMENT en 3 points, CONCLUSION avec appel à l'action. Phrases courtes à dire à voix haute, "
            f"avec indications de plans entre crochets.{(chr(10) * 2 + 'Inspirations :' + chr(10) + insp) if insp else ''}",
            registre="technique", prive=bool(insp))
        if self.kx.cerveau.derniere_erreur:
            raise ErreurModule("la rédaction du script a échoué")
        return rep

    # -- pipeline ---------------------------------------------------------
    def ajouter_idee(self, titre: str, etape: str = "idee", date_cible: str | None = None) -> int:
        now = time.time()
        return self.db.executer("INSERT INTO pipeline (titre, etape, date_cible, cree, maj) VALUES (?,?,?,?,?)",
                                (titre.strip(), etape, date_cible, now, now)).lastrowid

    def trouver(self, motif: str) -> list[dict]:
        m = normaliser(motif)
        return [p for p in self.db.lignes("SELECT * FROM pipeline ORDER BY maj DESC") if m and m in normaliser(p["titre"])]

    def changer_etape(self, motif: str, etape: str) -> str:
        trouves = self.trouver(motif)
        if not trouves:
            raise ErreurModule(f"aucune vidéo ne correspond à « {motif} »")
        if len(trouves) > 1:
            raise ErreurModule("plusieurs vidéos correspondent : " + liste_parlee([t["titre"] for t in trouves[:4]]))
        self.db.executer("UPDATE pipeline SET etape=?, maj=? WHERE id=?", (etape, time.time(), trouves[0]["id"]))
        self.kx.extensions.sons.jouer("succes")
        return f"« {trouves[0]['titre']} » passe à l'étape {LIBELLES[etape].lower()}."

    def ou_en_suis_je(self) -> str:
        lignes = self.db.lignes("SELECT * FROM pipeline WHERE etape != 'publie' ORDER BY maj DESC")
        publies = self.db.une("SELECT COUNT(*) AS n FROM pipeline WHERE etape='publie'")["n"]
        if not lignes and not publies:
            return "Ton pipeline est vide. Dis « ajoute l'idée ... au pipeline »."
        comptes = {e: sum(1 for l in lignes if l["etape"] == e) for e in ETAPES[:-1]}
        phrase = ", ".join(f"{n} en {LIBELLES[e].lower()}" for e, n in comptes.items() if n) or "rien en cours"
        morceaux = [f"Pipeline : {phrase}, {publies} publiée(s)."]
        agenda = self.kx.extensions.module("agenda")
        evs = []
        if agenda:
            try:
                now = dt.datetime.now()
                evs = agenda.evenements(now, now + dt.timedelta(days=14))
            except Exception:
                evs = []
        for l in lignes[:5]:
            mots = [m for m in normaliser(l["titre"]).split() if len(m) > 3]
            prochain = next((e for e in evs if mots and any(m in normaliser(e["titre"]) for m in mots)), None)
            if not prochain:
                prochain = next((e for e in evs if re.search(rf"\b{l['etape']}\b", normaliser(e["titre"]))), None)
            extra = f", créneau agenda {agenda.jour_parle(prochain['debut'])} à {agenda._heure(prochain)}" if prochain else ""
            cible = f", pour le {l['date_cible']}" if l["date_cible"] else ""
            morceaux.append(f"« {l['titre']} » : {LIBELLES[l['etape']].lower()}{cible}{extra}.")
        return " ".join(morceaux)

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        try:
            m = re.search(r"(https?://\S+)", texte)
            if m and re.search(r"\b(vault|inspiration|inspirations|sauvegarde|garde|ajoute|ingere|transcris|youtube)\b", c):
                return self.ajouter_au_vault(m.group(1).rstrip(".,)"))
            if re.search(r"\b(mon vault|mes inspirations|liste le vault)\b", c):
                v = self.vault(6)
                return ("Dans ton vault : " + liste_parlee([x["titre"][:50] or x["url"] for x in v]) + ".") if v else "Ton vault est vide."
            m = re.search(r"\b(?:donne[- ]moi|propose(?:[- ]moi)?|trouve[- ]moi|genere)\s+(\d+|trois|cinq|dix)?\s*idees?(?: de (?:videos?|contenus?|posts?))?\s*(?:sur|autour de|pour)?\s*(.*)", c)
            if m:
                n = {"trois": 3, "cinq": 5, "dix": 10}.get(m.group(1) or "", int(m.group(1)) if (m.group(1) or "").isdigit() else 5)
                return self.idees(suite_originale(commande, m, 2) or "mes thèmes habituels", min(n, 10))
            m = re.search(r"\b(?:ecris|redige|prepare|genere)(?:[- ]moi)? (?:un |le )?script(?: de (\d+) ?(?:secondes?|s|minutes?|min))?(?: sur| pour| autour de)?\s*(.*)", c)
            if m:
                d = int(m.group(1)) if m.group(1) else 60
                if re.search(r"minutes?|min\b", c) and m.group(1):
                    d *= 60
                return self.script(suite_originale(commande, m, 2) or "mon prochain sujet", d)
            m = re.search(r"\bajoute (?:l'idee |la video |l'idee de video )?(.+?) (?:au|dans le) pipeline\b", texte, re.IGNORECASE)
            if m:
                self.ajouter_idee(m.group(1).strip(" :"))
                self.kx.extensions.sons.jouer("succes")
                return f"« {m.group(1).strip(' :')} » ajouté au pipeline, étape idée."
            m = re.search(r"\bpasse (?:la video |l'idee )?(.+?) (?:en|au|a l'etape|a) (idee|script|tournage|montage|publie|publiee)\b", c)
            if m:
                return self.changer_etape(m.group(1).strip(), ALIAS_ETAPES.get(m.group(2), "idee"))
            m = re.search(r"\b(?:j'ai|on a|c'est) (?:fini|termine|tourne|monte|publie|poste)\s+(?:de |le |la |l'|mon |ma )?(?:tournage|montage|script)?\s*(?:de |du |d')?(.+)", c)
            if m and self.trouver(m.group(1).strip()):
                verbe = re.search(r"(tourne|monte|publie|poste|fini|termine)", c).group(1)
                etat_suivant = {"tourne": "montage", "monte": "publie", "publie": "publie", "poste": "publie"}.get(verbe)
                if etat_suivant:
                    return self.changer_etape(m.group(1).strip(), etat_suivant)
            if re.search(r"\b(ou j'en suis|ou en suis[- ]je|etat de mes videos|mon pipeline|point sur mes videos|avancement)\b", c):
                return self.ou_en_suis_je()
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Contenu : {e}."
        return None

    def statut(self) -> dict:
        return {"yt_dlp": self.ytdlp_disponible(), "vault": (self.db.une("SELECT COUNT(*) AS n FROM vault") or {}).get("n", 0),
                "pipeline": (self.db.une("SELECT COUNT(*) AS n FROM pipeline WHERE etape!='publie'") or {}).get("n", 0)}

    def outils(self) -> list[Outil]:
        return [
            Outil("contenu_pipeline", "Où j'en suis sur mes vidéos", lambda a: self.ou_en_suis_je(), {}, (), 1, True, "contenu"),
            Outil("contenu_vault_ajouter", "Ajouter un lien au vault", lambda a: self.ajouter_au_vault(a["url"]),
                  {"url": {"type": "string"}}, ("url",), 1, False, "contenu"),
        ]
