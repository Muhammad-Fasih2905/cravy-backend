# Cravy backend: free, permanent deploy (Render + Neon)

Render = sirf code chalata hai (free, disk temporary hoti hai). Neon = asli data (orders, menu, admin, photos) ek alag jagah.
Deploy sirf Render ka code badalta hai. Data Neon mein hai, isliye koi deploy usay chhoo hi nahi sakta.

## 1. Neon (database, free, expire nahi hota)
1. neon.com par signup (card nahi chahiye) -> New Project -> region Singapore/nearest.
2. "Connection string" copy karo (postgresql://...neon.tech/neondb?sslmode=require). Ye DATABASE_URL hai.

## 2. GitHub
Is folder ko PRIVATE GitHub repo mein push karo. `.env`, `venv`, `cravy.db` upload nahi honge (.gitignore).

## 3. Render (backend, free)
1. render.com -> New -> Blueprint -> repo chuno (render.yaml khud settings bhar dega).
2. Environment mein ye 3 bharo: DATABASE_URL (Neon wali), ADMIN_EMAIL, ADMIN_PASSWORD.
3. Deploy. Health check /health. Pehli dafa menu + admin khud ban jayega, demo data nahi.
4. Frontend (Netlify) mein API URL Render wala daalo.

## 4. Sleep fix (free)
Render free 15 min idle ke baad so jata hai (pehli request ~1 min). uptimerobot.com (free) -> HTTP monitor -> https://<tumhara-render-url>/health, har 5 min.

## 5. Backup (free)
GitHub repo -> Settings -> Secrets -> BACKUP_DATABASE_URL = Neon ka plain postgresql:// string. .github/workflows/backup.yml har Sunday dump banata hai (Actions -> Artifacts, 90 din).

## Local aur live alag kaise rehte hain
- PC par APP_ENV set nahi + DATABASE_URL=sqlite:///./cravy.db -> sirf cravy.db use hoti hai.
- Agar PC se galti se live DB ka URL lag jaye -> app start hi nahi hoti (check_environment).
- Live par APP_ENV=production -> SQLite par girna band: URL galat ho to app start hi nahi hoti, data chupke se gum nahi hota.
- Live par demo seed (DEMO10 coupon, Demo items) kabhi nahi chalta.
- Local testing data live mein kabhi nahi jata (cravy.db git mein nahi). Naya code deploy = sirf code.

## Purani uploads/photos
Photos ab Postgres mein hoti hain (STORAGE_BACKEND=db). Purani files ho to: ALLOW_REMOTE_DB_IN_DEV=true python -m app.migrate_uploads
