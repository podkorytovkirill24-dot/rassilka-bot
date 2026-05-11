# Deploy (hosting)

## 1) Environment variables
Set these variables in hosting panel:
- API_ID
- API_HASH
- SESSION_NAME (example: userbot_session)
- BOT_TOKEN
- ADMIN_IDS (example: 123456789 or 123,456)
- DELAY_MIN
- DELAY_MAX
- JOIN_DELAY_MIN
- JOIN_DELAY_MAX
- DB_PATH (example: sender.db)

## 2) Install and run
If hosting supports Procfile, it will run:
`worker: python userbot_sender.py`

Manual run:
```bash
pip install -r requirements.txt
python userbot_sender.py
```

## 3) Persistent storage
Use persistent disk/volume for:
- `*.session`
- `sender.db`

Without persistent storage, Telegram session and history will be lost after restart.

## 4) First start
At first launch Pyrogram may request login/confirmation for user session in console logs.
Complete authorization once, then session files will be reused.
