"""
KRYPTONIX / interfaces/routes_extensions.py
============================================
Routes web des modules Jarvis : panneau de contrôle (/panneau), cockpit financier (/finances),
permissions, sons, réglages des modules, serveur MCP local.

Toutes ces routes sont réservées à ta machine (127.0.0.1). Les secrets (clés API, jetons, mots de
passe) ne sont jamais renvoyés au navigateur : seul un « défini / non défini » est affiché.
"""

from __future__ import annotations

import hmac
import json
import logging
import os

from flask import Response, jsonify, render_template, request, send_file

from noyau.extensions.base import ErreurModule
from noyau.extensions.personnalites import PERSONNALITES
from noyau.extensions.routeur_llm import MODES

LOG = logging.getLogger("routes_ext")
MASQUE = "••••••"

# clé : (type, contrainte, secret?)
REGLAGES_EXT: dict[str, tuple] = {
    "mode_ia": ("choix", MODES, False), "fournisseur_llm": ("choix", ("anthropic", "openai"), False),
    "llm_modele_quotidien": ("texte", 80, False), "llm_modele_qualite": ("texte", 80, False),
    "budget_mensuel": ("decimal", (0, 10000), False), "openai_base_url": ("texte", 200, False),
    "cloud_documents": ("bool", None, False), "cles_api": ("json", None, True),
    "personnalite": ("choix", tuple(PERSONNALITES), False), "personnalite_voix": ("bool", None, False),
    "sons_actifs": ("bool", None, False), "sons_volume": ("entier", (0, 100), False), "sons_reflexion": ("bool", None, False),
    "hue_pont": ("texte", 80, False), "presence_actif": ("bool", None, False),
    "presence_telephones": ("liste", 5, False), "presence_scene_depart": ("texte", 60, False),
    "presence_scene_retour": ("texte", 60, False), "wol_machines": ("json", None, False),
    "delai_securite_n3": ("entier", (3, 60), False),
    "agenda_ical": ("json", None, True), "google_calendriers": ("liste", 20, False),
    "google_client_id": ("texte", 200, True), "google_client_secret": ("texte", 200, True),
    "google_refresh_token": ("texte", 400, True),
    "gmail_adresse": ("texte", 120, False), "gmail_mot_de_passe_app": ("texte", 80, True),
    "discord_token": ("texte", 200, True), "discord_salons": ("liste", 20, False),
    "discord_utilisateur_id": ("texte", 40, False), "discord_ecoute": ("bool", None, False),
    "instagram_comptes": ("json", None, True),
    "twilio_sid": ("texte", 80, True), "twilio_token": ("texte", 80, True), "twilio_numero": ("texte", 30, False),
    "twilio_url_publique": ("texte", 200, False), "twilio_contacts": ("json", None, False),
    "twilio_repondre_entrants": ("bool", None, False),
    "obs_hote": ("texte", 80, False), "obs_port": ("entier", (1, 65535), False), "obs_mot_de_passe": ("texte", 120, True),
    "applications": ("json", None, False),
    "wakeword_actif": ("bool", None, False), "wakeword_modele": ("texte", 200, False),
    "wakeword_seuil": ("decimal", (0.1, 0.99), False), "stt_moteur": ("choix", ("auto", "whisper", "google"), False),
    "reseau_actif": ("bool", None, False), "reseau_port": ("entier", (1024, 65535), False),
    "satellite_tts_serveur": ("bool", None, False), "satellite_exige_mot_reveil": ("bool", None, False),
    "mcp_actif": ("bool", None, False), "mcp_domaines": ("liste", 20, False),
    "agent_modele": ("texte", 80, False), "agent_url": ("texte", 200, False), "agent_actions": ("bool", None, False),
    "gestes_actif": ("bool", None, False), "navigateur_cdp": ("texte", 120, False),
}


def valider_ext(cle: str, valeur):
    genre, contrainte, _secret = REGLAGES_EXT[cle]
    if genre == "bool":
        return valeur.strip().lower() in ("1", "true", "oui", "on") if isinstance(valeur, str) else bool(valeur)
    if genre == "choix":
        if valeur not in contrainte:
            raise ValueError(f"{cle} : valeur non autorisée")
        return valeur
    if genre in ("entier", "decimal"):
        try:
            n = float(valeur)
        except (TypeError, ValueError):
            raise ValueError(f"{cle} : nombre attendu") from None
        n = max(contrainte[0], min(contrainte[1], n))
        return int(round(n)) if genre == "entier" else round(n, 2)
    if genre == "texte":
        t = str(valeur if valeur is not None else "").strip()
        if len(t) > contrainte or any(ord(c) < 32 for c in t):
            raise ValueError(f"{cle} : texte invalide")
        return t
    if genre == "liste":
        if isinstance(valeur, str):
            valeur = [l.strip() for l in valeur.replace(",", "\n").splitlines()]
        return [str(v).strip() for v in valeur if str(v).strip()][:contrainte]
    if genre == "json":
        if isinstance(valeur, str):
            valeur = json.loads(valeur) if valeur.strip() else {}
        if not isinstance(valeur, (dict, list)):
            raise ValueError(f"{cle} : objet ou liste JSON attendu")
        return valeur
    raise ValueError("type inconnu")


