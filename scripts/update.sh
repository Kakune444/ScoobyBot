#!/bin/bash
# Récupère les derniers changements du repo et redémarre le bot.
# Prévu pour tourner sur le serveur (VM), pas en local.
set -e

cd "$(dirname "$0")/.."
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart scoobybot
