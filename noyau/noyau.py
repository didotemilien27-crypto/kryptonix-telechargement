"""
KRYPTONIX / noyau/noyau.py
===========================
Le chef d'orchestre. Instancie tous les sous-systèmes, expose un état partagé
thread-safe aux deux interfaces, et route chaque commande :

    texte/voix ──▶ réflexes immédiats ──▶ agents (web, système, sandbox, docs)
                                      └─▶ cerveau Ollama (avec contexte)
                                              └─▶ mémoire + voix + abonnés UI
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Callable

from .cerveau import Cerveau, analyser_ton
from .config import charger_config, sauvegarder_config, DOSSIER_DONNEES
from .documents import Documents
from .memoire import Memoire
from .anticipation import Anticipation
from .redaction import Redaction, TOUS_FORMATS
from .nettoyage import Nettoyage
from .vision import Vision
from .media import Media
from .sandbox import Sandbox
from .systeme import Systeme
from .voix import Voix
from .meteo import Meteo
from .actualites import Actualites

LOG = logging.getLogger("noyau")

ETATS = ("veille", "ecoute", "reflexion", "parole", "ingestion")


class Kryptonix:
    """Instance unique de l'assistant. Tout passe par ici."""

    def __init__(self, config: dict | None = None):
        self.config = config or charger_config()
        if self.config.get("mode_leger"):
            self._appliquer_mode_leger()
        self.nom = self.config["nom_assistant"]
        self.demarre_a = time.time()

        # --- Sous-systèmes ---
        self.memoire = Memoire()
        self.systeme = Systeme(self.config, self.memoire)
        self.cerveau = Cerveau(self.config, self.memoire, self.systeme)
        self.documents = Documents(self.config, self.memoire, self.cerveau)
        self.sandbox = Sandbox(self.config)
        self.voix = Voix(self.config)
        self.redaction = Redaction(
            self.config, self.cerveau, self.memoire,
            dossier=os.path.join(DOSSIER_DONNEES, "documents_generes"),
        )
        self.anticipation = Anticipation(self.memoire)
        self.nettoyage = Nettoyage(self.config)
        self.vision = Vision(self.config, self.memoire)
        self.media = Media(self.config, self.memoire,
                           dossier=os.path.join(DOSSIER_DONNEES, "documents_generes"))
        self.meteo = Meteo(self.config)
        self.actualites = Actualites(self.config)

        # --- État partagé (lu par le HUD et le dashboard web) ---
        self._verrou = threading.RLock()
        self._etat = "veille"
        self.derniere_commande = ""
        self.derniere_reponse = ""
        self.dernier_registre = "neutre"
        self._abonnes: list[Callable[[str, dict], None]] = []
        self._ecoute_active = threading.Event()
        self._arret = threading.Event()

        self.memoire.journaliser("demarrage", f"{self.nom} initialisé")
        self.systeme.demarrer_surveillance(alerte=self._alerte_materielle)
        self._ingestion_initiale()
        LOG.info("%s est opérationnel.", self.nom)

    def _appliquer_mode_leger(self) -> None:
        """
        Réduit la charge CPU/RAM pour un PC modeste : moins de contexte envoyé
        au modèle, télémétrie moins fréquente, écoute permanente désactivée.
        N'écrase jamais un choix plus économe déjà fait par l'utilisateur.
        """
        allegements = {
            "num_ctx": 2048,
            "contexte_max_messages": 6,
            "telemetrie_intervalle": 5.0,
            "ecoute_permanente": False,
            "hud_actif": False,
        }
        for cle, valeur_legere in allegements.items():
            actuelle = self.config.get(cle)
            # On garde toujours la valeur la plus économe entre l'existante et l'allégée.
            if isinstance(valeur_legere, bool):
                self.config[cle] = valeur_legere or bool(actuelle) is False and valeur_legere
                self.config[cle] = valeur_legere
            elif isinstance(actuelle, (int, float)):
                self.config[cle] = min(actuelle, valeur_legere)
            else:
                self.config[cle] = valeur_legere
        LOG.info("Mode léger actif : contexte réduit, télémétrie espacée, HUD désactivé.")


    @property
    def etat(self) -> str:
        with self._verrou:
            return self._etat

    def _definir_etat(self, etat: str) -> None:
        with self._verrou:
            self._etat = etat if etat in ETATS else "veille"
        self._diffuser("etat", {"etat": self._etat})

    def abonner(self, rappel: Callable[[str, dict], None]) -> None:
        """Une interface s'abonne aux événements (etat, message, alerte)."""
        with self._verrou:
            self._abonnes.append(rappel)

    def _diffuser(self, evenement: str, donnees: dict) -> None:
        with self._verrou:
            abonnes = list(self._abonnes)
        for rappel in abonnes:
            try:
                rappel(evenement, donnees)
            except Exception as erreur:  # une UI cassée ne doit pas figer le noyau
                LOG.debug("Abonné en erreur : %s", erreur)

    def instantane(self) -> dict:
        """Photo complète de l'état, consommée par /api/etat et par le HUD."""
        with self._verrou:
            base = {
                "nom": self.nom,
                "etat": self._etat,
                "derniere_commande": self.derniere_commande,
                "derniere_reponse": self.derniere_reponse,
                "registre": self.dernier_registre,
            }
        base.update({
            "micro": self.voix.micro_disponible,
            "ecoute_active": self._ecoute_active.is_set(),
            "voix": self.voix.moteur_actif,
            "ollama": self.cerveau.disponible,
            "modele": self.cerveau.modele,
            "uptime": round(time.time() - self.demarre_a),
            "telemetrie": self.systeme.telemetrie,
            "stats": self.memoire.statistiques(),
            "mode_leger": bool(self.config.get("mode_leger", False)),
        })
        return base

    def _alerte_materielle(self, message: str) -> None:
        self._diffuser("alerte", {"message": message})
        LOG.warning("Alerte matérielle : %s", message)

    # ==================================================================
    #  Point d'entrée universel
    # ==================================================================
    def traiter(self, texte: str, source: str = "texte", vocaliser: bool = True) -> str:
        """Traite une commande et retourne la réponse. Thread-safe."""
        texte = (texte or "").strip()
        if not texte:
            return ""

        registre = analyser_ton(texte)
        with self._verrou:
            self.derniere_commande = texte
            self.dernier_registre = registre

        self._diffuser("message", {"role": "user", "contenu": texte, "source": source})
        self.memoire.ajouter_message("user", texte, source, registre)

        self._definir_etat("reflexion")
        try:
            reponse = self._router(texte, registre)
        except Exception as erreur:
            LOG.exception("Erreur de traitement")
            self.memoire.journaliser("erreur", f"{type(erreur).__name__}: {erreur}")
            reponse = ("Quelque chose a cédé dans mon circuit de traitement. "
                       "Le détail est dans le journal.")

        with self._verrou:
            self.derniere_reponse = reponse
        self.memoire.ajouter_message("assistant", reponse, source, registre)
        self._diffuser("message", {"role": "assistant", "contenu": reponse, "source": source})

        if vocaliser and self.voix.moteur_actif != "muet":
            self._definir_etat("parole")
            self.voix.parler(reponse)
        self._definir_etat("veille")
        return reponse

    # ==================================================================
    #  Routage
    # ==================================================================
    def _router(self, texte: str, registre: str) -> str:
        commande = texte.lower().strip()

        # --- 1. Réflexes prioritaires ---------------------------------
        if re.search(r"\b(chut|silence|tais[- ]toi|ta gueule|stop)\b", commande):
            self.voix.taire()
            return "Je me tais."

        if re.search(r"\b(qui es[- ]tu|tu es qui|présente[- ]toi|presente[- ]toi)\b", commande):
            return (f"Je suis {self.nom}. Cerveau local, mémoire permanente, aucune donnée "
                    "qui sort de cette machine. Je vois ton matériel, je lis tes documents "
                    "et je réponds sans intermédiaire.")

        # --- 2. Télémétrie & diagnostic -------------------------------
        if re.search(r"\b(télémétrie|telemetrie|diagnostic|état système|etat systeme|"
                     r"cpu|processeur|mémoire vive|memoire vive|ram|batterie|"
                     r"température|temperature)\b", commande):
            if "processus" in commande or "gourmand" in commande:
                return self._processus_gourmands()
            return self.systeme.diagnostic()

        if "processus" in commande and re.search(r"\b(gourmand|lourd|top|liste)\b", commande):
            return self._processus_gourmands()

        if re.search(r"\b(composants|composant|quel processeur|quelle carte graphique|"
                     r"caractéristiques (?:de )?(?:mon |la )?(?:pc|machine|ordinateur))\b",
                     commande):
            return self.systeme.composants_parle()

        # --- 2 bis. Analyse et nettoyage du disque ---------------------
        if re.search(r"\b(analyse (?:mon )?(?:pc|ordinateur|disque)|"
                     r"fichiers? inutiles?|espace disque|place sur le disque|"
                     r"nettoie(?:r)? (?:mon )?(?:pc|ordinateur)?)\b", commande):
            if re.search(r"\b(nettoie|supprime|vide|libère|libere)\b", commande) and \
               "temporaires" in commande or "temp" in commande:
                return self._nettoyer_temporaires()
            return self._analyser_pc()

        if re.search(r"\bnettoie (?:les )?(?:fichiers )?temporaires\b", commande):
            return self._nettoyer_temporaires()

        # --- 2 ter. Étude d'image ---------------------------------------
        correspondance = re.search(
            r"\b(?:analyse|décris|decris|regarde|étudie|etudie) (?:cette |l'|l’)?image\s*:?\s*(.+)?",
            texte, flags=re.IGNORECASE)
        if correspondance:
            chemin = (correspondance.group(1) or "").strip().strip('"\'')
            if chemin:
                return self._analyser_image(chemin)
            return ("Donne-moi le chemin de l'image, ou dépose-la dans le dashboard "
                    "avec « analyse cette image : » suivi du chemin.")

        # --- 2 quater. Météo & actualités en direct --------------------
        correspondance = re.search(
            r"\b(?:météo|meteo|quel temps fait[- ]il|va[- ]t[- ]il pleuvoir)\b"
            r"(?:\s+(?:à|a|sur|pour)\s+(.+))?", commande)
        if correspondance:
            ville = (correspondance.group(1) or "").strip(" ?.!,") or None
            return self.meteo.resume_parle(ville)

        if re.search(r"\b(actualités|actualite|actualites|actus|infos du jour|"
                     r"quoi de neuf dans le monde|fil d'info|fil d'infos)\b", commande):
            return self.actualites.resume_parle()

        # --- 3. Bras web ----------------------------------------------
        correspondance = re.search(
            r"\b(?:ouvre|ouvrir|lance|va sur|affiche)\s+(.+)", commande)
        if correspondance:
            cible = correspondance.group(1).strip()
            recherche = ""
            separateur = re.search(r"\s+(?:et cherche|cherche|recherche|sur)\s+(.+)", cible)
            if separateur:
                recherche = separateur.group(1).strip()
                cible = cible[:separateur.start()].strip()
            return self.systeme.ouvrir_site(cible, recherche)

        correspondance = re.search(r"\b(?:cherche|recherche|google)\s+(.+)", commande)
        if correspondance:
            return self.systeme.rechercher(correspondance.group(1).strip())

        # --- 4. Mémoire explicite -------------------------------------
        # On repart du texte original : la casse des noms propres doit survivre.
        correspondance = re.search(
            r"\b(?:retiens|souviens[- ]toi|note)\s+(?:que\s+)?(.+)", texte, flags=re.IGNORECASE)
        if correspondance:
            return self._memoriser(correspondance.group(1).strip())

        if re.search(r"\b(que sais[- ]tu sur moi|mon profil|ce que tu sais de moi)\b", commande):
            return self._restituer_profil()

        if re.search(r"\b(oublie tout|efface la mémoire|efface la memoire|purge)\b", commande):
            self.memoire.purger_conversations()
            return "Historique des conversations effacé. Ton profil et tes documents restent."

        # --- 5. Documents ---------------------------------------------
        correspondance = re.search(r"\b(?:aspire|ingère|ingere|avale|analyse le dossier)\s+(.+)",
                                   commande)
        if correspondance:
            return self._aspirer(correspondance.group(1).strip().strip('"\''))

        if re.search(r"\b(mes documents|bibliothèque|bibliotheque|quels documents)\b", commande):
            return self.documents.inventaire()

        # --- 6. Sandbox -----------------------------------------------
        correspondance = re.search(
            r"\b(?:exécute|execute|lance le code|évalue|evalue)\s*(?:ce code|python)?\s*:?\s*(.+)",
            texte, flags=re.IGNORECASE | re.DOTALL)
        if correspondance and ("code" in commande or "python" in commande
                               or "calcule" in commande):
            return self.executer_code(correspondance.group(1).strip())

        # --- 6 bis. Rédaction de documents ------------------------------
        verbes_redaction = r"(?:crée|cree|écris|ecris|rédige|redige|génère|genere|prépare|prepare)"

        # Forme précise : "crée un [type de document] [sujet] (en [format])"
        correspondance = re.search(
            verbes_redaction + r"\s+(?:moi\s+)?(?:un|une|le|la)?\s*"
            r"(lettre de motivation|rapport|document|texte|"
            r"lettre|cv|fiche|présentation|presentation|tableau|checklist)\s+"
            r"(.+)", texte, flags=re.IGNORECASE)
        if correspondance:
            return self._rediger(correspondance.group(1), correspondance.group(2))

        # Forme relâchée : "crée un [format] [sujet]" — ex: "crée un word pompier".
        # Ici le format est cité directement à la place du type de document.
        correspondance = re.search(
            verbes_redaction + r"\s+(?:moi\s+)?(?:un|une|le|la)?\s*"
            r"(word|pdf|excel|csv|html|markdown)\s+(.+)",
            texte, flags=re.IGNORECASE)
        if correspondance:
            return self._rediger("document", f"{correspondance.group(2)} en {correspondance.group(1)}")

        # --- 6 ter. Génération de sons et de vidéos (limites assumées) --
        if re.search(r"\b(génère|genere|crée|cree) (?:un |une )?(son|bip|alerte sonore|"
                     r"vidéo|video|diaporama)\b", commande):
            return self._generer_media(texte, commande)

        # --- 7. Le cerveau prend le relais ----------------------------
        contexte = self.documents.contexte_pertinent(texte)
        reponse = self.cerveau.reflechir(texte, contexte_documents=contexte, registre=registre)

        # --- 8. Anticipation : une suggestion pertinente, jamais imposée -
        suggestion = self.anticipation.analyser(texte)
        if suggestion:
            reponse = f"{reponse}\n\n{suggestion}"
        return reponse

    # ==================================================================
    #  Actions dédiées
    # ==================================================================
    def _processus_gourmands(self) -> str:
        processus = self.systeme.processus_gourmands(5)
        if not processus:
            return "Impossible de lire la table des processus (psutil manquant)."
        details = ", ".join(f"{p['nom']} à {p['ram']}% de RAM" for p in processus)
        return f"Les cinq plus voraces : {details}."

    def _memoriser(self, fait: str) -> str:
        if not fait:
            return "Retenir quoi, exactement ?"
        correspondance = re.match(r"(?:mon|ma|mes)\s+([\wÀ-ÿ' -]+?)\s+(?:est|sont|c'est)\s+(.+)",
                                  fait, flags=re.IGNORECASE)
        if correspondance:
            cle, valeur = correspondance.group(1).strip(), correspondance.group(2).strip(" .")
        else:
            cle, valeur = f"note_{int(time.time())}", fait
        self.memoire.definir_profil(cle, valeur)
        return f"C'est gravé : {cle} — {valeur}."

    def _restituer_profil(self) -> str:
        profil = self.memoire.tout_le_profil()
        if not profil:
            return "Je ne sais rien de personnel sur toi. Dis « retiens que... » et ça changera."
        faits = " ; ".join(f"{cle} : {valeur}" for cle, valeur in list(profil.items())[:12])
        return f"Ce que je retiens de toi — {faits}."

    def _aspirer(self, chemin: str) -> str:
        import os
        self._definir_etat("ingestion")
        try:
            if os.path.isdir(chemin):
                bilan = self.documents.aspirer_dossier(chemin, resumer=False)
                if bilan.get("erreur"):
                    return f"Dossier inaccessible : {bilan['erreur']}."
                return (f"Dossier absorbé : {bilan['analyses']} fichier(s) ingéré(s), "
                        f"{bilan['ignores']} ignoré(s), {bilan['echecs']} en échec. "
                        "Tout est désormais interrogeable.")
            resultat = self.documents.ingerer_fichier(chemin, resumer=True)
            if resultat["succes"]:
                if resultat["resume"]:
                    return f"« {resultat['nom']} » assimilé. {resultat['resume']}"
                return (f"« {resultat['nom']} » assimilé : {resultat.get('caracteres', 0)} "
                        "caractères indexés. Le compte rendu attendra qu'Ollama soit en ligne.")
            return f"Impossible d'ingérer ce fichier : {resultat['erreur']}."
        finally:
            self._definir_etat("veille")

    def executer_code(self, code: str, langage: str = "python") -> str:
        resultat = self.sandbox.executer(code, langage)
        self.memoire.journaliser(
            "sandbox", f"langage={resultat['langage']} succes={resultat['succes']} refus={resultat['refus']}"
        )
        return Sandbox.formater(resultat)

    # ------------------------------------------------------------------
    #  Analyse et nettoyage du PC
    # ------------------------------------------------------------------
    def _analyser_pc(self) -> str:
        self._definir_etat("ingestion")  # réutilise l'état visuel "travail en cours"
        try:
            rapport = self.nettoyage.analyser()
            self.memoire.journaliser(
                "analyse_pc",
                f"{rapport['temporaires']['nombre_fichiers']} fichiers temporaires, "
                f"{len(rapport['gros_fichiers_oublies'])} gros fichiers oubliés",
            )
            return self.nettoyage.rapport_parle(rapport)
        finally:
            self._definir_etat("veille")

    def _nettoyer_temporaires(self) -> str:
        self._definir_etat("ingestion")
        try:
            resultat = self.nettoyage.nettoyer_temporaires()
            self.memoire.journaliser(
                "nettoyage",
                f"{resultat['fichiers_supprimes']} fichiers supprimés, "
                f"{self.nettoyage.formater_octets(resultat['octets_liberes'])} libérés",
            )
            libere = self.nettoyage.formater_octets(resultat["octets_liberes"])
            return (f"J'ai supprimé {resultat['fichiers_supprimes']} fichiers temporaires "
                    f"et libéré {libere}. {resultat['fichiers_ignores']} fichiers étaient "
                    "en cours d'utilisation et ont été laissés tranquilles, c'est normal.")
        finally:
            self._definir_etat("veille")

    # ------------------------------------------------------------------
    #  Étude d'image
    # ------------------------------------------------------------------
    def _analyser_image(self, chemin: str) -> str:
        if not os.path.isfile(chemin):
            return f"Je ne trouve pas d'image à cet emplacement : {chemin}"
        self._definir_etat("reflexion")
        try:
            resultat = self.vision.analyser(chemin)
            return self.vision.rapport_parle(resultat)
        finally:
            self._definir_etat("veille")

    def analyser_image_televersee(self, chemin: str, question: str = "") -> dict:
        """Utilisée par le dashboard web lors d'un dépôt d'image."""
        self._definir_etat("reflexion")
        try:
            return self.vision.analyser(chemin, question)
        finally:
            self._definir_etat("veille")

    # ------------------------------------------------------------------
    #  Rédaction de documents
    # ------------------------------------------------------------------
    def _rediger(self, nature: str, reste: str) -> str:
        """
        Analyse « crée un [nature] [sujet] en [format] » et produit le fichier.
        Le format peut être omis (Markdown par défaut) ou précisé n'importe où
        dans la phrase : "... en pdf", "... au format word", etc.
        """
        format_cible = "md"
        sujet = reste.strip()

        correspondance_format = re.search(
            r"\b(?:en|au format|format)\s+(word|pdf|excel|markdown|texte|html|csv|"
            r"docx|xlsx|txt)\b", reste, flags=re.IGNORECASE)
        if correspondance_format:
            format_cible = self.redaction.resoudre_format(correspondance_format.group(1)) or "md"
            sujet = (reste[:correspondance_format.start()] +
                     reste[correspondance_format.end():]).strip(" .,")

        if not sujet:
            return "Sur quel sujet exactement ? Précise après le type de document."

        self._definir_etat("reflexion")
        try:
            contexte = self.documents.contexte_pertinent(sujet)
            contenu = self.redaction.generer_contenu(sujet, nature=nature,
                                                      contexte_documents=contexte)
            resultat = self.redaction.creer(titre=sujet, contenu=contenu,
                                            format_cible=format_cible)
        finally:
            self._definir_etat("veille")

        if not resultat["succes"]:
            return f"Échec de la rédaction : {resultat['erreur']}"

        hote = self.config.get("web_hote", "127.0.0.1")
        port = self.config.get("web_port", 5000)
        lien = f"http://{hote}:{port}/api/fichier-genere/{resultat['id']}"
        avertissement = f" (note : {resultat['erreur']})" if resultat["erreur"] else ""
        return (f"« {resultat['nom_fichier']} » est prêt{avertissement}. "
                f"Lien de téléchargement : {lien}")

    # ------------------------------------------------------------------
    #  Génération de sons et de vidéos — limites assumées et annoncées
    # ------------------------------------------------------------------
    def _generer_media(self, texte: str, commande: str) -> str:
        if not hasattr(self, "media"):
            return "Le module de génération de sons/vidéos n'est pas chargé."

        if re.search(r"\b(son|bip|alerte sonore)\b", commande):
            style = "alerte" if "alerte" in commande else "notification"
            resultat = self.media.generer_son(style)
        else:
            sujet = re.sub(
                r"^.*?\b(?:vidéo|video|diaporama)\b(?: sur| de| pour)?\s*", "",
                texte, flags=re.IGNORECASE).strip(" .,:") or "sujet non précisé"
            contenu = self.cerveau.reflechir(
                f"Rédige, en français, le contenu d'un court diaporama sur : {sujet}. "
                "Une ligne '# Titre' puis 4 à 6 lignes '## ' pour chaque diapositive, "
                "une phrase courte par diapositive, rien d'autre.",
                registre="technique",
            )
            resultat = self.media.generer_video(sujet, contenu)

        if not resultat["succes"]:
            return f"Échec de la génération : {resultat['erreur']}"

        hote = self.config.get("web_hote", "127.0.0.1")
        port = self.config.get("web_port", 5000)
        lien = f"http://{hote}:{port}/api/fichier-genere/{resultat['id']}"
        precision = (" — c'est un diaporama simple, pas une vidéo animée par IA, "
                    "cette dernière n'existe pas gratuitement en local."
                    if resultat.get("format") == "mp4" else "")
        return f"« {resultat['nom_fichier']} » est prêt{precision}. Lien : {lien}"

    def interroger_document(self, doc_id: int, question: str) -> str:
        """Étude ciblée d'un document précis (utilisée par le dashboard web)."""
        self._definir_etat("reflexion")
        try:
            return self.documents.repondre_sur_document(doc_id, question)
        finally:
            self._definir_etat("veille")

    def ingerer(self, chemin: str, resumer: bool = True) -> dict:
        """Ingestion directe (utilisée par l'upload du dashboard web)."""
        self._definir_etat("ingestion")
        try:
            return self.documents.ingerer_fichier(chemin, resumer=resumer)
        finally:
            self._definir_etat("veille")

    def _ingestion_initiale(self) -> None:
        """Aspire au démarrage les dossiers listés dans config.json (ex: ancien Ultron)."""
        dossiers = self.config.get("ingestion_auto_dossiers") or []
        if not dossiers:
            return

        def travail():
            for dossier in dossiers:
                bilan = self.documents.aspirer_dossier(dossier, resumer=False)
                LOG.info("Ingestion auto de %s : %s", dossier, bilan)

        threading.Thread(target=travail, daemon=True, name="kryptonix-ingestion").start()

    # ==================================================================
    #  Boucle vocale (agent autonome d'écoute)
    # ==================================================================
    def demarrer_ecoute(self) -> bool:
        """Lance la boucle micro avec mot de réveil. Retourne False si pas de micro."""
        if not self.voix.micro_disponible:
            LOG.info("Écoute impossible : aucun micro exploitable.")
            return False
        if self._ecoute_active.is_set():
            return True
        self._ecoute_active.set()
        threading.Thread(target=self._boucle_vocale, daemon=True,
                         name="kryptonix-ecoute").start()
        LOG.info("Écoute vocale active — mot de réveil : « %s ».", self.config["mot_reveil"])
        return True

    def arreter_ecoute(self) -> None:
        self._ecoute_active.clear()

    def basculer_ecoute(self) -> bool:
        if self._ecoute_active.is_set():
            self.arreter_ecoute()
            return False
        return self.demarrer_ecoute()

    def _boucle_vocale(self) -> None:
        mot_reveil = self.config.get("mot_reveil", "kryptonix").lower()
        while self._ecoute_active.is_set() and not self._arret.is_set():
            # On n'écoute jamais pendant que l'on parle : évite l'auto-déclenchement.
            if self.voix.en_train_de_parler:
                time.sleep(0.2)
                continue

            self._definir_etat("ecoute")
            phrase = self.voix.ecouter()
            if not self._ecoute_active.is_set():
                break
            if not phrase:
                self._definir_etat("veille")
                continue

            minuscule = phrase.lower()
            if mot_reveil in minuscule:
                commande = re.sub(rf"\b{re.escape(mot_reveil)}\b", "", minuscule,
                                  count=1).strip(" ,.:!?")
                if not commande:
                    self.voix.parler("Je t'écoute.")
                    self._definir_etat("ecoute")
                    commande = self.voix.ecouter()
                if commande:
                    self.traiter(commande, source="voix")
            else:
                self._definir_etat("veille")
        self._definir_etat("veille")

    # ==================================================================
    def recharger_config(self, nouvelle: dict) -> None:
        """Applique et persiste une modification de configuration à chaud."""
        self.config.update(nouvelle)
        sauvegarder_config(self.config)
        self.cerveau.modele = self.config["ollama_modele"]
        self.cerveau.hote = self.config["ollama_hote"].rstrip("/")
        self.cerveau.verifier_connexion()
        self.voix.active = bool(self.config.get("voix_active", True))
        self.voix.recharger(self.config)
        if "ville_meteo" in nouvelle:
            self.meteo.oublier_ville(nouvelle["ville_meteo"])
        LOG.info("Configuration rechargée.")

    def arreter(self) -> None:
        LOG.info("Extinction de %s...", self.nom)
        self._arret.set()
        self.arreter_ecoute()
        self.systeme.arreter_surveillance()
        self.voix.arreter()
        self.memoire.journaliser("arret", "extinction propre")
        self.memoire.fermer()
