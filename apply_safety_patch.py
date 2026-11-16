"""Cravy safety patch. Run from the ROOT of your repo (the folder that contains `app/`):
    python apply_safety_patch.py
It changes nothing unless EVERY edit matches your current files (all-or-nothing), and is safe to run twice."""
import sys
from pathlib import Path

staged, crlf = {}, {}

def _load(path):
    if path not in staged:
        raw = Path(path).read_bytes().decode()
        crlf[path] = "\r\n" in raw
        staged[path] = raw.replace("\r\n", "\n")
    return staged[path]

def edit(path, old, new, count=1):
    txt = _load(path)
    if txt.count(old) != count:
        sys.exit(f"STOPPED, nothing was changed. {path}: expected text not found ({old[:60]!r}). "
                 "Your file differs from the one I patched; send me that file.")
    staged[path] = txt.replace(old, new)

if not Path("app/db.py").exists():
    sys.exit("Run this from the repo root (the folder that contains app/).")
if "def check_environment" in Path("app/db.py").read_text():
    sys.exit("Already applied. Nothing to do.")

# ---------- config.py ----------
edit("app/config.py",
'''    database_url: str = "sqlite:///./cravy.db"
''',
'''    # APP_ENV=production on the live host (Render etc.), nothing (= development) on your own PC.
    # production: the app REFUSES to start on a throw-away SQLite file and never loads demo data.
    # development: the app REFUSES to start if DATABASE_URL points at a remote (live) database.
    app_env: str = "development"
    # "disk" = uploads go to UPLOAD_DIR (local PC). "db" = uploads are stored inside Postgres, so they survive
    # redeploys on hosts whose disk is wiped (Render free). Set STORAGE_BACKEND=db on the live host.
    storage_backend: str = "disk"
    allow_remote_db_in_dev: bool = False  # only for a deliberate one-off maintenance run from your PC
    database_url: str = "sqlite:///./cravy.db"
''')
edit("app/config.py",
'''

settings = Settings()''',
'''
    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"


settings = Settings()''')

# ---------- db.py ----------
edit("app/db.py",
'''_url = _resolve_url()

_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, connect_args=_args, pool_pre_ping=True)
''',
'''def check_environment(url: str, production: bool, allow_remote_dev: bool = False) -> None:
    """Keep the live data and the local/test data apart.
    * production + SQLite  -> stop: a SQLite file on the host is wiped on every deploy (that is how live data gets lost).
    * development + remote database -> stop: your PC (tests, seeds, experiments) must never write into the live database."""
    u = make_url(url)
    is_sqlite = u.get_backend_name() == "sqlite"
    if production and is_sqlite:
        raise RuntimeError(
            "APP_ENV=production but DATABASE_URL is missing/invalid, so the app would use a temporary SQLite file "
            "and lose all data on the next deploy. Set DATABASE_URL to your Postgres (Neon) connection string."
        )
    if not production and not is_sqlite and (u.host or "") not in ("localhost", "127.0.0.1", "::1") and not allow_remote_dev:
        raise RuntimeError(
            f"Refusing to start: this is a development run (APP_ENV is not 'production') but DATABASE_URL points at "
            f"the remote database '{u.host}'. Use sqlite:///./cravy.db locally. Set APP_ENV=production on the live host."
        )


_url = _resolve_url()
check_environment(_url, settings.is_production, settings.allow_remote_db_in_dev)

_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
_pool = {} if _url.startswith("sqlite") else {"pool_recycle": 240}  # Neon suspends idle connections; renew them before that
engine = create_engine(_url, connect_args=_args, pool_pre_ping=True, **_pool)
''')

# ---------- models.py ----------
edit("app/models.py",
"from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint",
"from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint")
edit("app/models.py",
'''class AppMeta(Base):''',
'''class StoredFile(Base):
    """An uploaded file (product photo / payment screenshot) kept inside the database (STORAGE_BACKEND=db),
    so it survives redeploys on hosts with a temporary disk."""
    __tablename__ = "stored_files"
    subdir: Mapped[str] = mapped_column(String(20), primary_key=True)  # "products" or "receipts"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(60), default="application/octet-stream")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AppMeta(Base):''')

