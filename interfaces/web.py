"""
KRYPTONIX / interfaces/web.py
==============================
Dashboard web (Flask) : terminal synaptique, télémétrie live, gestion des flux,
ingestion de documents et de médias (audio, images, vidéos) par glisser-déposer,
console sandbox et réglages.

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
import uuid

from flask import Flask, Response, jsonify, render_template, request, send_file

from noyau.config import DOSSIER_DONNEES, REGLAGES, valider_reglage
from noyau.medias import (EXT_AUDIO, EXT_IMAGE, EXT_VIDEO, LIMITES_OCTETS, PRECISIONS_AUDIO,
                          ErreurComprehension, ffmpeg, type_de_fichier)

LOG = logging.getLogger("web")

# Dernier signal de vie envoyé par la page (utilisé pour fermer proprement
# l'application si elle tourne dans un navigateur plutôt que dans sa fenêtre).
DERNIER_BATTEMENT = [0.0]

DOSSIER_DOCS_RECUS = os.path.join(DOSSIER_DONNEES, "documents_recus")

EXTENSIONS_AUTORISEES = {
    ".pdf", ".txt", ".log", ".md", ".csv", ".json", ".xml", ".html",
    ".py", ".js", ".css", ".bat", ".sh", ".ini", ".cfg", ".yaml", ".yml", ".srt",
}


def creer_application(kryptonix) -> Flask:
    """Fabrique l'application Flask branchée sur une instance de Kryptonix."""
    application = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates"),
    )
    application.config["MAX_CONTENT_LENGTH"] = 520 * 1024 * 1024  # la plus grosse vidéo acceptée + marge
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
        """Catalogue réel des voix (neuronales + système) pour la liste déroulante."""
        return jsonify(kryptonix.voix.catalogue())

    @application.route("/api/voix/tester", methods=["POST"])
    def voix_tester():
        """Fait entendre la voix avec les réglages actuels et dit quel moteur a parlé."""
        donnees = request.get_json(silent=True) or {}
        texte = (donnees.get("texte") or "").strip()[:300] or None
        return jsonify(kryptonix.voix.apercu(texte))

    # ==================================================================
    #  Réglages (apparence, voix, infos en direct, assistant, sandbox)
    # ==================================================================
    @application.route("/api/reglages")
    def obtenir_reglages():
        reglages = {cle: kryptonix.config.get(cle) for cle in REGLAGES}
        reglages["moteur_actif"] = kryptonix.voix.moteur_actif
        return jsonify(reglages)

    @application.route("/api/reglages", methods=["POST"])
    def modifier_reglages():
        donnees = request.get_json(silent=True) or {}
        valides, erreurs = {}, {}
        for cle, valeur in donnees.items():
            if cle not in REGLAGES:
                continue
            try:
                valides[cle] = valider_reglage(cle, valeur)
            except ValueError as erreur:
                erreurs[cle] = str(erreur)
        if not valides and not erreurs:
            return jsonify({"succes": False, "erreur": "aucun réglage reconnu"}), 400
        if valides:
            kryptonix.recharger_config(valides)
        return jsonify({"succes": not erreurs, "reglages": valides, "erreurs": erreurs,
                        "moteur_actif": kryptonix.voix.moteur_actif}), (200 if valides else 400)

    # ==================================================================
    #  Modèles d'IA (liste, changement, téléchargement sans terminal)
    # ==================================================================
    SUGGESTIONS_MODELES = [
        {"id": "mistral", "libelle": "Mistral 7B : polyvalent, très bon en français (environ 4 Go)"},
        {"id": "llama3.2:3b", "libelle": "Llama 3.2 3B : rapide, bon compromis (environ 2 Go)"},
        {"id": "gemma:2b", "libelle": "Gemma 2B : très léger pour petits PC (environ 1,7 Go)"},
        {"id": "qwen2.5:7b", "libelle": "Qwen 2.5 7B : très capable (environ 4,7 Go)"},
        {"id": "moondream", "libelle": "Moondream : regarde les images, très léger (environ 1,7 Go)"},
        {"id": "llava", "libelle": "Llava : regarde les images, plus précis (environ 4,7 Go)"},
    ]
    telechargement = {"nom": "", "actif": False, "pourcentage": 0, "message": "",
                      "erreur": "", "termine": False}
    verrou_telechargement = threading.Lock()

    @application.route("/api/modeles")
    def modeles():
        kryptonix.cerveau.verifier_connexion()
        with verrou_telechargement:
            etat_dl = dict(telechargement)
        installes = kryptonix.cerveau.modeles_installes
        actuel = kryptonix.config.get("ollama_modele") or ""
        # « mistral » et « mistral:latest » sont le même modèle pour Ollama : pas de doublon.
        if actuel and ":" not in actuel and f"{actuel}:latest" in installes:
            actuel = f"{actuel}:latest"
        return jsonify({"installes": installes,
                        "actuel": actuel,
                        "ollama": kryptonix.cerveau.disponible,
                        "suggestions": SUGGESTIONS_MODELES, "telechargement": etat_dl})

    @application.route("/api/modeles/telecharger", methods=["POST"])
    def modeles_telecharger():
        import re as _re
        from noyau import premier_lancement
        nom = ((request.get_json(silent=True) or {}).get("nom") or "").strip()
        if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,79}", nom):
            return jsonify({"succes": False, "erreur": "nom de modèle invalide"}), 400
        with verrou_telechargement:
            if telechargement["actif"]:
                return jsonify({"succes": False, "erreur": "un téléchargement est déjà en cours"}), 409
            telechargement.update(nom=nom, actif=True, pourcentage=0, message="Démarrage…",
                                  erreur="", termine=False)

        def suivre(pct: int, message: str) -> None:
            with verrou_telechargement:
                telechargement.update(pourcentage=pct, message=message)

        def travail():
            hote = kryptonix.config["ollama_hote"].rstrip("/")
            try:
                if not premier_lancement.ollama_repond(hote):
                    raise RuntimeError("le moteur d'IA ne répond pas")
                reussi = premier_lancement.telecharger_modele(hote, nom, suivre)
                erreur = "" if reussi else "téléchargement impossible (connexion ou nom inconnu)"
            except Exception as exception:
                erreur = str(exception)
            kryptonix.cerveau.verifier_connexion()
            kryptonix.vision.oublier_detection()
            with verrou_telechargement:
                telechargement.update(actif=False, termine=not erreur, erreur=erreur,
                                      pourcentage=100 if not erreur else telechargement["pourcentage"],
                                      message="Modèle prêt." if not erreur else erreur)

        threading.Thread(target=travail, daemon=True, name="kryptonix-modele").start()
        return jsonify({"succes": True})

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

    # ------------------------------------------------------------------
    #  Tâches de fond : transcrire une heure d'audio ou regarder une vidéo prend du temps.
    #  Le dépôt répond tout de suite avec un numéro de tâche que la page interroge.
    # ------------------------------------------------------------------
    travaux: dict[str, dict] = {}
    verrou_travaux = threading.Lock()
    verrou_media = threading.Lock()     # un seul gros traitement à la fois : le PC reste réactif

    def creer_travail(nom: str, genre: str) -> str:
        identifiant = uuid.uuid4().hex[:12]
        with verrou_travaux:
            for ancien in [k for k, t in travaux.items() if time.time() - t["cree"] > 3600]:
                travaux.pop(ancien, None)
            travaux[identifiant] = {"id": identifiant, "nom": nom, "type": genre, "cree": time.time(),
                                    "etat": "en_attente", "pourcentage": 0,
                                    "message": "En file d'attente…", "resultat": None}
        return identifiant

    def maj_travail(identifiant: str, **champs) -> None:
        with verrou_travaux:
            if identifiant in travaux:
                travaux[identifiant].update(champs)

    def travail_media(identifiant: str, chemin: str, nom: str) -> None:
        with verrou_media:
            maj_travail(identifiant, etat="en_cours", pourcentage=1, message="Je m'y mets…")
            try:
                resultat = kryptonix.ingerer(
                    chemin, resumer=True,
                    suivi=lambda pct, msg: maj_travail(identifiant, pourcentage=pct, message=msg))
            except Exception as erreur:         # ne jamais laisser une tâche sans verdict
                LOG.exception("Traitement du média %s", nom)
                resultat = {"succes": False, "nom": nom, "erreur": str(erreur),
                            "message_doux": kryptonix.repli.cas("anomalie", kryptonix.dernier_registre)}
        if resultat.get("succes"):
            maj_travail(identifiant, etat="termine", pourcentage=100, message="Terminé.",
                        resultat=resultat)
            if resultat.get("resume"):      # la conversation se souvient de ce qui vient d'être compris
                kryptonix.memoire.ajouter_message(
                    "assistant", f"[J'ai assimilé « {nom} »] {resultat['resume']}", "systeme", "neutre")
            kryptonix.voix.parler(f"{nom} assimilé.")
        else:
            maj_travail(identifiant, etat="echec", message="Je n'ai pas pu.", resultat=resultat)
            if resultat.get("message_doux"):
                kryptonix.voix.parler(resultat["message_doux"])

    @application.errorhandler(413)
    def trop_gros(_erreur):
        return jsonify({"succes": False, "erreur": "fichier trop volumineux",
                        "message_doux": kryptonix.repli.cas("trop_gros", kryptonix.dernier_registre)}), 413

    @application.route("/api/document", methods=["POST"])
    def televerser():
        fichier = request.files.get("document")
        if not fichier or not fichier.filename:
            return jsonify({"succes": False, "erreur": "aucun fichier reçu"}), 400

        nom = os.path.basename(fichier.filename)
        extension = os.path.splitext(nom)[1].lower()
        genre = type_de_fichier(nom)               # audio | image | video | None (= document texte)
        registre = kryptonix.dernier_registre
        if extension not in EXTENSIONS_AUTORISEES and genre is None:
            return jsonify({"succes": False, "erreur": f"extension refusée : {extension}",
                            "message_doux": kryptonix.repli.cas("format", registre)}), 400

        # Un sous-dossier par dépôt : deux fichiers de même nom ne s'écrasent pas, et le nom
        # affiché dans la bibliothèque reste le vrai nom du fichier (sans préfixe).
        sous_dossier = os.path.join(DOSSIER_DOCS_RECUS, str(int(time.time() * 1000)))
        os.makedirs(sous_dossier, exist_ok=True)
        chemin = os.path.join(sous_dossier, nom)
        fichier.save(chemin)
        limite = LIMITES_OCTETS[genre] if genre else 25 * 1024 * 1024
        if os.path.getsize(chemin) > limite:
            os.remove(chemin)
            return jsonify({"succes": False, "erreur": "fichier trop volumineux",
                            "message_doux": kryptonix.repli.cas("trop_gros", registre)}), 413

        if genre:       # audio, image ou vidéo : traitement long, en tâche de fond
            identifiant = creer_travail(nom, genre)
            threading.Thread(target=travail_media, args=(identifiant, chemin, nom), daemon=True,
                             name="kryptonix-media").start()
            return jsonify({"succes": True, "travail": identifiant, "nom": nom, "type": genre})

        resultat = kryptonix.ingerer(chemin, resumer=True)
        if resultat["succes"]:
            kryptonix.voix.parler(f"Document {resultat['nom']} assimilé.")
        return jsonify(resultat)

    @application.route("/api/travaux/<identifiant>")
    def etat_travail(identifiant: str):
        with verrou_travaux:
            travail = travaux.get(identifiant)
            if travail is None:
                return jsonify({"succes": False, "erreur": "tâche inconnue"}), 404
            return jsonify(dict(travail))

    # ------------------------------------------------------------------
    #  Les « sens » de KRYPTONIX : oreille (audio) et vue (images, vidéos)
    # ------------------------------------------------------------------
    @application.route("/api/medias/etat")
    def etat_medias():
        return jsonify({
            "ffmpeg": bool(ffmpeg()),
            "oreille": {**kryptonix.comprehension.transcripteur.etat(), "precisions": PRECISIONS_AUDIO},
            "vue": {"modele": kryptonix.vision.modele_vision, "conseil": "moondream"},
            "formats": {"audio": sorted(EXT_AUDIO), "image": sorted(EXT_IMAGE), "video": sorted(EXT_VIDEO)},
        })

    @application.route("/api/medias/oreille/preparer", methods=["POST"])
    def preparer_oreille():
        """Télécharge le modèle d'oreille à l'avance, pour que le premier message vocal soit instantané."""
        identifiant = creer_travail("oreille", "oreille")

        def travail():
            maj_travail(identifiant, etat="en_cours", pourcentage=5,
                        message="Je télécharge mon oreille (une seule fois, quelques minutes)…")
            try:
                kryptonix.comprehension.transcripteur.preparer()
                maj_travail(identifiant, etat="termine", pourcentage=100, message="Mon oreille est prête.")
            except ErreurComprehension as erreur:
                maj_travail(identifiant, etat="echec", message=kryptonix.repli.cas(
                    erreur.cle, kryptonix.dernier_registre))
        threading.Thread(target=travail, daemon=True, name="kryptonix-oreille").start()
        return jsonify({"succes": True, "travail": identifiant})

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

    # --- Modules Jarvis : panneau, finances, permissions, sons, MCP ----------
    from interfaces.routes_extensions import enregistrer as enregistrer_extensions
    enregistrer_extensions(application, kryptonix)

    @application.errorhandler(500)
    def erreur_interne(erreur):
        LOG.error("Erreur serveur : %s", erreur)
        return jsonify({"succes": False, "erreur": "erreur interne"}), 500

    return application


def lancer_serveur(kryptonix, hote: str | None = None, port: int | None = None,
                   en_arriere_plan: bool = True) -> threading.Thread | None:
    """Démarre le serveur Flask (en arrière-plan par défaut)."""
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
