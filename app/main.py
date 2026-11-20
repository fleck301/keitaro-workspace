from contextlib import asynccontextmanager
from copy import deepcopy
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
import pycountry
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.keitaro import DemoKeitaro, Keitaro, KeitaroError
from app.models import Stream, database
from app.weights import rebalance, signature

load_dotenv()
STATIC = Path(__file__).parent / "static"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCampaign(Input):
    name: str = Field(min_length=1, max_length=150)
    country: str
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

    @field_validator("country")
    @classmethod
    def country_code(cls, value):
        value = value.strip().upper()
        if not pycountry.countries.get(alpha_2=value):
            raise ValueError("Укажите двухбуквенный ISO-код страны.")
        return value


class Revision(Input):
    version: int = Field(ge=1)


class DraftChange(Revision):
    action: str = Field(pattern="^(add|remove|restore|pin|unpin)$")
    offer_id: int = Field(gt=0)
    share: int | None = Field(default=None, ge=0, le=100, strict=True)


def view(row):
    return {"id": row.id, "campaign_id": row.campaign_id, "name": row.name,
            "version": row.version, "editable": row.remote.get("schema") == "landings" and bool(row.published or row.draft),
            "dirty": row.draft != row.published, "offers": row.draft,
            "schema": row.remote.get("schema"), "type": row.remote.get("type"),
            "position": row.remote.get("position", 0)}


