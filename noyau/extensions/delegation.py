"""
KRYPTONIX / noyau/extensions/delegation.py
===========================================
Délégation à un agent de réflexion local (type Hermes) — séparation stricte :

    L'AGENT RÉFLÉCHIT (analyse, recherche, plan, brouillon).
    KRYPTONIX EXÉCUTE : il détient les clés et le corps.

L'agent n'a accès à aucun outil ni à aucune donnée sensible : il reçoit une question (et un
contexte non confidentiel) et répond par du texte. Quand il propose des actions, il les écrit
sous la forme `ACTION {"outil": "...", "args": {...}}` ; Kryptonix les valide (outil connu,
domaine autorisé, jamais de finances), puis les fait passer par le coffre de sécurité N1/N2/N3 :
une action sensible demandera TA confirmation, comme si tu l'avais demandée toi-même.

Réglages : agent_modele ("hermes3" par défaut), agent_url (API compatible OpenAI ; vide = Ollama
local), agent_actions (true : l'agent peut proposer des actions ; false : réflexion seule).
"""

from __future__ import annotations

import json
import logging
import re
import threading

from .base import ErreurModule, Module, Outil, http, liste_parlee, normaliser

LOG = logging.getLogger("delegation")
JAMAIS = {"finances", "mail", "telephone", "ecran", "social"}

CONSIGNE = """Tu es un agent de réflexion. Tu ANALYSES, tu PLANIFIES, tu PROPOSES. Tu n'exécutes rien toi-même.
Réponds en français, de façon structurée et concise (10 lignes maximum).
{actions}"""
CONSIGNE_ACTIONS = """Si une action concrète est utile, ajoute en fin de réponse, une par ligne (3 maximum) :
ACTION {{"outil": "<nom>", "args": {{...}}}}
Outils que tu peux proposer (Kryptonix décidera et demandera confirmation si nécessaire) :
{outils}"""


class Delegation(Module):
    nom = "delegation"
    description = "Délégation à un agent de réflexion local (Hermes)"
    exemples = ("demande à Hermes de préparer mon week-end", "réfléchis en profondeur à mon planning de la semaine")

    def __init__(self, kx):
        super().__init__(kx)
        self.derniere = {}

    def modele(self) -> str:
        return self.config.get("agent_modele") or "hermes3"

    def _outils_proposables(self):
        return [o for o in self.kx.extensions.outils() if o.domaine not in JAMAIS and o.mcp]

    def _appeler(self, question: str, contexte: str) -> str:
        outils = self._outils_proposables()
        actions = CONSIGNE_ACTIONS.format(outils="\n".join(f"- {o.nom} : {o.description}" for o in outils)) \
            if self.config.get("agent_actions", True) and outils else "Ne propose aucune action."
        messages = [{"role": "system", "content": CONSIGNE.format(actions=actions)},
                    {"role": "user", "content": (f"Contexte : {contexte}\n\n" if contexte else "") + question}]
        url = (self.config.get("agent_url") or "").rstrip("/")
        delai = float(self.config.get("agent_timeout", 180))
        if url:
            r = http("POST", f"{url}/chat/completions", json={"model": self.modele(), "messages": messages}, timeout=delai,
                     headers={"Authorization": f"Bearer {self.config.get('agent_cle', 'local')}"})
            if r.status_code >= 400:
                raise ErreurModule(f"l'agent a répondu {r.status_code}")
            try:
                return r.json()["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, ValueError):
                raise ErreurModule("réponse illisible de l'agent") from None
        hote = self.config.get("ollama_hote", "http://localhost:11434").rstrip("/")
        r = http("POST", f"{hote}/api/chat", json={"model": self.modele(), "messages": messages, "stream": False}, timeout=delai)
        if r.status_code == 404:
            raise ErreurModule(f"le modèle « {self.modele()} » n'est pas installé (ollama pull {self.modele()})")
        if r.status_code >= 400:
            raise ErreurModule(f"l'agent a répondu {r.status_code}")
        return (r.json().get("message", {}).get("content", "") or "").strip()

    @staticmethod
    def extraire_actions(texte: str) -> tuple[str, list[dict]]:
        actions, gardees = [], []
        for ligne in texte.splitlines():
            m = re.match(r"\s*ACTION\s*(\{.*\})\s*$", ligne)
            if m:
                try:
                    a = json.loads(m.group(1))
                    if isinstance(a, dict) and isinstance(a.get("outil"), str):
                        actions.append({"outil": a["outil"], "args": a.get("args") if isinstance(a.get("args"), dict) else {}})
                        continue
                except ValueError:
                    pass
            gardees.append(ligne)
        return "\n".join(gardees).strip(), actions[:3]

    def executer_propositions(self, actions: list[dict]) -> list[str]:
        """Chaque action passe par le coffre : validation de l'outil, puis niveau N1/N2/N3."""
        valides = {o.nom for o in self._outils_proposables()}
        sorties = []
        for a in actions:
            if a["outil"] not in valides:
                sorties.append(f"(proposition ignorée : « {a['outil']} » n'est pas autorisé)")
                continue
            sorties.append(self.kx.extensions.securite.lancer(a["outil"], a["args"],
                                                              description=f"{a['outil']} (proposé par l'agent)"))
            if self.kx.extensions.securite.en_attente:
                break                       # on attend ta réponse avant la suite
        return sorties

    def deleguer(self, question: str, contexte: str = "") -> tuple[str, list[dict]]:
        brut = self._appeler(question, contexte)
        texte, actions = self.extraire_actions(brut)
        self.derniere = {"question": question, "reponse": texte, "actions": actions}
        return texte, actions

    def deleguer_en_fond(self, question: str) -> str:
        def travail():
            try:
                texte, actions = self.deleguer(question)
                suite = ""
                if actions:
                    suite = " " + " ".join(self.executer_propositions(actions))
                msg = f"L'agent a terminé : {texte}{suite}"
            except ErreurModule as e:
                msg = f"L'agent n'a pas pu répondre : {e}."
            self.kx.extensions.sons.jouer("notification")
            self.kx._diffuser("message", {"role": "assistant", "contenu": msg, "source": "agent"})
            self.kx.memoire.ajouter_message("assistant", msg, "agent", "neutre")
            self.kx.voix.parler(msg[:600])
        threading.Thread(target=travail, daemon=True, name="kryptonix-agent").start()
        return f"Je confie ça à {self.modele()}. Je te préviens dès qu'il a terminé."

    def intention(self, texte: str, commande: str) -> str | None:
        m = re.search(r"\b(?:demande a|delegue a|confie a|fais reflechir)\s+(?:hermes|l'agent|ton agent|mon agent)\s*(?:de |d'|:)?\s*(.+)", normaliser(texte))
        if not m:
            m = re.search(r"\b(?:reflechis en profondeur (?:a|sur)|analyse en profondeur|fais un plan detaille (?:pour|de))\s*(.+)", normaliser(texte))
        if not m:
            return None
        return self.deleguer_en_fond(m.group(1).strip())

    def statut(self) -> dict:
        return {"modele": self.modele(), "url": self.config.get("agent_url") or "ollama local",
                "actions": bool(self.config.get("agent_actions", True))}

    def outils(self) -> list[Outil]:
        return [Outil("agent_deleguer", "Confier une réflexion de fond à l'agent local",
                      lambda a: self.deleguer_en_fond(a["question"]), {"question": {"type": "string"}}, ("question",), 1, False, "agent")]
