# Tempora

Pointage des horaires de salariés par **lien personnel** (QR code ou tag NFC), pour une petite structure. Une seule page web, un gros bouton Arrivée / Départ, une page d'administration avec récapitulatif mensuel et export CSV.

Python 3 · Flask · SQLite · aucun service externe. Conçu pour tourner sur un Raspberry Pi, sur le réseau local.

## Fonctionnement

- Chaque salarié a un **lien personnel** (`/p/<jeton>`), ouvert via un QR code ou un tag NFC (NTAG) qui contient l'URL.
- La page affiche le statut (en poste / hors poste), un bouton Arrivée ou Départ, les heures de la semaine et les derniers pointages.
- L'horodatage est pris **côté serveur** (fuseau Europe/Paris) : il ne dépend pas de l'horloge du téléphone.
- Un second appui à moins de 20 secondes, ou depuis une page périmée, n'enregistre rien.
- L'administration (`/admin`, authentification HTTP Basic) permet de consulter le mois par salarié et par jour, d'ajouter ou de supprimer un pointage, et d'exporter en CSV (`/admin/export.csv?m=AAAA-MM`, séparateur `;`, lisible directement dans Excel).
- Toute correction manuelle est enregistrée dans un **journal des modifications** affiché en bas de la page admin.
- Les oublis (arrivée sans départ, ou l'inverse) sont signalés par « ⚠ incomplet » et ne comptent pas dans les totaux.

## Installation (Raspberry Pi OS / Debian)

```bash
sudo apt install -y python3-venv
mkdir -p ~/tempora && cd ~/tempora        # y placer app.py
python3 -m venv venv
./venv/bin/pip install flask waitress
```

## Configuration

Tout se règle par variables d'environnement :

| Variable | Rôle | Défaut |
|---|---|---|
| `TEMPORA_ADMIN_PASSWORD` | Mot de passe de l'admin (**obligatoire** pour accéder à `/admin`) | *(vide : admin inaccessible)* |
| `TEMPORA_ADMIN_USER` | Identifiant de l'admin | `jerome` |
| `TEMPORA_DB` | Chemin de la base SQLite | `tempora.db` |
| `TEMPORA_ALLOWED_NETS` | Réseaux autorisés à pointer, ex. `192.168.1.0/24` (plusieurs : séparés par des virgules) | vide : pas de restriction |
| `TEMPORA_BASE_URL` | URL de base affichée dans les liens, ex. `http://192.168.1.50:8080` | URL de la requête |
| `TEMPORA_PORT` | Port d'écoute (mode `serve`) | `8080` |

Un modèle est fourni dans `tempora.env.example`.

## Utilisation

Dans le dossier du projet, avec les variables chargées :

```bash
./venv/bin/python app.py add "Prénom"   # crée un salarié et affiche son lien personnel
./venv/bin/python app.py list           # liste les salariés et leurs liens
./venv/bin/python app.py disable <id>   # désactive un salarié (son lien cesse de fonctionner)
./venv/bin/python app.py serve          # serveur de test (port 8080)
```

Pour fabriquer le QR code : `qrencode -o qr.png "http://<adresse>:8080/p/<jeton>"` (paquet `qrencode`), ou écrire l'URL sur un tag NTAG avec une application NFC.

## Démarrage automatique (systemd)

Le fichier `tempora.service` lance l'application avec **waitress** au démarrage et la relance en cas de plantage.

```bash
sudo cp tempora.env.example /etc/tempora.env      # puis l'éditer
sudo chmod 600 /etc/tempora.env                   # contient le mot de passe admin
sudo cp tempora.service /etc/systemd/system/tempora.service
# adapter User=, WorkingDirectory=, PYTHONPATH= et ExecStart= à votre installation
sudo systemctl daemon-reload
sudo systemctl enable --now tempora
systemctl status tempora
```

Journaux : `journalctl -u tempora -f`.

Pour utiliser les commandes `add` / `list` alors que `/etc/tempora.env` est en lecture réservée à root :

```bash
set -a; . <(sudo cat /etc/tempora.env); set +a
```

## Sauvegarde

Toutes les données sont dans le fichier SQLite (`TEMPORA_DB`). Copiez-le régulièrement ailleurs (cron hebdomadaire, par exemple). Les pointages font office de preuve du temps de travail : conservez l'historique.

## Sécurité et données personnelles

- **Ne jamais versionner** `tempora.db` (noms, jetons d'accès, horaires) ni le fichier d'environnement réel. Le `.gitignore` fourni les exclut.
- Un lien personnel permet de pointer pour la personne concernée : traitez-le comme un secret. En cas de fuite, désactivez le salarié (`disable`) et recréez-le (`add`).
- Sans HTTPS, le lien circule en clair : à réserver à un réseau local privé, ou à placer derrière un reverse proxy ou un tunnel chiffré (Caddy, Tailscale, Cloudflare Tunnel). Derrière un proxy, `TEMPORA_ALLOWED_NETS` verra l'adresse du proxy et non celle du client.
- Les heures de travail sont des données personnelles (RGPD) : informez les salariés de ce qui est enregistré et de la durée de conservation. Si le téléphone personnel du salarié est utilisé, son accord est nécessaire.

## Limites connues

- Une période à cheval sur minuit ou sur deux mois est signalée « incomplet » et doit être corrigée à la main.
- Le fuseau horaire (Europe/Paris) est fixé dans le code.
- Pas de gestion des pauses, des heures supplémentaires ni des absences : l'outil enregistre des plages de présence.

## Licence

MIT
