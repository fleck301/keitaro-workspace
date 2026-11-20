from copy import deepcopy
import json
from pathlib import Path

import httpx


class KeitaroError(Exception):
    def __init__(self, message, status=502):
        self.message, self.status = message, status


class Keitaro:
    def __init__(self, url, key, transport=None):
        if not url.startswith("https://") or not key:
            raise ValueError("Для live-режима задайте KEITARO_URL с HTTPS и KEITARO_API_KEY.")
        self.http = httpx.Client(
            base_url=url.rstrip("/") + "/admin_api/v1/",
            headers={"Api-Key": key, "Accept": "application/json", "User-Agent": "KeitaroWrapper/1.0"},
            timeout=httpx.Timeout(25, connect=10), transport=transport,
        )

    def request(self, method, path, data=None, params=None):
        try:
            response = self.http.request(method, path, json=data, params=params)
        except httpx.RequestError as exc:
            # Do not blindly retry POST: a timed-out request might already exist.
            raise KeitaroError("Keitaro недоступен или не ответил вовремя. Проверьте состояние перед повтором.") from exc
        if response.is_error:
            status = response.status_code
            message = {401: "Проверьте API-ключ.", 403: "Недостаточно прав Keitaro.",
                       404: "Объект не найден в Keitaro.", 429: "Лимит запросов Keitaro. Повторите позже."}.get(status, "Keitaro отклонил запрос. Проверьте параметры и журнал сервера.")
            raise KeitaroError(f"{message} HTTP {status}.")
        try:
            return response.json()
        except ValueError as exc:
            raise KeitaroError("Keitaro вернул ответ, который не является JSON.") from exc

    def campaigns(self):
        result, offset, size = [], 0, 100
        while True:
            page = self.request("GET", "campaigns", params={"offset": offset, "limit": size})
            result.extend(page)
            if len(page) < size:
                return result
            offset += size

    def close(self):
        self.http.close()


class DemoKeitaro:
    """Local persistent simulator of only the API endpoints this application uses."""
    def __init__(self, path=".demo-keitaro.json"):
        self.path = Path(path)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {
                "campaigns": [{"id": 1001, "name": "test_goncharenko_demo", "domain_id": 1, "group_id": 1, "traffic_source_id": 1}],
                "offers": [{"id": i, "name": n, "state": "active"} for i, n in enumerate(["Aurora · Portugal", "Nord · Germany", "Atlas · Spain", "Nova · France", "Luna · Italy"], 101)],
                "domains": [{"id": 1, "name": "demo.example", "state": "active"}],
                "groups": [{"id": 1, "name": "Тестовые кампании"}],
                "traffic_sources": [{"id": 1, "name": "Demo traffic", "state": "active"}],
                "streams": [
                    {"id": 10011, "campaign_id": 1001, "name": "Geo → Google", "schema": "redirect", "type": "regular", "position": 1, "action_type": "http", "action_payload": "https://www.google.com/", "offers": [], "filters": [{"name": "country", "mode": "accept", "payload": ["PT"]}]},
                    {"id": 10012, "campaign_id": 1001, "name": "Остальной трафик → офферы", "schema": "landings", "type": "default", "position": 2, "action_type": "http", "filters": [], "offers": [{"offer_id": 101, "share": 50, "state": "active"}, {"offer_id": 102, "share": 50, "state": "active"}]},
                ],
            }
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False))
        tmp.replace(self.path)

    def campaigns(self):
        return deepcopy(self.data["campaigns"])

    def request(self, method, path, data=None, params=None):
        parts = path.split("/")
        collection = parts[0]
        rows = self.data.get(collection, [])
        if method == "GET" and len(parts) == 1:
            return deepcopy(rows)
        if method == "GET" and len(parts) == 3 and parts[2] == "streams":
            return deepcopy([s for s in self.data["streams"] if s["campaign_id"] == int(parts[1])])
        if method == "GET" and len(parts) == 2:
            row = next((r for r in rows if r["id"] == int(parts[1])), None)
            if row is None:
                raise KeitaroError("Объект не найден.", 404)
            return deepcopy(row)
        if method == "POST" and len(parts) == 1 and collection in {"campaigns", "streams"}:
            row = {**deepcopy(data), "id": max([r["id"] for r in rows], default=0) + 1}
            rows.append(row)
            self.save()
            return deepcopy(row)
        if method == "PUT" and len(parts) == 2 and collection in {"streams", "campaigns"}:
            row = next((r for r in rows if r["id"] == int(parts[1])), None)
            if row is None:
                raise KeitaroError("Объект не найден.", 404)
            row.update(deepcopy(data))
            self.save()
            return deepcopy(row)
        raise KeitaroError("Этот метод не поддерживается симулятором.", 400)

    def close(self):
        pass
