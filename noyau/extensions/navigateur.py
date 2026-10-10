"""
KRYPTONIX / noyau/extensions/navigateur.py
===========================================
Web, recherche et navigateur piloté.

  • Recherche contextuelle (DuckDuckGo, sans clé) : « renseigne-toi sur ... » → réponse synthétisée.
  • Lecture d'une page : « résume cette page https://... ».
  • Navigateur piloté (Playwright, ton vrai Chrome) : résumé / traduction de l'onglet actif,
    gestion des onglets, clic et saisie dans les pages.
  • Réservation assistée (restaurant, rendez-vous) : j'ouvre la page, je remplis ce que je peux,
    et JE NE VALIDE JAMAIS seul : la validation finale est une action de niveau 3.

Pour piloter ton Chrome : dis « lance Chrome pilotable » (il s'ouvre avec un profil dédié :
connecte-toi une fois à tes sites, la session est conservée), ou lance Chrome avec
`--remote-debugging-port=9222`. Playwright doit être installé (`pip install playwright`) ;
aucun navigateur n'est téléchargé : on se branche sur Chrome/Edge déjà installé.

HONNÊTETÉ : les sites de réservation changent sans cesse ; le remplissage automatique est
heuristique (champs date, heure, personnes, nom, téléphone). Quand il échoue, je te le dis et
je te laisse la main sur la page déjà ouverte.
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote_plus, urlparse

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, http, liste_parlee, normaliser, suite_originale, texte_http

LOG = logging.getLogger("navigateur")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


def html_vers_texte(html: str, limite: int = 12000) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html_lib.unescape(html)).strip()[:limite]


def url_valide(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)


# ---------------------------------------------------------------------------
#  Recherche DuckDuckGo
# ---------------------------------------------------------------------------
def rechercher_web(requete: str, maxi: int = 5, base: str = "https://html.duckduckgo.com/html/") -> list[dict]:
    r = http("GET", base, params={"q": requete, "kl": "fr-fr"}, timeout=12,
             headers={"User-Agent": "Mozilla/5.0 (Kryptonix assistant)"})
    if r.status_code != 200:
        raise ErreurModule(f"le moteur de recherche a répondu {r.status_code}")
    corps = texte_http(r)
    liens = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', corps, re.S)
    extraits = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', corps, re.S)
    sortie = []
    for i, (href, titre) in enumerate(liens[:maxi]):
        url = href
        if "uddg=" in href:
            url = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query).get("uddg", [href])[0]
        sortie.append({"titre": html_vers_texte(titre, 160), "url": url,
                       "extrait": html_vers_texte(extraits[i], 300) if i < len(extraits) else ""})
    return sortie


# ---------------------------------------------------------------------------
#  Pilote Playwright (un thread dédié : l'API synchrone est liée à son thread)
# ---------------------------------------------------------------------------
class Pilote:
    def __init__(self, config: dict):
        self.config = config
        self._file: "queue.Queue" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._pw = None
        self._navigateur = None
        self.erreur = ""

    @property
    def dispo(self) -> bool:
        return sync_playwright is not None

    @property
    def point_cdp(self) -> str:
        return str(self.config.get("navigateur_cdp", "http://127.0.0.1:9222"))

    @property
    def profil(self) -> str:
        return os.path.join(DOSSIER_DONNEES, "chrome_profil")

    # -- thread ---------------------------------------------------------
    def _boucle(self) -> None:
        while True:
            fn, retour = self._file.get()
            if fn is None:
                break
            try:
                retour.put((True, fn()))
            except Exception as e:      # remonte l'erreur à l'appelant
                retour.put((False, e))

    def executer(self, fn, delai: float = 40.0):
        if not self.dispo:
            raise ErreurModule("Playwright n'est pas installé (pip install playwright)")
        if self._thread is None:
            self._thread = threading.Thread(target=self._boucle, daemon=True, name="kryptonix-playwright")
            self._thread.start()
        retour: "queue.Queue" = queue.Queue()
        self._file.put((fn, retour))
        try:
            ok, valeur = retour.get(timeout=delai)
        except queue.Empty:
            raise ErreurModule("le navigateur ne répond pas") from None
        if not ok:
            if isinstance(valeur, ErreurModule):
                raise valeur
            raise ErreurModule(f"le navigateur a échoué ({type(valeur).__name__})") from None
        return valeur

    # -- connexion --------------------------------------------------------
    def _chemin_chrome(self) -> str | None:
        if self.config.get("chrome_chemin") and os.path.exists(self.config["chrome_chemin"]):
            return self.config["chrome_chemin"]
        candidats = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        for c in candidats:
            if os.path.exists(c):
                return c
        return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("chrome")

    def lancer_chrome(self) -> str:
        chrome = self._chemin_chrome()
        if not chrome:
            raise ErreurModule("je ne trouve pas Chrome ou Edge sur cette machine")
        port = urlparse(self.point_cdp).port or 9222
        os.makedirs(self.profil, exist_ok=True)
        subprocess.Popen([chrome, f"--remote-debugging-port={port}", f"--user-data-dir={self.profil}",
                          "--no-first-run", "--no-default-browser-check"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            try:
                if http("GET", self.point_cdp + "/json/version", timeout=1).status_code == 200:
                    return "Chrome pilotable est lancé."
            except ErreurModule:
                time.sleep(0.5)
        raise ErreurModule("Chrome ne s'est pas ouvert en mode pilotable")

    def _contexte(self):
        """À appeler depuis le thread Playwright."""
        if self._navigateur is None or not self._navigateur.is_connected():
            if self._pw is None:
                self._pw = sync_playwright().start()
            try:
                self._navigateur = self._pw.chromium.connect_over_cdp(self.point_cdp)
            except Exception:
                raise ErreurModule("je ne suis pas branché sur ton navigateur : dis « lance Chrome pilotable »") from None
        contextes = self._navigateur.contexts
        return contextes[0] if contextes else self._navigateur.new_context()

    # -- opérations (toutes exécutées dans le thread Playwright) ---------
    def onglets(self) -> list[dict]:
        def f():
            return [{"i": i, "titre": p.title(), "url": p.url} for i, p in enumerate(self._contexte().pages)]
        return self.executer(f)

    def _page_active(self):
        pages = self._contexte().pages
        if not pages:
            raise ErreurModule("aucun onglet ouvert")
        for p in reversed(pages):
            try:
                if p.evaluate("document.visibilityState") == "visible":
                    return p
            except Exception:
                continue
        return pages[-1]

    def texte_onglet_actif(self) -> dict:
        def f():
            p = self._page_active()
            return {"titre": p.title(), "url": p.url, "texte": html_vers_texte(p.content())}
        return self.executer(f)

    def ouvrir(self, url: str) -> str:
        if not url_valide(url):
            raise ErreurModule("adresse invalide")

        def f():
            p = self._contexte().new_page()
            p.goto(url, wait_until="domcontentloaded", timeout=25000)
            return p.title()
        return self.executer(f, 45)

    def aller_a(self, motif: str) -> str:
        m = normaliser(motif)

        def f():
            for p in self._contexte().pages:
                if m in normaliser(p.title() + " " + p.url):
                    p.bring_to_front()
                    return p.title()
            raise ErreurModule(f"aucun onglet ne correspond à « {motif} »")
        return self.executer(f)

    def fermer(self, motif: str | None) -> int:
        def f():
            n = 0
            pages = self._contexte().pages
            cibles = [self._page_active()] if not motif else \
                [p for p in pages if normaliser(motif) in normaliser(p.title() + " " + p.url)]
            for p in cibles:
                p.close()
                n += 1
            return n
        return self.executer(f)

    def cliquer(self, texte: str) -> str:
        def f():
            p = self._page_active()
            cible = p.get_by_text(texte, exact=False).first
            cible.click(timeout=6000)
            return p.title()
        return self.executer(f)

    def saisir(self, champ: str, valeur: str) -> bool:
        def f():
            p = self._page_active()
            for loc in (p.get_by_label(champ, exact=False), p.get_by_placeholder(champ, exact=False)):
                if loc.count():
                    loc.first.fill(valeur, timeout=4000)
                    return True
            return False
        return self.executer(f)

    def defiler(self, bas: bool = True) -> None:
        self.executer(lambda: self._page_active().mouse.wheel(0, 700 if bas else -700))

    def fermer_tout(self) -> None:
        def f():
            if self._navigateur:
                try:
                    self._navigateur.close()
                except Exception:
                    pass
            if self._pw:
                self._pw.stop()
        if self._thread:
            try:
                self.executer(f, 5)
            except ErreurModule:
                pass
            self._file.put((None, None))


# ---------------------------------------------------------------------------
#  Module
# ---------------------------------------------------------------------------
class Navigateur(Module):
    nom = "navigateur"
    description = "Recherche web, résumé et pilotage du navigateur, réservations assistées"
    exemples = ("renseigne-toi sur le télescope James Webb", "résume cet onglet", "traduis cette page",
                "ferme les onglets youtube", "réserve une table pour 2 demain à 20h chez Chez Paul")

    def __init__(self, kx):
        super().__init__(kx)
        self.pilote = Pilote(self.config)
        self._reservation: dict | None = None

    def arreter(self) -> None:
        self.pilote.fermer_tout()

    # -- recherche --------------------------------------------------------
    def chercher_et_repondre(self, question: str) -> str:
        resultats = rechercher_web(question, 5, self.config.get("moteur_recherche_url", "https://html.duckduckgo.com/html/"))
        if not resultats:
            return "La recherche n'a rien donné."
        self.kx.memoire.journaliser("recherche_web", question[:80])
        sources = " | ".join(f"{r['titre']}" for r in resultats[:3])
        if self.kx.cerveau.disponible or self.config.get("mode_ia", "local") != "local" or self.kx.cerveau.verifier_connexion():
            blocs = "\n".join(f"- {r['titre']} : {r['extrait']}" for r in resultats)
            texte = self.kx.cerveau.reflechir(
                f"Question : {question}\nVoici des extraits de résultats web. Réponds en français en 2 à 4 phrases, "
                f"uniquement à partir de ces extraits, sans inventer. Si ce n'est pas suffisant, dis-le.\n{blocs}",
                registre="technique")
            if not self.kx.cerveau.derniere_erreur and texte:
                return f"{texte} (Sources : {sources}.)"
        return "Voici ce que j'ai trouvé : " + " ; ".join(f"{r['titre']} — {r['extrait'][:120]}" for r in resultats[:3])

    def resumer_url(self, url: str, traduire: bool = False) -> str:
        if not url_valide(url):
            raise ErreurModule("adresse invalide")
        r = http("GET", url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Kryptonix assistant)"})
        if r.status_code != 200:
            raise ErreurModule(f"le site a répondu {r.status_code}")
        return self._resumer_texte(html_vers_texte(texte_http(r)), url, traduire)

    def _resumer_texte(self, texte: str, nom: str, traduire: bool = False) -> str:
        if not texte:
            raise ErreurModule("la page ne contient pas de texte lisible")
        if not (self.kx.cerveau.disponible or self.kx.cerveau.verifier_connexion() or self.config.get("mode_ia", "local") != "local"):
            return "Début de la page : " + texte[:400]
        consigne = ("Traduis en français l'essentiel de ce texte (en 5 à 8 phrases)." if traduire
                    else "Résume en français, en 3 à 5 phrases claires, ce texte de page web.")
        rep = self.kx.cerveau.reflechir(f"{consigne}\n\n{nom}\n{texte[:6000]}", registre="technique")
        return rep if not self.kx.cerveau.derniere_erreur else "Début de la page : " + texte[:400]

    # -- réservation ------------------------------------------------------
    def preparer_reservation(self, c: str, texte: str) -> str:
        from .agenda import parse_date_heure
        debut, avec_heure, reste = parse_date_heure(c)
        personnes = re.search(r"\b(?:pour )?(\d{1,2}|un|une|deux|trois|quatre|cinq|six|sept|huit) (?:personnes?|couverts?|convives?)\b", c)
        nb = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8}
        n = int(personnes.group(1)) if personnes and personnes.group(1).isdigit() else (nb.get(personnes.group(1)) if personnes else 2)
        lieu = re.search(r"\b(?:chez|au|a l'|à l')\s+([A-ZÀ-Ý][\wÀ-ÿ' -]{2,40})", texte)
        nom = lieu.group(1).strip() if lieu else ""
        if not nom or not debut or not avec_heure:
            return ("Pour réserver, donne-moi le nom de l'établissement, le jour, l'heure et le nombre de personnes, "
                    "par exemple : « réserve une table pour 2 demain à 20h chez Chez Paul ».")
        self._reservation = {"nom": nom, "debut": debut, "personnes": n}
        url = f"https://www.thefork.fr/search?cityId=&queryText={quote_plus(nom)}"
        if self.config.get("reservation_url"):
            url = self.config["reservation_url"].format(nom=quote_plus(nom))
        try:
            self.pilote.ouvrir(url)
        except ErreurModule as e:
            return f"Réservation : {e}. Je garde les détails : {nom}, {n} personnes, {debut:%d/%m} à {debut:%Hh%M}."
        remplis = []
        for champ, valeur in (("personnes", str(n)), ("date", debut.strftime("%d/%m/%Y")), ("heure", debut.strftime("%H:%M"))):
            try:
                if self.pilote.saisir(champ, valeur):
                    remplis.append(champ)
            except ErreurModule:
                break
        self.kx.extensions.sons.jouer("scan")
        bilan = ("J'ai rempli " + liste_parlee(remplis) + ". ") if remplis else \
            "Je n'ai pas pu remplir les champs automatiquement sur cette page. "
        return (f"J'ai ouvert la recherche pour {nom} ({n} personnes, {debut:%d/%m} à {debut:%Hh%M}). {bilan}"
                "Vérifie la page ; dis « valide la réservation » quand tu veux que je clique sur la confirmation.")

    def valider_reservation(self) -> str:
        for libelle in ("Confirmer la réservation", "Réserver", "Confirmer", "Valider"):
            try:
                self.pilote.cliquer(libelle)
                self.kx.extensions.sons.jouer("succes")
                return f"J'ai cliqué sur « {libelle} »."
            except ErreurModule:
                continue
        raise ErreurModule("je ne trouve pas de bouton de confirmation sur la page, finis-la à la main")

    # -- voix -------------------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        try:
            m = re.search(r"(https?://\S+)", texte)
            if m and re.search(r"\b(resume|resumer|lis|traduis|traduire)\b", c):
                return self.resumer_url(m.group(1).rstrip(".,)"), traduire="tradui" in c)
            if re.search(r"\blance (?:le )?(?:chrome|navigateur) pilotable\b|\bbranche[- ]toi (?:sur|a) (?:chrome|mon navigateur)\b", c):
                return self.pilote.lancer_chrome()
            if re.search(r"\b(resume|resumer|traduis|traduire)\b.*\b(onglet|page)\b|\b(onglet|page)\b.*\b(resume|traduis)\b", c):
                d = self.pilote.texte_onglet_actif()
                return self._resumer_texte(d["texte"], d["titre"], traduire=bool(re.search(r"\btradui", c)))
            if re.search(r"\b(quels|liste|mes) onglets\b|\bonglets ouverts\b", c):
                o = self.pilote.onglets()
                return f"{len(o)} onglet(s) : " + liste_parlee([x["titre"][:40] or x["url"] for x in o]) + "." if o else "Aucun onglet."
            m = re.search(r"\bferme (?:tous )?les onglets? (.+)", c)
            if m:
                n = self.pilote.fermer(m.group(1).strip())
                return f"{n} onglet(s) fermé(s)."
            if re.search(r"\bferme (?:cet|l'|l’)?onglet\b", c):
                self.pilote.fermer(None)
                return "Onglet fermé."
            m = re.search(r"\b(?:va|passe|bascule|montre)(?: moi)? (?:sur|a|vers) l'onglet (.+)", c)
            if m:
                return f"Onglet « {self.pilote.aller_a(m.group(1).strip())} » au premier plan."
            m = re.search(r"\bclique (?:sur )?(.+)", texte, re.IGNORECASE)
            if m and "onglet" not in c and re.search(r"\b(page|bouton|lien|sur)\b", c):
                self.pilote.cliquer(m.group(1).strip(" ."))
                self.kx.extensions.sons.jouer("clic")
                return "Cliqué."
            m = re.search(r"\b(?:tape|ecris|remplis|saisis)\s+(.+?)\s+dans (?:le champ |la case )?(.+)", texte, re.IGNORECASE)
            if m and self.pilote.dispo:
                ok = self.pilote.saisir(m.group(2).strip(" ."), m.group(1).strip())
                return "C'est saisi." if ok else "Je ne trouve pas ce champ sur la page."
            if re.search(r"\bdescends?\b.*\b(page)\b|\bscrolle? (?:vers le )?bas\b", c):
                self.pilote.defiler(True)
                return "Page défilée."
            if re.search(r"\bremonte\b.*\bpage\b|\bscrolle? (?:vers le )?haut\b", c):
                self.pilote.defiler(False)
                return "Page remontée."
            if re.search(r"\bvalide la reservation\b|\bconfirme la reservation\b", c):
                return self.securise("reservation_valider", {}, description="valider la réservation sur la page ouverte")
            if re.search(r"\b(reserve|reserver|prends? une reservation|reservation)\b.*\b(table|restaurant|chez|rendez-vous|resto)\b|\breserve (?:moi )?(?:une table|un resto)\b", c):
                return self.preparer_reservation(c, texte)
            m = re.search(r"\b(?:renseigne[- ]toi sur|recherche (?:sur le web|en ligne|sur internet)|cherche (?:sur le web|en ligne|sur internet|sur duckduckgo)|"
                          r"fais une recherche (?:sur|a propos de)|que dit le web sur)\s*:?\s*(.+)", c)
            if m:
                return self.chercher_et_repondre(suite_originale(commande, m, 1).strip(" ?."))
        except ErreurModule as e:
            self.kx.extensions.sons.jouer("erreur")
            return f"Navigateur : {e}."
        return None

    def statut(self) -> dict:
        return {"playwright": self.pilote.dispo, "cdp": self.pilote.point_cdp}

    def outils(self) -> list[Outil]:
        return [
            Outil("web_recherche", "Rechercher sur le web et synthétiser", lambda a: self.chercher_et_repondre(a["question"]),
                  {"question": {"type": "string"}}, ("question",), 1, False, "web"),
            Outil("web_resumer_url", "Résumer une page web", lambda a: self.resumer_url(a["url"]),
                  {"url": {"type": "string"}}, ("url",), 1, False, "web"),
            Outil("reservation_valider", "Valider une réservation sur la page ouverte", lambda a: self.valider_reservation(),
                  {}, (), 3, False, "web"),
        ]
