#!/bin/sh

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set" >&2
  exit 1
fi

send_telegram() {
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\":\"${TELEGRAM_CHAT_ID}\",\"text\":\"$1\",\"parse_mode\":\"HTML\"}" \
    > /dev/null || echo "WARNING: Telegram notification failed"
}

echo "Watchdog started. Monitoring 'autobot' container..."

# Outer loop reconnects if docker events exits (e.g. Docker daemon restart).
while true; do
  docker events \
    --filter "container=autobot" \
    --filter "event=die" \
    --format "{{.Actor.Attributes.exitCode}}" | while IFS= read -r exit_code; do

    TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

    if [ "$exit_code" = "0" ]; then
      MSG="&#x1F7E1; <b>Autobot stopped</b> (graceful shutdown)&#10;${TIMESTAMP}"
    elif [ -z "$exit_code" ]; then
      MSG="&#x1F534; <b>Autobot exited</b> (exit code unknown — possible OOM kill)&#10;${TIMESTAMP}&#10;Restarting automatically..."
    else
      MSG="&#x1F534; <b>Autobot crashed</b> (exit code: ${exit_code})&#10;${TIMESTAMP}&#10;Restarting automatically..."
    fi

    echo "Container exited (code=${exit_code:-unknown}) — sending Telegram alert"
    send_telegram "$MSG"
  done

  echo "docker events stream ended — reconnecting in 5s..."
  sleep 5
done