def create_app(db_url=None, client=None, demo=None):
    demo = os.getenv("DEMO_MODE", "false").lower() == "true" if demo is None else demo
    engine, sessions = database(db_url or os.getenv("DATABASE_URL", "sqlite:///./keitaro.db"))
    tracker = client or (DemoKeitaro() if demo else Keitaro(os.getenv("KEITARO_URL", ""), os.getenv("KEITARO_API_KEY", "")))
    # Single-process tool. Serialize read/modify/write, including remote requests.
    # Version numbers also reject stale browser tabs. See README deployment limits.
    lock = Lock()
    prefix = os.getenv("CAMPAIGN_PREFIX", "test_goncharenko_")

    @asynccontextmanager
    async def lifespan(app):
        yield
        tracker.close()
        engine.dispose()

    app = FastAPI(title="Keitaro Workspace", version="1.0.0", lifespan=lifespan)
    app.state.engine, app.state.tracker = engine, tracker

    def session():
        with lock, sessions() as db:
            yield db

    @app.exception_handler(KeitaroError)
    async def remote_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(StaleDataError)
    async def stale_error(request, exc):
        return JSONResponse(status_code=409, content={"detail": "Данные изменились в другой вкладке. Обновите редактор."})

    @app.middleware("http")
    async def local_write_guard(request: Request, call_next):
        # No cross-origin writes, even if this local tool is opened from another site.
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin writes are disabled."})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    def find(db, stream_id, version):
        row = db.get(Stream, stream_id)
        if row is None:
            raise HTTPException(404, "Сначала загрузите потоки кампании.")
        if row.version != version:
            raise HTTPException(409, "Данные изменились. Обновите редактор.")
        if row.remote.get("schema") != "landings" or not (row.published or row.draft):
            raise HTTPException(422, "У этого потока нет редактируемой схемы офферов.")
        return row

    def reconcile(db, campaign_id, remote_streams):
        names = {int(o["id"]): o["name"] for o in tracker.request("GET", "offers")}
        existing = {s.id: s for s in db.scalars(select(Stream).where(Stream.campaign_id == campaign_id))}
        seen = set()
        for remote in remote_streams:
            stream_id = int(remote["id"])
            seen.add(stream_id)
            row = existing.get(stream_id)
            old = {r["offer_id"]: r for r in row.published} if row else {}
            bindings = []
            for offer in remote.get("offers", []):
                offer_id = int(offer["offer_id"])
                previous = old.pop(offer_id, {})
                bindings.append({"offer_id": offer_id, "name": names.get(offer_id, previous.get("name", f"Offer #{offer_id}")),
                                 "share": int(offer["share"]), "pinned": previous.get("pinned", False),
                                 "archived": offer.get("state", "active") != "active", "remote": deepcopy(offer)})
            for previous in old.values():
                bindings.append({**previous, "share": 0, "pinned": False, "archived": True})
            if row is None:
                row = Stream(id=stream_id, campaign_id=campaign_id)
                db.add(row)
            row.name, row.remote = remote["name"], deepcopy(remote)
            row.published, row.draft = deepcopy(bindings), deepcopy(bindings)
        for stream_id, row in existing.items():
            if stream_id not in seen:
                db.delete(row)  # Stream removed remotely; offer archive belongs to that stream.
        db.commit()
        return [view(s) for s in db.scalars(select(Stream).where(Stream.campaign_id == campaign_id))]

    @app.get("/api/config")
    def config():
        return {"demo": demo, "prefix": prefix}

    @app.get("/api/catalog")
    def catalog(db=Depends(session)):
        return {"domains": tracker.request("GET", "domains"),
                "groups": tracker.request("GET", "groups", params={"type": "campaigns"}),
                "traffic_sources": tracker.request("GET", "traffic_sources"),
                "offers": tracker.request("GET", "offers"),
                "countries": [{"id": c.alpha_2, "name": c.name} for c in sorted(pycountry.countries, key=lambda c: c.name)]}

    @app.get("/api/campaigns")
    def campaigns(db=Depends(session)):
        return tracker.campaigns()

    @app.post("/api/campaigns", status_code=201)
    def create(body: CreateCampaign, db=Depends(session)):
        name = body.name.strip()
        if not name.startswith(prefix):
            raise HTTPException(422, f"Имя должно начинаться с {prefix}.")
        # Validate all foreign keys before creating anything remotely.
        for path, value, params in [("offers", body.offer_id, None), ("domains", body.domain_id, None),
                                    ("groups", body.group_id, {"type": "campaigns"}), ("traffic_sources", body.traffic_source_id, None)]:
            if not any(int(item["id"]) == value and item.get("state") not in {"deleted", "disabled"} for item in tracker.request("GET", path, params=params)):
                raise HTTPException(422, f"Недоступный объект {path}: {value}.")
        campaign = tracker.request("POST", "campaigns", {"name": name, "alias": "test-" + uuid4().hex[:12], "type": "position",
                    "state": "disabled", "domain_id": body.domain_id, "group_id": str(body.group_id), "traffic_source_id": body.traffic_source_id})
        campaign_id = int(campaign["id"])
        try:
            common = {"campaign_id": campaign_id, "state": "active", "action_type": "http", "collect_clicks": True}
            tracker.request("POST", "streams", {**common, "name": f"Geo {body.country} → Google", "type": "regular", "position": 1,
                "schema": "redirect", "action_payload": "https://www.google.com/", "filters": [{"name": "country", "mode": "accept", "payload": [body.country]}]})
            tracker.request("POST", "streams", {**common, "name": "Остальной трафик → оффер", "type": "default", "position": 2,
                "schema": "landings", "offers": [{"offer_id": body.offer_id, "share": 100, "state": "active"}], "filters": [], "landings": []})
            campaign = tracker.request("PUT", f"campaigns/{campaign_id}", {"state": "active"})
            streams = tracker.request("GET", f"campaigns/{campaign_id}/streams")
            local_streams = reconcile(db, campaign_id, streams)
        except KeitaroError as exc:
            raise KeitaroError(f"Кампания #{campaign_id} создана. Настройка не завершена: {exc.message} Проверьте её в Keitaro; не создавайте повтор вслепую.") from exc
        return {"campaign": campaign, "streams": local_streams}

    @app.get("/api/campaigns/{campaign_id}/streams")
    def streams(campaign_id: int, db=Depends(session)):
        return [view(s) for s in db.scalars(select(Stream).where(Stream.campaign_id == campaign_id))]

    @app.post("/api/campaigns/{campaign_id}/fetch")
    def fetch(campaign_id: int, discard: bool = False, db=Depends(session)):
        rows = list(db.scalars(select(Stream).where(Stream.campaign_id == campaign_id)))
        if not discard and any(s.draft != s.published for s in rows):
            raise HTTPException(409, "Есть локальные изменения. Сначала отправьте или отмените их.")
        remote = tracker.request("GET", f"campaigns/{campaign_id}/streams")
        return reconcile(db, campaign_id, remote)

    @app.post("/api/streams/{stream_id}/draft")
    def change(stream_id: int, body: DraftChange, db=Depends(session)):
        row = find(db, stream_id, body.version)
        draft = deepcopy(row.draft)
        offer = next((o for o in draft if o["offer_id"] == body.offer_id), None)
        if body.action == "add":
            if offer:
                raise HTTPException(422, "Оффер уже есть. Используйте «Вернуть» для архивного.")
            source = next((o for o in tracker.request("GET", "offers") if int(o["id"]) == body.offer_id and o.get("state", "active") == "active"), None)
            if source is None:
                raise HTTPException(422, "Оффер недоступен в Keitaro.")
            draft.append({"offer_id": body.offer_id, "name": source["name"], "share": 0, "pinned": False, "archived": False, "remote": {}})
        else:
            if offer is None:
                raise HTTPException(404, "Оффер не найден в этом потоке.")
            if body.action == "restore":
                offer["archived"], offer["pinned"] = False, False
            elif body.action == "remove":
                offer["archived"], offer["pinned"] = True, False
            elif offer["archived"]:
                raise HTTPException(422, "Сначала верните оффер из архива.")
            elif body.action == "pin":
                if body.share is None:
                    raise HTTPException(422, "Укажите закрепляемый вес.")
                offer["pinned"], offer["share"] = True, body.share
            elif body.action == "unpin":
                offer["pinned"] = False
        try:
            row.draft = rebalance(draft)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.commit()
        return view(row)

    @app.post("/api/streams/{stream_id}/cancel")
    def cancel(stream_id: int, body: Revision, db=Depends(session)):
        row = find(db, stream_id, body.version)
        row.draft = deepcopy(row.published)
        db.commit()
        return view(row)

    @app.post("/api/streams/{stream_id}/push")
    def push(stream_id: int, body: Revision, db=Depends(session)):
        row = find(db, stream_id, body.version)
        active = [o for o in row.draft if not o["archived"]]
        if not active or sum(o["share"] for o in active) != 100:
            raise HTTPException(422, "Нужен хотя бы один оффер и сумма весов 100%.")
        current = tracker.request("GET", f"streams/{stream_id}")
        if current.get("schema") != row.remote.get("schema") or signature(current.get("offers", [])) != signature(row.remote.get("offers", [])):
            raise HTTPException(409, "Поток изменили в Keitaro. Отмените черновик, загрузите потоки и повторите изменения.")
        # Partial PUT: keep redirect, filters, landings, triggers and all other settings intact.
        payload = [{**{k: v for k, v in o.get("remote", {}).items() if k in {"id", "offer_id"}},
                    "offer_id": o["offer_id"], "share": o["share"], "state": "active"} for o in active]
        remote = tracker.request("PUT", f"streams/{stream_id}", {"offers": payload})
        if signature(remote.get("offers", [])) != signature(payload):
            raise KeitaroError("Keitaro ответил, но состав офферов отличается. Загрузите потоки и проверьте результат.")
        draft = deepcopy(row.draft)
        remote_bindings = {int(o["offer_id"]): o for o in remote.get("offers", [])}
        for offer in draft:
            offer["remote"] = remote_bindings.get(offer["offer_id"], {})
        row.remote, row.draft, row.published = remote, draft, deepcopy(draft)
        db.commit()
        return view(row)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app