# ---------- uploads.py ----------
edit("app/uploads.py",
'''import secrets
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import settings
''',
'''import secrets

from fastapi import HTTPException, UploadFile

from . import storage
''')
edit("app/uploads.py",
'''    folder = Path(settings.upload_dir) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ext
    (folder / name).write_bytes(data)
    return name''',
'''    name = secrets.token_hex(16) + ext
    storage.put(subdir, name, data)
    return name''')

# ---------- admin.py receipt ----------
edit("app/routers/admin.py",
'''    return FileResponse(Path(cfg.upload_dir) / "receipts" / o.payment.receipt_path)''',
'''    got = storage.get("receipts", o.payment.receipt_path)
    if got is None:
        raise HTTPException(404, "The receipt file is missing")
    return Response(got[0], media_type=got[1])''')
edit("app/routers/admin.py",
"from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse",
"from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse")
edit("app/routers/admin.py",
"from ..uploads import save_upload\n",
"from ..uploads import save_upload\nfrom .. import storage\n")

# ---------- main.py ----------
edit("app/main.py",
'''Path(settings.upload_dir, "products").mkdir(parents=True, exist_ok=True)
app.mount("/media/products", StaticFiles(directory=Path(settings.upload_dir) / "products"), name="product-media")  # receipts are NOT served
''',
'''if settings.storage_backend.strip().lower() == "db":  # photos live in the database, so a redeploy never loses them
    from fastapi import HTTPException
    from fastapi.responses import Response
    from . import storage

    @app.get("/media/products/{name}", include_in_schema=False)
    def _product_media(name: str):
        got = storage.get("products", name)
        if got is None:
            raise HTTPException(404, "Not found")
        return Response(got[0], media_type=got[1], headers={"Cache-Control": "public, max-age=31536000, immutable"})  # names are random: a changed photo gets a new name
else:
    Path(settings.upload_dir, "products").mkdir(parents=True, exist_ok=True)
    app.mount("/media/products", StaticFiles(directory=Path(settings.upload_dir) / "products"), name="product-media")  # receipts are NOT served
''')
edit("app/main.py",
'''        if db.scalar(select(models.Category.id).limit(1)) is None:
            seed.run()''',
'''        if db.scalar(select(models.Category.id).limit(1)) is None and not settings.is_production:
            seed.run()  # demo menu + DEMO10 coupon: local/dev only, never on the live database''')

