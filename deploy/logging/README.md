# Logging conventions
#
# - systemd units use StandardOutput=journal → journalctl -u <unit>
# - apps also write RotatingFileHandler under $CENACOLO_HOME/var/log/
# - Never log PAN/CVV/cookies/captcha tokens
# - Prefer key=value fields: order_no= stage= code=
