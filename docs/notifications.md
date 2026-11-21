# Notifications

[← Back to README](../README.md)

PruneMate can notify you after each run via Gotify, ntfy, Discord or Telegram. Configure it under
**Notifications** in the web UI and use **Save & send test notification** to check it.

- **Priority**: Low, Medium or High (each provider handles this differently, see below).
- **Only notify when something was pruned**: skip notifications for runs that removed nothing.
- Stored tokens are never shown again. Leave the field empty to keep the token, or click **Remove**.

## Gotify

1. Create an application in your Gotify server and copy its token.
2. In PruneMate: provider **Gotify**, URL `https://gotify.example.com`, and the app token.

Priority: Low = 2, Medium = 5, High = 8.

## ntfy

1. Pick a topic name, e.g. `prunemate-alerts`.
2. In PruneMate: provider **ntfy**, URL `https://ntfy.sh` (or your own server), and the topic.
3. Optional authentication:
   - an access token (recommended), or
   - `https://username:password@ntfy.example.com` in the URL.

Subscribe via `https://ntfy.sh/<topic>` or the ntfy app
([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) /
[iOS](https://apps.apple.com/app/ntfy/id1625396347)).

Priority: Low = 2, Medium = 3, High = 5.

## Discord

1. In your Discord server: **Server settings → Integrations → Webhooks → New Webhook**.
2. Copy the webhook URL (`https://discord.com/api/webhooks/...`) into PruneMate.

Priority sets the color: Low = green, Medium = orange, High = red.

## Telegram

1. Message **@BotFather**, send `/newbot` and copy the bot token (`123456789:ABC...`).
2. Get your chat ID by messaging **@userinfobot**, or message your bot and open
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` (look for `"chat":{"id":...}`).
3. In PruneMate: provider **Telegram**, the bot token and the chat ID.

For groups, add the bot to the group and use the group ID (starts with `-`). For channels, use `@channelname`.

Priority: Low = silent, Medium/High = with sound.
