"""
KRYPTONIX / interfaces/web.py
==============================
Dashboard web (Flask) : terminal synaptique, télémétrie live, gestion des flux,
ingestion de documents par glisser-déposer et console sandbox.

Le flux temps réel passe par SSE (Server-Sent Events) : une file par client,
alimentée par les diffusions du noyau. Pas de WebSocket, pas de dépendance en plus.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import tempfile
import threading
import time

from flask import Flask, Response, jsonify, render_template, request, send_file

from noyau.config import DOSSIER_DONNEES

LOG = logging.getLogger("web")

# Dernier signal de vie envoyé par la page (utilisé pour fermer proprement
# l'application si elle tourne dans un navigateur plutôt que dans sa fenêtre).
DERNIER_BATTEMENT = [0.0]

DOSSIER_DOCS_RECUS = os.path.join(DOSSIER_DONNEES, "documents_recus")

EXTENSIONS_AUTORISEES = {
    ".pdf", ".txt", ".log", ".md", ".csv", ".json", ".xml", ".html",
    ".py", ".js", ".bat", ".sh", ".ini", ".cfg", ".yaml", ".yml", ".srt",
}


def creer_application(kryptonix) -> Flask:
    """Fabrique l'application Flask branchée sur une instance de Kryptonix."""
    application = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
    )
    application.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 Mo
    os.makedirs(DOSSIER_DOCS_RECUS, exist_ok=True)

    # --- Bus SSE : une file par navigateur connecté --------------------
    clients: list[queue.Queue] = []
    verrou = threading.Lock()

    def diffuser(evenement: str, donnees: dict) -> None:
        charge = json.dumps({"evenement": evenement, **donnees}, ensure_ascii=False)
        with verrou:
            morts = []
            for file in clients:
                try:
                    file.put_nowait(charge)
                except queue.Full:
                    morts.append(file)
            for file in morts:
                clients.remove(file)

    kryptonix.abonner(diffuser)

    # ==================================================================
    #  Pages
    # ==================================================================
    @application.route("/api/battement")
    def battement():
        DERNIER_BATTEMENT[0] = time.time()
        return jsonify({"ok": True})

    @application.route("/")
    def accueil():
        return render_template("index.html", nom=kryptonix.nom)

    # ==================================================================
    #  Flux temps réel
    # ==================================================================
    @application.route("/api/flux")
    def flux():
        file: queue.Queue = queue.Queue(maxsize=200)
        with verrou:
            clients.append(file)

        def generer():
            try:
                yield f"data: {json.dumps({'evenement': 'connexion'})}\n\n"
                while True:
                    try:
                        charge = file.get(timeout=20)
                        yield f"data: {charge}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"   # garde la connexion ouverte
            finally:
                with verrou:
                    if file in clients:
                        clients.remove(file)

        return Response(generer(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ==================================================================
    #  État & télémétrie
    # ==================================================================
    @application.route("/api/etat")
    def etat():
        return jsonify(kryptonix.instantane())

    @application.route("/api/telemetrie")
    def telemetrie():
        return jsonify(kryptonix.systeme.rafraichir())

    @application.route("/api/processus")
    def processus():
        return jsonify({"processus": kryptonix.systeme.processus_gourmands(8)})

    @application.route("/api/evenements")
    def evenements():
        return jsonify({"evenements": kryptonix.memoire.evenements(25)})

    # ==================================================================
    #  Conversation
    # ==================================================================
    @application.route("/api/commande", methods=["POST"])
    def commande():
        donnees = request.get_json(silent=True) or {}
        texte = (donnees.get("texte") or "").strip()
        vocaliser = bool(donnees.get("vocaliser", True))
        if not texte:
            return jsonify({"succes": False, "erreur": "commande vide"}), 400
        reponse = kryptonix.traiter(texte, source="web", vocaliser=vocaliser)
        return jsonify({"succes": True, "reponse": reponse,
                        "registre": kryptonix.dernier_registre})

    @application.route("/api/historique")
    def historique():
        limite = min(int(request.args.get("limite", 40)), 200)
        return jsonify({"messages": kryptonix.memoire.historique(limite)})

    @application.route("/api/profil")
    def profil():
        return jsonify({"profil": kryptonix.memoire.tout_le_profil()})

    @application.route("/api/purger", methods=["POST"])
    def purger():
        kryptonix.memoire.purger_conversations()
        return jsonify({"succes": True})

    # ==================================================================
    #  Voix
    # ==================================================================
    @application.route("/api/ecoute", methods=["POST"])
    def ecoute():
        actif = kryptonix.basculer_ecoute()
        return jsonify({"succes": True, "ecoute_active": actif,
                        "micro": kryptonix.voix.micro_disponible})

    @application.route("/api/silence", methods=["POST"])
    def silence():
        kryptonix.voix.taire()
        return jsonify({"succes": True})

    @application.route("/api/voix/liste")
    def voix_liste():
        return jsonify({"voix": kryptonix.voix.voix_disponibles()})

    # ==================================================================
    #  Réglages (apparence, voix)
    # ==================================================================
    CLES_REGLAGES = (
        "apparence_palette", "apparence_forme", "apparence_taille",
        "voix_style", "moteur_tts", "vitesse_voix", "hauteur_voix", "voix_active",
    )

    @application.route("/api/reglages")
    def obtenir_reglages():
        return jsonify({cle: kryptonix.config.get(cle) for cle in CLES_REGLAGES})

    @application.route("/api/reglages", methods=["POST"])
    def modifier_reglages():
        donnees = request.get_json(silent=True) or {}
        filtre = {cle: valeur for cle, valeur in donnees.items() if cle in CLES_REGLAGES}
        if not filtre:
            return jsonify({"succes": False, "erreur": "aucun réglage reconnu"}), 400
        kryptonix.recharger_config(filtre)
        return jsonify({"succes": True, "reglages": filtre})

    # ==================================================================
    #  Météo & actualités en direct
    # ==================================================================
    @application.route("/api/meteo")
    def meteo():
        ville = request.args.get("ville")
        return jsonify(kryptonix.meteo.actuel(ville))

    @application.route("/api/actualites")
    def actualites():
        return jsonify({"actualites": kryptonix.actualites.titres()})

    # ==================================================================
    #  Documents
    # ==================================================================
    @application.route("/api/documents")
    def documents():
        return jsonify({"documents": kryptonix.memoire.documents(40)})

    @application.route("/api/document", methods=["POST"])
    def televerser():
        fichier = request.files.get("document")
        if not fichier or not fichier.filename:
            return jsonify({"succes": False, "erreur": "aucun fichier reçu"}), 400

        nom = os.path.basename(fichier.filename)
        extension = os.path.splitext(nom)[1].lower()
        if extension not in EXTENSIONS_AUTORISEES:
            return jsonify({"succes": False,
                            "erreur": f"extension refusée : {extension}"}), 400

        chemin = os.path.join(DOSSIER_DOCS_RECUS, f"{int(time.time())}_{nom}")
        fichier.save(chemin)

        resultat = kryptonix.ingerer(chemin, resumer=True)
        if resultat["succes"]:
            kryptonix.voix.parler(f"Document {resultat['nom']} assimilé.")
        return jsonify(resultat)

    @application.route("/api/document/<int:doc_id>/resume")
    def resume_document(doc_id: int):
        return jsonify({"resume": kryptonix.documents.resumer_document(doc_id)})

    @application.route("/api/document/<int:doc_id>/fichier")
    def telecharger_document(doc_id: int):
        document = kryptonix.memoire.document(doc_id)
        if not document or not os.path.isfile(document["chemin"]):
            return jsonify({"succes": False, "erreur": "fichier original introuvable"}), 404
        return send_file(document["chemin"], as_attachment=True, download_name=document["nom"])

    @application.route("/api/document/<int:doc_id>/question", methods=["POST"])
    def interroger_document(doc_id: int):
        donnees = request.get_json(silent=True) or {}
        question = (donnees.get("question") or "").strip()
        if not question:
            return jsonify({"succes": False, "erreur": "question manquante"}), 400
        reponse = kryptonix.interroger_document(doc_id, question)
        return jsonify({"succes": True, "reponse": reponse})

    @application.route("/api/aspirer", methods=["POST"])
    def aspirer():
        donnees = request.get_json(silent=True) or {}
        chemin = (donnees.get("chemin") or "").strip()
        if not chemin:
            return jsonify({"succes": False, "erreur": "chemin manquant"}), 400
        bilan = kryptonix.documents.aspirer_dossier(chemin, resumer=False)
        return jsonify({"succes": "erreur" not in bilan, **bilan})

    # ==================================================================
    #  Sandbox
    # ==================================================================
    @application.route("/api/executer", methods=["POST"])
    def executer():
        donnees = request.get_json(silent=True) or {}
        code = donnees.get("code") or ""
        langage = donnees.get("langage") or "python"
        return jsonify(kryptonix.sandbox.executer(code, langage))

    @application.route("/api/sandbox/langages")
    def sandbox_langages():
        return jsonify({"langages": kryptonix.sandbox.langages_disponibles()})

    # ==================================================================
    #  Analyse et nettoyage du PC
    # ==================================================================
    @application.route("/api/pc/analyser")
    def pc_analyser():
        rapport = kryptonix.nettoyage.analyser()
        rapport["resume_parle"] = kryptonix.nettoyage.rapport_parle(rapport)
        return jsonify(rapport)

    @application.route("/api/pc/nettoyer", methods=["POST"])
    def pc_nettoyer():
        resultat = kryptonix.nettoyage.nettoyer_temporaires()
        resultat["libere_lisible"] = kryptonix.nettoyage.formater_octets(
            resultat["octets_liberes"])
        kryptonix.memoire.journaliser("nettoyage", f"{resultat['fichiers_supprimes']} fichiers")
        return jsonify(resultat)

    @application.route("/api/pc/composants")
    def pc_composants():
        return jsonify(kryptonix.systeme.composants())

    # ==================================================================
    #  Étude d'image
    # ==================================================================
    @application.route("/api/image", methods=["POST"])
    def analyser_image():
        fichier = request.files.get("image")
        if not fichier or not fichier.filename:
            return jsonify({"succes": False, "erreur": "aucune image reçue"}), 400

        extension = os.path.splitext(fichier.filename)[1].lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            return jsonify({"succes": False, "erreur": f"format refusé : {extension}"}), 400

        dossier = os.path.join(tempfile.gettempdir(), "kryptonix_images")
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, f"{int(time.time())}_{os.path.basename(fichier.filename)}")
        fichier.save(chemin)

        question = request.form.get("question", "")
        resultat = kryptonix.analyser_image_televersee(chemin, question)
        resultat["succes"] = "erreur" not in resultat
        resultat["rapport_parle"] = kryptonix.vision.rapport_parle(resultat)
        return jsonify(resultat)

    # ==================================================================
    #  Rédaction et fichiers générés (documents, sons, vidéos)
    # ==================================================================
    @application.route("/api/rediger", methods=["POST"])
    def rediger():
        donnees = request.get_json(silent=True) or {}
        sujet = (donnees.get("sujet") or "").strip()
        format_cible = donnees.get("format", "md")
        nature = donnees.get("nature", "document")
        if not sujet:
            return jsonify({"succes": False, "erreur": "sujet manquant"}), 400

        contexte = kryptonix.documents.contexte_pertinent(sujet)
        contenu = kryptonix.redaction.generer_contenu(sujet, nature=nature,
                                                       contexte_documents=contexte)
        resultat = kryptonix.redaction.creer(titre=sujet, contenu=contenu,
                                             format_cible=format_cible)
        return jsonify(resultat)

    @application.route("/api/fichiers-generes")
    def fichiers_generes():
        return jsonify({"fichiers": kryptonix.memoire.fichiers_generes(40)})

    @application.route("/api/fichier-genere/<int:fichier_id>")
    def telecharger_fichier_genere(fichier_id: int):
        fichier = kryptonix.memoire.fichier_genere(fichier_id)
        if not fichier or not os.path.isfile(fichier["chemin"]):
            return jsonify({"succes": False, "erreur": "fichier introuvable"}), 404
        return send_file(fichier["chemin"], as_attachment=True, download_name=fichier["nom"])

    # ==================================================================
    @application.errorhandler(413)
    def trop_gros(_erreur):
        return jsonify({"succes": False, "erreur": "fichier trop volumineux (25 Mo max)"}), 413

    @application.errorhandler(500)
    def erreur_interne(erreur):
        LOG.error("Erreur serveur : %s", erreur)
        return jsonify({"succes": False, "erreur": "erreur interne"}), 500

    return application


def lancer_serveur(kryptonix, hote: str | None = None, port: int | None = None,
                   en_arriere_plan: bool = True) -> threading.Thread | None:
    """Démarre Flask. En arrière-plan par défaut pour laisser le HUD au thread principal."""
    application = creer_application(kryptonix)
    hote = hote or kryptonix.config.get("web_hote", "127.0.0.1")
    port = int(port or kryptonix.config.get("web_port", 5000))

    def servir():
        application.run(host=hote, port=port, debug=False,
                        use_reloader=False, threaded=True)

    if not en_arriere_plan:
        servir()
        return None

    thread = threading.Thread(target=servir, daemon=True, name="kryptonix-web")
    thread.start()
    LOG.info("Dashboard disponible sur http://%s:%d", hote, port)
    return thread
