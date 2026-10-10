"""
KRYPTONIX / noyau/extensions/routeur_llm.py
============================================
Routage du cerveau : trois modes, fournisseur indépendant de la voix.

  local    Tout reste sur ta machine (Ollama). Gratuit, hors-ligne. (défaut)
  hybride  IA cloud « du quotidien » (rapide, économique) pour la conversation ;
           l'agent local garde tout ce qui est privé (documents, résumés).
  qualite  Le modèle cloud le plus puissant configuré.

Suivi des coûts (par appel, par mois), plafond de budget mensuel et BASCULE AUTOMATIQUE en
local dès que le plafond est atteint. En cas de panne du cloud, retour local immédiat.

Confidentialité : les extraits de documents ingérés ne partent JAMAIS vers le cloud, sauf si
tu actives `cloud_documents` dans la config.

Fournisseurs : anthropic (API Messages) et openai (toute API compatible : OpenAI, OpenRouter,
Groq, Mistral, LM Studio... via `openai_base_url`).
"""

from __future__ import annotations

import logging
import os
import time

from ..config import DOSSIER_DONNEES
from .base import ErreurModule, Module, Outil, http, normaliser
from .stockage import Stockage

LOG = logging.getLogger("routeur")

MODES = ("local", "hybride", "qualite")

# Tarifs indicatifs en dollars par million de jetons (entrée, sortie). À ADAPTER dans la config
# (`prix_par_million`) selon ton fournisseur : ils servent uniquement à estimer ta dépense.
PRIX_PAR_DEFAUT = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-opus-5-5": (5.0, 25.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.5, 10.0),
}
PRIX_INCONNU = (3.0, 15.0)   # prudent : on surestime plutôt que de sous-estimer

SCHEMA = """
CREATE TABLE IF NOT EXISTS couts_api (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, mois TEXT NOT NULL,
    fournisseur TEXT, modele TEXT, jetons_in INTEGER, jetons_out INTEGER, cout REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_couts_mois ON couts_api(mois);
"""