NEW = {
    'app/storage.py': '"""Where uploaded files (product photos, payment screenshots) are kept.\n\nSTORAGE_BACKEND=disk (default, your PC): files go to UPLOAD_DIR.\nSTORAGE_BACKEND=db   (live host)       : files go into the Postgres table `stored_files`, so they are as safe as the\n                                         orders themselves and survive every redeploy (the host\'s disk is wiped).\nReading looks in the database first (db mode) and then on disk, so nothing already on disk is orphaned."""\nfrom pathlib import Path\n\nfrom .config import settings\n\n_TYPES = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}\n\n\ndef use_db() -> bool:\n    return settings.storage_backend.strip().lower() == "db"\n\n\ndef content_type(name: str) -> str:\n    return _TYPES.get(Path(name).suffix.lower(), "application/octet-stream")\n\n\ndef put(subdir: str, name: str, data: bytes) -> None:\n    if use_db():\n        from . import models\n        from .db import SessionLocal\n\n        with SessionLocal() as db:\n            db.merge(models.StoredFile(subdir=subdir, name=name, content_type=content_type(name), data=data))\n            db.commit()\n        return\n    folder = Path(settings.upload_dir) / subdir\n    folder.mkdir(parents=True, exist_ok=True)\n    (folder / name).write_bytes(data)\n\n\ndef get(subdir: str, name: str) -> tuple[bytes, str] | None:\n    name = Path(name).name  # never allow a path\n    if use_db():\n        from . import models\n        from .db import SessionLocal\n\n        with SessionLocal() as db:\n            row = db.get(models.StoredFile, (subdir, name))\n            if row is not None:\n                return bytes(row.data), row.content_type\n    f = Path(settings.upload_dir) / subdir / name\n    if f.is_file():\n        return f.read_bytes(), content_type(name)\n    return None\n',
    'app/migrate_uploads.py': '"""One-off helper: copy files that are already in UPLOAD_DIR (products/ and receipts/) into the database.\nOnly adds what is missing; never overwrites. Run on a machine that has both the files and the live DATABASE_URL:\n    ALLOW_REMOTE_DB_IN_DEV=true python -m app.migrate_uploads\n"""\nfrom pathlib import Path\n\nfrom . import models\nfrom .config import settings\nfrom .db import Base, SessionLocal, engine\nfrom .storage import content_type\n\nBase.metadata.create_all(engine)\nadded = 0\nwith SessionLocal() as db:\n    for sub in ("products", "receipts"):\n        folder = Path(settings.upload_dir) / sub\n        if not folder.is_dir():\n            continue\n        for f in folder.iterdir():\n            if f.is_file() and db.get(models.StoredFile, (sub, f.name)) is None:\n                db.add(models.StoredFile(subdir=sub, name=f.name, content_type=content_type(f.name), data=f.read_bytes()))\n                added += 1\n    db.commit()\nprint(f"Copied {added} file(s) into the database.")\n',
    'tests/test_safety.py': 'import pytest\n\nfrom app.db import check_environment\n\nSQLITE = "sqlite:///./cravy.db"\nNEON = "postgresql+psycopg://u:p@ep-cool-123.us-east-2.aws.neon.tech/neondb?sslmode=require"\nLOCAL_PG = "postgresql+psycopg://u:p@localhost:5432/cravy"\n\n\ndef test_production_refuses_sqlite():\n    with pytest.raises(RuntimeError):\n        check_environment(SQLITE, production=True)\n\n\ndef test_production_accepts_postgres():\n    check_environment(NEON, production=True)\n\n\ndef test_dev_refuses_remote_database():\n    with pytest.raises(RuntimeError):\n        check_environment(NEON, production=False)\n\n\ndef test_dev_allows_sqlite_and_local_postgres():\n    check_environment(SQLITE, production=False)\n    check_environment(LOCAL_PG, production=False)\n\n\ndef test_dev_remote_only_with_explicit_override():\n    check_environment(NEON, production=False, allow_remote_dev=True)\n',
    'render.yaml': 'services:\n  - type: web\n    name: cravy-api\n    runtime: python\n    plan: free\n    region: ohio\n    buildCommand: pip install -r requirements.txt\n    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT\n    healthCheckPath: /health\n    envVars:\n      - key: PYTHON_VERSION\n        value: 3.13.5\n      - key: APP_ENV\n        value: production\n      - key: STORAGE_BACKEND\n        value: db\n      - key: SECRET_KEY\n        generateValue: true\n      - key: DATABASE_URL\n        sync: false\n      - key: ADMIN_EMAIL\n        sync: false\n      - key: ADMIN_PASSWORD\n        sync: false\n      - key: CORS_ORIGINS\n        value: https://eatcravy.netlify.app\n',
    '.github/workflows/backup.yml': 'name: weekly-db-backup\non:\n  schedule:\n    - cron: "0 2 * * 0"   # every Sunday\n  workflow_dispatch: {}\njobs:\n  backup:\n    runs-on: ubuntu-latest\n    steps:\n      - name: Dump the live database\n        env:\n          DATABASE_URL: ${{ secrets.BACKUP_DATABASE_URL }}   # plain postgresql://... string from Neon\n        run: docker run --rm postgres:17 pg_dump "$DATABASE_URL" --no-owner -Fc > cravy-$(date +%F).dump\n      - uses: actions/upload-artifact@v4\n        with:\n          name: cravy-db-backup\n          path: cravy-*.dump\n          retention-days: 90\n',
    'DEPLOY.md': '# Cravy backend: free, permanent deploy (Render + Neon)\n\nRender = sirf code chalata hai (free, disk temporary hoti hai). Neon = asli data (orders, menu, admin, photos) ek alag jagah.\nDeploy sirf Render ka code badalta hai. Data Neon mein hai, isliye koi deploy usay chhoo hi nahi sakta.\n\n## 1. Neon (database, free, expire nahi hota)\n1. neon.com par signup (card nahi chahiye) -> New Project -> region Singapore/nearest.\n2. "Connection string" copy karo (postgresql://...neon.tech/neondb?sslmode=require). Ye DATABASE_URL hai.\n\n## 2. GitHub\nIs folder ko PRIVATE GitHub repo mein push karo. `.env`, `venv`, `cravy.db` upload nahi honge (.gitignore).\n\n## 3. Render (backend, free)\n1. render.com -> New -> Blueprint -> repo chuno (render.yaml khud settings bhar dega).\n2. Environment mein ye 3 bharo: DATABASE_URL (Neon wali), ADMIN_EMAIL, ADMIN_PASSWORD.\n3. Deploy. Health check /health. Pehli dafa menu + admin khud ban jayega, demo data nahi.\n4. Frontend (Netlify) mein API URL Render wala daalo.\n\n## 4. Sleep fix (free)\nRender free 15 min idle ke baad so jata hai (pehli request ~1 min). uptimerobot.com (free) -> HTTP monitor -> https://<tumhara-render-url>/health, har 5 min.\n\n## 5. Backup (free)\nGitHub repo -> Settings -> Secrets -> BACKUP_DATABASE_URL = Neon ka plain postgresql:// string. .github/workflows/backup.yml har Sunday dump banata hai (Actions -> Artifacts, 90 din).\n\n## Local aur live alag kaise rehte hain\n- PC par APP_ENV set nahi + DATABASE_URL=sqlite:///./cravy.db -> sirf cravy.db use hoti hai.\n- Agar PC se galti se live DB ka URL lag jaye -> app start hi nahi hoti (check_environment).\n- Live par APP_ENV=production -> SQLite par girna band: URL galat ho to app start hi nahi hoti, data chupke se gum nahi hota.\n- Live par demo seed (DEMO10 coupon, Demo items) kabhi nahi chalta.\n- Local testing data live mein kabhi nahi jata (cravy.db git mein nahi). Naya code deploy = sirf code.\n\n## Purani uploads/photos\nPhotos ab Postgres mein hoti hain (STORAGE_BACKEND=db). Purani files ho to: ALLOW_REMOTE_DB_IN_DEV=true python -m app.migrate_uploads\n',
    '.env.example': "# ===== LOCAL (your PC) =====  copy this file to .env  (the real .env must NEVER be uploaded to GitHub)\n# Leave APP_ENV unset locally. Locally the app uses its own SQLite file and refuses to touch a remote database.\nDATABASE_URL=sqlite:///./cravy.db\nCORS_ORIGINS=http://localhost:3000\nSECRET_KEY=change-me\nADMIN_EMAIL=owner@example.com\nADMIN_PASSWORD=choose-a-strong-password\nUPLOAD_DIR=./uploads\nEASYPAISA_NUMBER=03143404938\nEASYPAISA_ACCOUNT_NAME=\nSHOP_LATITUDE=24.911012\nSHOP_LONGITUDE=67.105612\nSMTP_HOST=\nSMTP_PORT=587\nSMTP_USER=\nSMTP_PASS=\nEMAIL_FROM=\nADMIN_NOTIFY_EMAIL=\n\n# ===== LIVE HOST (set these in Render's Environment tab, NOT in a file) =====\n# APP_ENV=production\n# STORAGE_BACKEND=db\n# DATABASE_URL=postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require   (from Neon)\n# SECRET_KEY=<long random, set once and never change>\n# ADMIN_EMAIL / ADMIN_PASSWORD / CORS_ORIGINS=https://eatcravy.netlify.app\n",
}

for path, text in NEW.items():
    if Path(path).exists():
        print("kept your existing", path, "(skipped)")
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8", newline="\n")
        print("created", path)
for path, text in staged.items():
    Path(path).write_bytes((text.replace("\n", "\r\n") if crlf[path] else text).encode())
    print("patched", path)
gi = Path(".gitignore")
have = gi.read_text() if gi.exists() else ""
add = [l for l in [".env", ".env.*", "!.env.example", "venv/", ".venv/", "*.db", "__pycache__/", "*.pyc", "uploads/*", ".vscode/"] if l not in have.split()]
if add:
    gi.write_text(have.rstrip("\n") + "\n" + "\n".join(add) + "\n")
    print("updated .gitignore")
print("DONE")