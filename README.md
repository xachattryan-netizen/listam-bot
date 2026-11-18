# list.am → Telegram բոտ

Ամեն 10 րոպեն մեկ (GitHub Actions) ստուգում է list.am-ի որոնումը և նոր հայտարարությունները ուղարկում Telegram-ով։

- Որոնումը՝ `config.json` → `searches`
- Token / chat_id՝ GitHub → Settings → Secrets and variables → Actions → `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Ձեռքով գործարկել՝ Actions → list.am watcher → Run workflow
- Հաճախականությունը՝ `.github/workflows/listam.yml` → `cron`
