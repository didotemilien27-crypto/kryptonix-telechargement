#!/usr/bin/env python3
"""
KRYPTONIX — pont MCP (stdio) pour Claude Desktop et autres clients MCP.

Il relaie les messages MCP vers le serveur de Kryptonix qui tourne sur ta machine (l'application
doit être ouverte). Variables d'environnement :
    KRYPTONIX_URL    http://127.0.0.1:5000/mcp   (défaut)
    KRYPTONIX_JETON  jeton affiché dans le panneau (section Réseau, satellites et MCP)

Exemple (claude_desktop_config.json) :
{ "mcpServers": { "kryptonix": { "command": "python", "args": ["C:/chemin/mcp_kryptonix.py"],
    "env": { "KRYPTONIX_JETON": "..." } } } }
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("KRYPTONIX_URL", "http://127.0.0.1:5000/mcp")
JETON = os.environ.get("KRYPTONIX_JETON", "")


def relayer(ligne: str):
    req = urllib.request.Request(URL, data=ligne.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {JETON}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode("utf-8") if r.status == 200 else None
    except urllib.error.HTTPError as e:
        raise RuntimeError("jeton refusé" if e.code == 401 else f"HTTP {e.code}")
    except OSError:
        raise RuntimeError("Kryptonix n'est pas ouvert ou injoignable")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    for ligne in sys.stdin:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            rep = relayer(ligne)
        except RuntimeError as e:
            try:
                id_ = json.loads(ligne).get("id")
            except ValueError:
                id_ = None
            if id_ is not None:
                rep = json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": -32000, "message": str(e)}}, ensure_ascii=False)
            else:
                rep = None
        if rep:
            sys.stdout.write(rep + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