def enregistrer(application, kx) -> None:
    ext = kx.extensions

    def local_seulement():
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"succes": False, "erreur": "réservé à cette machine"}), 403
        return None

    @application.before_request
    def garde_locale():
        chemin = request.path
        if chemin.startswith(("/api/extensions", "/api/permissions", "/api/finances", "/api/sons", "/api/ia",
                              "/api/reseau", "/panneau", "/finances", "/mcp")):
            refus = local_seulement()
            if refus:
                return refus

    # -- pages ------------------------------------------------------------
    @application.route("/panneau")
    def panneau():
        return render_template("panneau.html", nom=kx.nom)

    @application.route("/finances")
    def page_finances():
        return render_template("finances.html", nom=kx.nom)

    # -- état / modules ---------------------------------------------------
    @application.route("/api/extensions/statut")
    def ext_statut():
        return jsonify({"succes": True, **ext.statut(), "ia": ext.routeur.bilan(), "etat": kx.etat,
                        "voix": kx.voix.moteur_actif, "micro": kx.voix.micro_disponible,
                        "cerveau": {"disponible": kx.cerveau.disponible, "modele": kx.cerveau.modele},
                        "personnalites": {k: v["libelle"] for k, v in PERSONNALITES.items()},
                        "personnalite": kx.config.get("personnalite", "majordome")})

    @application.route("/api/extensions/config", methods=["GET", "POST"])
    def ext_config():
        if request.method == "GET":
            sortie = {}
            for cle, (genre, contrainte, secret) in REGLAGES_EXT.items():
                v = kx.config.get(cle)
                sortie[cle] = {"type": genre, "contrainte": list(contrainte) if isinstance(contrainte, tuple) else contrainte,
                               "secret": secret, "defini": v not in (None, "", [], {}),
                               "valeur": (MASQUE if v not in (None, "", [], {}) else "") if secret else v}
            return jsonify({"succes": True, "reglages": sortie})
        donnees = request.get_json(silent=True) or {}
        appliques, erreurs = {}, {}
        for cle, valeur in donnees.items():
            if cle not in REGLAGES_EXT:
                erreurs[cle] = "réglage inconnu"
                continue
            if REGLAGES_EXT[cle][2] and valeur == MASQUE:
                continue                                   # secret inchangé
            try:
                appliques[cle] = valider_ext(cle, valeur)
            except (ValueError, json.JSONDecodeError) as e:
                erreurs[cle] = str(e)
        if appliques:
            kx.recharger_config(appliques)
            for nom in ("reseau", "social", "domotique", "gestes"):
                m = ext.module(nom)
                try:
                    if nom == "reseau" and "reseau_actif" in appliques and not appliques["reseau_actif"]:
                        m.arreter()
                    elif m:
                        m.demarrer()
                except Exception:
                    LOG.exception("redémarrage de %s", nom)
        return jsonify({"succes": not erreurs, "appliques": list(appliques), "erreurs": erreurs})

    @application.route("/api/extensions/outil", methods=["POST"])
    def ext_outil():
        d = request.get_json(silent=True) or {}
        reponse = ext.securite.lancer(str(d.get("nom", "")), d.get("args") or {})
        return jsonify({"succes": True, "reponse": reponse, "en_attente": ext.securite.en_attente})

    # -- permissions -----------------------------------------------------------
    @application.route("/api/permissions", methods=["GET", "POST"])
    def permissions():
        if request.method == "POST":
            d = request.get_json(silent=True) or {}
            nom = str(d.get("nom", ""))
            try:
                if "niveau" in d:
                    ext.securite.definir_niveau(nom, int(d["niveau"]))
                if "toujours" in d:
                    if d["toujours"]:
                        if not ext.securite.autoriser_toujours(nom):
                            return jsonify({"succes": False, "erreur": "un outil de niveau 3 ne peut pas être autorisé en permanence"}), 400
                    else:
                        ext.securite.revoquer(nom)
            except ValueError as e:
                return jsonify({"succes": False, "erreur": str(e)}), 400
        return jsonify({"succes": True, **ext.securite.permissions()})

    @application.route("/api/permissions/revoquer", methods=["POST"])
    def permissions_revoquer():
        return jsonify({"succes": True, "revoquees": ext.securite.revoquer()})

    # -- IA ----------------------------------------------------------------
    @application.route("/api/ia")
    def ia_bilan():
        return jsonify({"succes": True, **ext.routeur.bilan()})

    # -- sons --------------------------------------------------------------
    @application.route("/api/sons")
    def sons_liste():
        return jsonify({"succes": True, "sons": ext.sons.liste(), "actifs": ext.sons.actifs,
                        "volume": kx.config.get("sons_volume", 70)})

    @application.route("/api/sons/<nom>.wav")
    def son_fichier(nom: str):
        if nom not in ext.sons.catalogue:
            return jsonify({"succes": False, "erreur": "son inconnu"}), 404
        ext.sons.fabriquer()
        return send_file(ext.sons.chemin(nom), mimetype="audio/wav")

    @application.route("/api/sons/jouer", methods=["POST"])
    def son_jouer():
        nom = (request.get_json(silent=True) or {}).get("nom", "")
        return jsonify({"succes": ext.sons.jouer(nom, force=True)})

    # -- réseau / MCP ---------------------------------------------------------
    @application.route("/api/reseau")
    def reseau_info():
        r, mcp = ext.module("reseau"), ext.module("mcp")
        return jsonify({"succes": True, "actif": r.statut()["actif"], "port": r.port or kx.config.get("reseau_port", 5055),
                        "jeton": r.jeton(), "pieces": r.satellites.liste(), "mcp_jeton": mcp.jeton(),
                        "mcp_outils": [o.nom for o in mcp.outils_exposes()]})

    @application.route("/mcp", methods=["POST"])
    def mcp_http():
        mcp = ext.module("mcp")
        auth = request.headers.get("Authorization", "")
        recu = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not recu or not hmac.compare_digest(recu.encode(), mcp.jeton().encode()):
            return jsonify({"erreur": "jeton invalide"}), 401
        rep = mcp.traiter_json(request.get_data(as_text=True))
        if rep is None:
            return Response(status=202)
        return Response(rep, mimetype="application/json")

    # -- finances (strictement local) -------------------------------------------
    def fin():
        return ext.module("finances")

    @application.route("/api/finances/resume")
    def fin_resume():
        return jsonify({"succes": True, **fin().resume()})

    @application.route("/api/finances/abonnement", methods=["POST"])
    def fin_ajouter():
        d = request.get_json(silent=True) or {}
        try:
            ident = fin().ajouter_abonnement(str(d["nom"]), float(d["montant"]), d.get("periodicite", "mensuel"),
                                             d.get("prochaine") or None, str(d.get("categorie", "")))
        except (KeyError, ValueError, TypeError):
            return jsonify({"succes": False, "erreur": "nom et montant requis"}), 400
        except ErreurModule as e:
            return jsonify({"succes": False, "erreur": str(e)}), 400
        return jsonify({"succes": True, "id": ident})

    @application.route("/api/finances/abonnement/<int:ident>", methods=["PATCH", "DELETE"])
    def fin_modifier(ident: int):
        if request.method == "DELETE":
            fin().supprimer_abonnement(ident)
        else:
            fin().modifier_abonnement(ident, **(request.get_json(silent=True) or {}))
        return jsonify({"succes": True})

    @application.route("/api/finances/echeance", methods=["POST"])
    def fin_echeance():
        d = request.get_json(silent=True) or {}
        try:
            ident = fin().ajouter_echeance(str(d["libelle"]), str(d["date"]), float(d.get("montant") or 0))
        except (KeyError, ValueError, TypeError, ErreurModule):
            return jsonify({"succes": False, "erreur": "libellé et date valide requis"}), 400
        return jsonify({"succes": True, "id": ident})

    @application.route("/api/finances/echeance/<int:ident>/payee", methods=["POST"])
    def fin_payee(ident: int):
        fin().marquer_payee(ident)
        return jsonify({"succes": True})

    @application.route("/api/finances/import", methods=["POST"])
    def fin_import():
        brut = request.get_data()
        if not brut or len(brut) > 10_000_000:
            return jsonify({"succes": False, "erreur": "fichier vide ou trop volumineux"}), 400
        for codage in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texte = brut.decode(codage)
                break
            except UnicodeDecodeError:
                continue
        try:
            return jsonify({"succes": True, **fin().importer_csv(texte)})
        except ErreurModule as e:
            return jsonify({"succes": False, "erreur": str(e)}), 400

    @application.route("/api/finances/candidat/<int:ident>/<action>", methods=["POST"])
    def fin_candidat(ident: int, action: str):
        try:
            fin().confirmer_candidat(ident, action == "accepter")
        except ErreurModule as e:
            return jsonify({"succes": False, "erreur": str(e)}), 404
        return jsonify({"succes": True})

    @application.route("/api/finances/detecter", methods=["POST"])
    def fin_detecter():
        n = fin().detecter_recurrences()
        try:
            n += fin().detecter_depuis_gmail()
        except ErreurModule:
            pass
        return jsonify({"succes": True, "nouveaux": n})
