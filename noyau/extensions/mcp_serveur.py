"""
KRYPTONIX / noyau/extensions/mcp_serveur.py
============================================
Serveur MCP (Model Context Protocol) : expose tes outils domotique, PC, OBS, agenda... à des clients
MCP (Claude Desktop, etc.) — de façon SÉCURISÉE :

  • seuls les outils marqués `mcp=True` ET appartenant aux domaines listés dans `mcp_domaines`
    sont visibles (les finances, mails, appels, vision d'écran ne le sont jamais) ;
  • niveau 1 : exécution directe ; niveau 2 : seulement si tu l'as « toujours autorisé » ;
    niveau 3 (extinction, suppression...) : JAMAIS via MCP, il faut ta voix sur place ;
  • accès HTTP protégé par jeton (`mcp_jeton`, généré automatiquement).

Transports : HTTP (`POST /mcp` sur l'interface web locale) et stdio via `mcp_kryptonix.py`
(pont à copier dans la config de Claude Desktop).
"""

from __future__ import annotations

import json
import logging
import secrets

from .base import Module, Outil

LOG = logging.getLogger("mcp")
VERSION_PROTOCOLE = "2024-11-05"
DOMAINES_PAR_DEFAUT = ["domotique", "pc", "obs", "utilitaires", "agenda", "ia", "personnalite", "memoire", "contenu"]
DOMAINES_JAMAIS = {"finances", "mail", "telephone", "ecran", "social"}


class ServeurMCP(Module):
    nom = "mcp"
    description = "Serveur MCP pour clients externes (Claude Desktop...)"

    def jeton(self) -> str:
        j = self.config.get("mcp_jeton")
        if not j:
            j = secrets.token_urlsafe(24)
            self.kx.recharger_config({"mcp_jeton": j})
        return j

    def domaines(self) -> set[str]:
        voulus = set(self.config.get("mcp_domaines") or DOMAINES_PAR_DEFAUT)
        return voulus - DOMAINES_JAMAIS

    def outils_exposes(self) -> list[Outil]:
        if not self.config.get("mcp_actif", True):
            return []
        doms = self.domaines()
        return [o for o in self.kx.extensions.outils() if o.mcp and o.domaine in doms and o.domaine not in DOMAINES_JAMAIS]

    @staticmethod
    def _schema(o: Outil) -> dict:
        return {"type": "object", "properties": o.proprietes, "required": list(o.requis)}

    def appeler(self, nom: str, args: dict) -> tuple[str, bool]:
        """→ (texte, est_erreur)"""
        o = next((x for x in self.outils_exposes() if x.nom == nom), None)
        if o is None:
            return f"Outil inconnu ou non exposé : {nom}", True
        sec = self.kx.extensions.securite
        niveau = sec.niveau_de(o.nom, o.niveau)
        if niveau >= 3:
            return ("Action sensible : elle exige ta confirmation vocale sur place et ne peut pas être "
                    "déclenchée par un client MCP."), True
        if niveau == 2 and o.nom not in sec.autorises():
            return (f"« {o.nom} » demande une confirmation. Dis « toujours autoriser {o.domaine} » à Kryptonix "
                    "pour la permettre aux clients MCP."), True
        resultat = sec._executer(o, args or {})
        self.kx.memoire.journaliser("mcp", o.nom)
        return resultat, resultat.startswith(("Je n'ai pas pu", "Action « "))

    def traiter(self, message: dict) -> dict | None:
        """Traite un message JSON-RPC ; None pour une notification."""
        id_ = message.get("id")
        methode = message.get("method", "")
        params = message.get("params") or {}

        def ok(resultat):
            return {"jsonrpc": "2.0", "id": id_, "result": resultat}

        def erreur(code, msg):
            return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}

        if id_ is None:                                   # notification
            return None
        if methode == "initialize":
            return ok({"protocolVersion": params.get("protocolVersion") or VERSION_PROTOCOLE,
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": self.kx.nom.lower(), "version": "1.0"}})
        if methode == "ping":
            return ok({})
        if methode == "tools/list":
            return ok({"tools": [{"name": o.nom, "description": o.description, "inputSchema": self._schema(o)}
                                 for o in self.outils_exposes()]})
        if methode == "tools/call":
            texte, err = self.appeler(params.get("name", ""), params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": texte}], "isError": err})
        if methode in ("resources/list", "prompts/list"):
            return ok({methode.split("/")[0]: []})
        return erreur(-32601, f"méthode inconnue : {methode}")

    def traiter_json(self, brut: str) -> str | None:
        try:
            m = json.loads(brut)
        except ValueError:
            return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}})
        if isinstance(m, list):
            rep = [r for r in (self.traiter(x) for x in m if isinstance(x, dict)) if r]
            return json.dumps(rep) if rep else None
        if not isinstance(m, dict):
            return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "requête invalide"}})
        rep = self.traiter(m)
        return json.dumps(rep, ensure_ascii=False) if rep else None

    def statut(self) -> dict:
        return {"actif": bool(self.config.get("mcp_actif", True)), "outils": len(self.outils_exposes()),
                "domaines": sorted(self.domaines())}