class RouteurLLM(Module):
    nom = "ia"
    description = "Modes local / hybride / qualité, coûts et budget"
    exemples = ("passe en mode hybride", "mode qualité", "combien j'ai dépensé", "reste en local")

    def __init__(self, kx):
        super().__init__(kx)
        self.db = Stockage(os.path.join(DOSSIER_DONNEES, "extensions.db"), SCHEMA)
        self.derniere_erreur = ""
        self.bascule_budget = False          # True : plafond atteint, tout passe en local
        self._alerte_budget_dite = ""
        self.dernier_moteur = "local"

    # -- configuration ----------------------------------------------------
    @property
    def mode(self) -> str:
        m = str(self.config.get("mode_ia", "local")).lower()
        return m if m in MODES else "local"

    def definir_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("mode inconnu")
        self.kx.recharger_config({"mode_ia": mode})
        self.bascule_budget = False

    def _cle(self, fournisseur: str) -> str:
        cles = self.config.get("cles_api") or {}
        env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(fournisseur, "")
        return (cles.get(fournisseur) or os.environ.get(env, "")).strip()

    @property
    def fournisseur(self) -> str:
        return str(self.config.get("fournisseur_llm", "anthropic")).lower()

    def modele_pour(self, qualite: bool) -> str:
        if qualite:
            return self.config.get("llm_modele_qualite") or "claude-opus-5-5"
        return self.config.get("llm_modele_quotidien") or "claude-haiku-4-5-20251001"

    def cloud_pret(self) -> bool:
        return bool(self._cle(self.fournisseur))

    # -- coûts ------------------------------------------------------------
    def _prix(self, modele: str) -> tuple[float, float]:
        table = dict(PRIX_PAR_DEFAUT)
        for k, v in (self.config.get("prix_par_million") or {}).items():
            try:
                table[k] = (float(v[0]), float(v[1]))
            except (TypeError, ValueError, IndexError):
                pass
        return table.get(modele, PRIX_INCONNU)

    def enregistrer_cout(self, modele: str, j_in: int, j_out: int) -> float:
        p_in, p_out = self._prix(modele)
        cout = (j_in * p_in + j_out * p_out) / 1_000_000
        self.db.executer(
            "INSERT INTO couts_api (ts, mois, fournisseur, modele, jetons_in, jetons_out, cout) VALUES (?,?,?,?,?,?,?)",
            (time.time(), time.strftime("%Y-%m"), self.fournisseur, modele, j_in, j_out, cout))
        return cout

    def depense_mois(self) -> float:
        r = self.db.une("SELECT COALESCE(SUM(cout),0) AS t FROM couts_api WHERE mois=?", (time.strftime("%Y-%m"),))
        return float(r["t"]) if r else 0.0

    def budget(self) -> float:
        try:
            return float(self.config.get("budget_mensuel", 5.0))
        except (TypeError, ValueError):
            return 5.0

    def plafond_atteint(self) -> bool:
        return self.budget() > 0 and self.depense_mois() >= self.budget()

    def bilan(self) -> dict:
        j = self.db.une("SELECT COUNT(*) AS n, COALESCE(SUM(jetons_in),0) AS i, COALESCE(SUM(jetons_out),0) AS o "
                        "FROM couts_api WHERE mois=?", (time.strftime("%Y-%m"),)) or {}
        return {"mode": self.mode, "fournisseur": self.fournisseur, "cloud_pret": self.cloud_pret(),
                "depense_mois": round(self.depense_mois(), 4), "budget": self.budget(),
                "plafond_atteint": self.plafond_atteint(), "appels": j.get("n", 0),
                "jetons_in": j.get("i", 0), "jetons_out": j.get("o", 0),
                "dernier_moteur": self.dernier_moteur, "erreur": self.derniere_erreur}

    # -- appel ------------------------------------------------------------
    def doit_utiliser_cloud(self, prive: bool, a_documents: bool) -> bool:
        if self.mode == "local" or prive or getattr(self.kx.extensions, "confidentiel_restant", 0) > 0:
            return False
        if a_documents and not self.config.get("cloud_documents", False):
            return False
        if not self.cloud_pret():
            return False
        if self.plafond_atteint():
            if not self.bascule_budget:
                self.bascule_budget = True
                self.kx.extensions.sons.jouer("budget")
                self.kx._diffuser("alerte", {"message": "Plafond de budget cloud atteint : retour en local."})
            return False
        return True

    def completer(self, messages: list[dict], prive: bool = False, a_documents: bool = False,
                  temperature: float = 0.7) -> str | None:
        """Texte du modèle cloud, ou None si on doit rester en local (le noyau prend le relais)."""
        if not self.doit_utiliser_cloud(prive, a_documents):
            self.dernier_moteur = "local"
            return None
        modele = self.modele_pour(self.mode == "qualite")
        try:
            if self.fournisseur == "anthropic":
                texte, j_in, j_out = self._anthropic(modele, messages, temperature)
            else:
                texte, j_in, j_out = self._openai(modele, messages, temperature)
        except ErreurModule as e:
            self.derniere_erreur = str(e)
            LOG.warning("cloud indisponible (%s) : bascule locale", e)
            self.dernier_moteur = "local (repli)"
            return None
        self.derniere_erreur = ""
        self.dernier_moteur = f"{self.fournisseur}:{modele}"
        self.enregistrer_cout(modele, j_in, j_out)
        return texte or None

    def _anthropic(self, modele, messages, temperature):
        systeme = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        corps = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        fusion = []                 # l'API exige l'alternance user/assistant
        for m in corps:
            if fusion and fusion[-1]["role"] == m["role"]:
                fusion[-1]["content"] += "\n" + m["content"]
            else:
                fusion.append(dict(m))
        if not fusion or fusion[0]["role"] != "user":
            fusion.insert(0, {"role": "user", "content": "(début de conversation)"})
        base = (self.config.get("anthropic_base_url") or "https://api.anthropic.com").rstrip("/")
        r = http("POST", f"{base}/v1/messages", timeout=float(self.config.get("cloud_timeout", 45)),
                 headers={"x-api-key": self._cle("anthropic"), "anthropic-version": "2023-06-01",
                          "content-type": "application/json"},
                 json={"model": modele, "max_tokens": int(self.config.get("cloud_max_jetons", 1024)),
                       "system": systeme, "messages": fusion, "temperature": min(1.0, temperature)})
        if r.status_code in (401, 403):
            raise ErreurModule("clé API refusée par le fournisseur")
        if r.status_code == 429:
            raise ErreurModule("quota du fournisseur dépassé")
        if r.status_code >= 400:
            raise ErreurModule(f"le fournisseur a répondu {r.status_code}")
        d = r.json()
        texte = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage", {})
        return texte.strip(), int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))

    def _openai(self, modele, messages, temperature):
        base = (self.config.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        r = http("POST", f"{base}/chat/completions", timeout=float(self.config.get("cloud_timeout", 45)),
                 headers={"Authorization": f"Bearer {self._cle('openai')}"},
                 json={"model": modele, "messages": messages, "temperature": temperature,
                       "max_tokens": int(self.config.get("cloud_max_jetons", 1024))})
        if r.status_code in (401, 403):
            raise ErreurModule("clé API refusée par le fournisseur")
        if r.status_code == 429:
            raise ErreurModule("quota du fournisseur dépassé")
        if r.status_code >= 400:
            raise ErreurModule(f"le fournisseur a répondu {r.status_code}")
        d = r.json()
        try:
            texte = d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ErreurModule("réponse inattendue du fournisseur") from None
        u = d.get("usage", {})
        return texte.strip(), int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))

    # -- voix / outils ----------------------------------------------------
    def intention(self, texte: str, commande: str) -> str | None:
        c = normaliser(commande)
        m = None
        import re
        if re.search(r"\b(mode|passe en|reste en|bascule en|utilise l'ia) ?(?:mode )?(local|hors ligne|hors-ligne)\b|\breste en local\b", c):
            self.definir_mode("local")
            self.kx.extensions.sons.jouer("ia_hybride")
            return "Mode local : tout reste sur ta machine."
        if re.search(r"\b(mode|passe en|bascule en) (?:mode )?hybride\b", c):
            if not self.cloud_pret():
                self.definir_mode("hybride")
                return ("Mode hybride réglé, mais aucune clé API n'est configurée : je reste en local "
                        "en attendant. Ajoute ta clé dans le panneau, section Intelligence.")
            self.definir_mode("hybride")
            self.kx.extensions.sons.jouer("ia_hybride")
            return "Mode hybride : cloud rapide pour la conversation, agent local pour le privé."
        if re.search(r"\b(mode|passe en|bascule en) (?:mode )?qualite\b", c):
            self.definir_mode("qualite")
            self.kx.extensions.sons.jouer("ia_hybride")
            return ("Mode qualité activé." if self.cloud_pret() else
                    "Mode qualité réglé, mais sans clé API je reste en local pour l'instant.")
        if re.search(r"\b(combien (?:j'ai|ai-je) depense|mes couts|budget (?:api|cloud|ia)|depense (?:api|cloud))\b", c):
            b = self.bilan()
            return (f"Ce mois-ci : {b['depense_mois']:.3f} dollars sur un plafond de {b['budget']:.2f}, "
                    f"{b['appels']} appel(s) cloud. Mode actuel : {b['mode']}.")
        if re.search(r"\b(quel mode|en quel mode|quel moteur)\b", c):
            b = self.bilan()
            return f"Mode {b['mode']}, dernier moteur utilisé : {b['dernier_moteur']}."
        return None

    def statut(self) -> dict:
        return self.bilan()

    def outils(self) -> list[Outil]:
        return [Outil("ia_definir_mode", "Changer le mode IA (local, hybride, qualité)",
                      lambda a: (self.definir_mode(a.get("mode", "local")) or f"Mode {self.mode}."),
                      {"mode": {"type": "string", "enum": list(MODES)}}, ("mode",), 1, False, "ia")]
