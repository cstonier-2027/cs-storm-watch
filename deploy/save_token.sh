#!/bin/zsh
# Saves a Cloudflare API token for deploy.sh without echoing it.
F="$HOME/.config/stormwatch/cloudflare.env"
mkdir -p "${F:h}" && chmod 700 "${F:h}"
read -rs "T?Paste your Cloudflare API token, then press Return: "
echo
if [ -z "$T" ]; then echo "Nothing pasted; not saved."; exit 1; fi
umask 077
printf 'CLOUDFLARE_API_TOKEN=%s\n' "$T" > "$F"
unset T
echo "Saved. You can close this tab."
