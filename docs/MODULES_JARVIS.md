# Modules « Jarvis » de Kryptonix

Tout ce qui a été ajouté vit dans `noyau/extensions/` (un fichier par domaine). **Rien de l'existant n'a été retiré** :
les 352 fonctions/classes d'origine sont toujours là, toutes les routes web d'origine aussi. Si aucun module ne
reconnaît une phrase, le noyau d'origine répond exactement comme avant.

Tableau de bord : **http://127.0.0.1:5000/panneau** (modes IA, voix, permissions, sons, réglages de tous les modules)
et **/finances** (cockpit financier local). Les deux sont aussi accessibles par les boutons « panneau » / « finances » en haut de l'application.

## Ce qui marche tout de suite (sans rien configurer)
Sons (46 effets), personnalités, minuteurs / rappels / chronomètre / heure, mémoire à long terme (proches, projets, préférences),
contrôle du PC (applications, volume, médias, stats CPU/RAM/GPU), sécurité N1/N2/N3, cockpit financier (import CSV),
pipeline vidéo, recherche web (DuckDuckGo), résumé de page web, agenda local.

## Ce qui demande UN réglage (panneau → Réglages des modules)
| Module | À renseigner | Dis ensuite |
|---|---|---|
| Philips Hue | `hue_pont` (IP du pont), appuie sur son bouton | « associe le pont Hue » puis « allume le salon » |
| Présence | `presence_actif`, `presence_telephones` (IP du téléphone) | scènes automatiques départ/retour |
| Réveil du PC | `wol_machines` = `{"bureau": "AA:BB:CC:DD:EE:FF"}` | « réveille le PC du bureau » |
| Google Agenda | `python scripts/google_autorisation.py ID SECRET` | « qu'est-ce que j'ai demain ? » |
| Agendas iCal | `agenda_ical` = `[{"nom":"Sport","url":"https://…/basic.ics"}]` | idem (lecture seule) |
| Gmail | `gmail_adresse` + `gmail_mot_de_passe_app` | « résume mes mails » |
| Discord | `discord_token`, `discord_salons`, `discord_utilisateur_id` | « résume discord aujourd'hui » |
| Instagram | `instagram_comptes` (id compte pro + jeton) | « combien d'abonnés sur instagram » |
| Twilio | `twilio_sid/token/numero`, `twilio_contacts`, `twilio_url_publique` | « appelle Maman et dis-lui… » |
| OBS | activer le serveur WebSocket d'OBS + `obs_mot_de_passe` | « lance l'enregistrement » |
| IA cloud | `cles_api`, `mode_ia` = hybride / qualite, `budget_mensuel` | « passe en mode hybride » |
| Réseau local | `reseau_actif: true` | mobile, satellites, MCP distant |

## Sécurité graduée
* **N1** libre · **N2** « je confirme ? » · **N3** confirmation stricte **+ 10 s pour annuler** (extinction/redémarrage du PC, suppression d'événement, appel téléphonique, envoi de mail, validation d'une réservation).
* Confirmation vocale **stricte** : « non », « pas », « annule », « surtout pas », « oui mais non »… n'exécutent **rien**. Une réponse ambiguë annule.
* « Toujours autoriser » : possible pour N2 uniquement, révocable (« révoque les autorisations »). **Un N3 n'est jamais permanent.**

## Modes IA et budget
`local` (Ollama, défaut) · `hybride` (cloud rapide pour la conversation, local pour le privé) · `qualite` (modèle cloud le plus puissant).
Fournisseur indépendant de la voix. Coûts suivis par appel, **plafond mensuel → bascule automatique en local**, retour local immédiat si le cloud tombe.
Tes **documents, mails, messages Discord, captures d'écran et finances ne partent jamais vers le cloud** (sauf `cloud_documents: true` pour les documents).
Les tarifs par défaut sont indicatifs : ajuste `prix_par_million` selon ton fournisseur.

## Multi-pièces, mobile, MCP
* `reseau_actif: true` démarre un petit serveur (port 5055) **séparé** du tableau de bord, protégé par jeton + anti force brute.
* **Satellite** (Raspberry Pi…) : copie `satellite/kryptonix_satellite.py` dans la pièce, puis `python3 kryptonix_satellite.py --cerveau http://IP_DU_PC:5055 --jeton XXXX --piece cuisine`. « Dis à la cuisine que le repas est prêt ».
* **iPhone / Siri** : app Raccourcis → « Obtenir le contenu de l'URL » en POST vers `http://IP_DU_PC:5055/mobile/commande` (en-tête `Authorization: Bearer JETON`, corps JSON `{"texte": "…"}`), puis « Énoncer le texte ». Mêmes routes pour `/mobile/note` et `/mobile/idee`.
* **MCP / Claude Desktop** : copie la configuration affichée dans le panneau (section Réseau). Seuls domotique, PC, OBS, agenda (lecture), minuteurs, mémoire sont exposés ; **finances, mails, appels, écran, réseaux sociaux ne le sont jamais** ; les actions N3 sont impossibles via MCP.
* **Twilio** : le tunnel (ngrok…) doit pointer vers le port **5055**, jamais vers 5000.

## Limites honnêtes (ce que je n'ai pas pu vérifier ici)
Tout est testé automatiquement (voir ci-dessous) avec de faux serveurs fidèles aux protocoles, mais je n'ai évidemment pas **ton matériel ni tes comptes** :
* Micro, wake word openWakeWord, Whisper, webcam/gestes, vrai OBS, vrai pont Hue, vrais comptes Google/Discord/Instagram/Twilio/Gmail → à valider chez toi. Chaque module te dit en français ce qui manque ou échoue.
* « Hey Kryptonix » comme mot d'activation local nécessite un modèle openWakeWord entraîné (le projet fournit « hey_jarvis » ; renseigne le chemin d'un modèle perso dans `wakeword_modele`). L'éveil par transcription « Kryptonix » reste actif en secours.
* Réservations web : remplissage **heuristique** (les sites changent) ; la validation finale reste toujours manuelle/N3.
* Gestes : MediaPipe précis si `mediapipe==0.10.14`, sinon repli mouvement simple.
* Vault TikTok/Instagram : dépend de `yt-dlp` et de ffmpeg ; ces plateformes bloquent parfois les téléchargements.
* Chrome pilotable : Chrome récent exige un profil dédié (créé automatiquement) ; connecte-toi une fois à tes sites.
* Le premier lancement fabrique les 46 sons en arrière-plan (~4 s) ; ils sont réutilisés ensuite.

## Tests
`python tests/run_all.py` (≈ 70 s) : 138 tests couvrant sécurité, routage IA/budget, Hue (faux pont), agenda (faux Google + iCal), mails (faux IMAP/SMTP), Discord/Instagram, Twilio (signature officielle), OBS (faux serveur WebSocket v5 avec authentification), MCP, agent, réseau/satellites/mobile, finances, pipeline, gestes, wake word, routes web.
