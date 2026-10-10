"""Outils de test : faux noyau léger, faux serveurs HTTP, base temporaire."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
TMP = tempfile.mkdtemp(prefix="kxtest_")
import noyau.config as _cfg  # noqa: E402
_cfg.DOSSIER_DONNEES = TMP          # AVANT d'importer les extensions : aucune écriture dans le vrai dossier


class FauxMemoire:
    def __init__(self):
        self.profil, self.journal, self.messages = {}, [], []

    def journaliser(self, type_, detail=""):
        self.journal.append((type_, detail))

    def definir_profil(self, cle, valeur):
        self.profil[cle.lower()] = valeur

    def tout_le_profil(self):
        return dict(self.profil)

    def oublier_profil(self, cle):
        self.profil.pop(cle.lower(), None)

    def ajouter_message(self, *a, **k):
        self.messages.append(a)

    def rechercher_documents(self, *a, **k):
        return []


class FauxCerveau:
    def __init__(self):
        self.disponible = True
        self.derniere_erreur = ""
        self.reponses = []           # file de réponses scriptées
        self.appels = []

    def verifier_connexion(self):
        return True

    def reflechir(self, texte, contexte_documents="", registre=None, prive=False):
        self.appels.append({"texte": texte, "prive": prive})
        return self.reponses.pop(0) if self.reponses else "Réponse simulée."


class FauxVoix:
    def __init__(self):
        self.dit = []
        self.voix_edge = "fr-FR-RemyMultilingualNeural"
        self.stt_local = None

    def parler(self, texte, *a, **k):
        self.dit.append(texte)


class FauxVision:
    def ocr_disponible(self): return True
    def vision_disponible(self): return False
    def extraire_texte(self, chemin): return "Erreur : ModuleNotFoundError: No module named 'foo'"
    def decrire(self, *a): return "description"
    def analyser(self, *a): return {}
    def rapport_parle(self, r): return "rapport"


class FauxKx:
    nom = "Kryptonix"

    def __init__(self, reset=True, **config):
        from noyau.extensions import Extensions
        if reset:                     # chaque test repart d'un état propre
            for f in ("permissions.json", "extensions.db", "finances.db"):
                try:
                    os.remove(os.path.join(TMP, f))
                except OSError:
                    pass
        self.config = {"simulation_systeme": True, "sons_actifs": False, "nom_utilisateur": "Test", "mot_reveil": "kryptonix",
                       "delai_securite_n3": 0.2, **config}
        self.memoire, self.cerveau, self.voix, self.vision = FauxMemoire(), FauxCerveau(), FauxVoix(), FauxVision()
        self.diffuses, self._abonnes = [], []
        self.etat = "veille"
        self.documents = self.comprehension = None
        self.extensions = Extensions(self)
        self.cerveau.extensions = self.extensions

    def abonner(self, f): self._abonnes.append(f)
    def _diffuser(self, ev, d): self.diffuses.append((ev, d))
    def _definir_etat(self, e): self.etat = e
    def recharger_config(self, modif): self.config.update(modif)

    def traiter(self, texte, source="", vocaliser=True):
        r = self.extensions.intercepter(texte)
        if r is None:
            r = self.extensions.router(texte, texte.lower().strip())
        return r if r is not None else "(rien : le noyau d'origine reprendrait la main)"


class Mock:
    """Faux serveur HTTP : routes[(METHODE, chemin)] = fonction(requete) -> (code, corps, type)."""

    def __init__(self):
        self.routes, self.requetes = {}, []
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a): pass

            def _go(self):
                u = urlparse(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                corps = self.rfile.read(n) if n else b""
                req = {"methode": self.command, "chemin": u.path, "q": {k: v[0] for k, v in parse_qs(u.query).items()},
                       "corps": corps, "entetes": dict(self.headers), "url": self.path}
                try:
                    req["json"] = json.loads(corps) if corps else None
                except ValueError:
                    req["json"] = None
                req["form"] = {k: v[0] for k, v in parse_qs(corps.decode("utf-8", "replace")).items()} if corps else {}
                mock.requetes.append(req)
                f = mock.routes.get((self.command, u.path))
                if f is None:
                    for (m, prefixe), g in mock.routes.items():
                        if m == self.command and prefixe.endswith("*") and u.path.startswith(prefixe[:-1]):
                            f = g
                            break
                code, donnees, type_ = f(req) if f else (404, {"erreur": "non mocké"}, "application/json")
                brut = (json.dumps(donnees) if not isinstance(donnees, (str, bytes)) else donnees)
                brut = brut.encode() if isinstance(brut, str) else brut
                self.send_response(code)
                self.send_header("Content-Type", type_ or "application/json")
                self.send_header("Content-Length", str(len(brut)))
                self.end_headers()
                self.wfile.write(brut)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _go

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self): return f"http://127.0.0.1:{self.port}"
    @property
    def hote(self): return f"127.0.0.1:{self.port}"

    def ok(self, methode, chemin, donnees, code=200, type_="application/json"):
        self.routes[(methode, chemin)] = lambda r: (code, donnees, type_)

    def arreter(self):
        self.srv.shutdown(); self.srv.server_close()
