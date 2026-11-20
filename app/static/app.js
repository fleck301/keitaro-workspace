const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let catalog,
  campaigns = [],
  streams = [],
  campaignId = null,
  config,
  busy = false;
async function api(path, body) {
  const response = await fetch(
    "/api" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("Сервер вернул непонятный ответ. Проверьте подключение.");
  }
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Проверьте заполнение полей.",
    );
  return data;
}
function notice(message, error = false) {
  $("#notice").textContent = message;
  $("#notice").className = error ? "error" : "";
  $("#notice").hidden = false;
}
async function task(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    await fn();
  } catch (e) {
    notice(e.message, true);
  } finally {
    busy = false;
    render();
    $("#open-create").disabled = !catalog;
    $("#fetch").disabled = !campaignId;
    $("#create-form button[type=submit]").disabled = false;
    $("#close-create").disabled = false;
  }
}
function options(rows) {
  return rows
    .filter((r) => !["deleted", "disabled"].includes(r.state))
    .map(
      (r) =>
        `<option value="${esc(r.id)}">${esc(r.name)} · #${esc(r.id)}</option>`,
    )
    .join("");
}
function campaignOptions() {
  const query = $("#campaign-search").value.trim().toLowerCase();
  const filtered = campaigns.filter(
    (c) =>
      `${c.name} ${c.id}`.toLowerCase().includes(query) || c.id === campaignId,
  );
  $("#campaign").innerHTML =
    '<option value="">Выберите кампанию</option>' + options(filtered);
  $("#campaign").value = campaignId || "";
}
async function selectCampaign(id) {
  campaignId = Number(id) || null;
  streams = campaignId ? await api(`/campaigns/${campaignId}/streams`) : [];
  if (campaignId && !streams.length)
    streams = await api(`/campaigns/${campaignId}/fetch`, {});
  campaignOptions();
}
function render() {
  $("#campaign-meta").textContent = campaignId
    ? `${streams.length} потоков · кампания #${campaignId}`
    : "Выберите кампанию, чтобы загрузить потоки.";
  if (!streams.length) {
    $("#streams").innerHTML =
      '<div class="empty">' +
      (campaignId
        ? "Потоков пока нет. Нажмите Fetch streams."
        : "Выберите кампанию или создайте новую.") +
      "</div>";
    return;
  }
  $("#streams").innerHTML = [...streams]
    .sort((a, b) => a.position - b.position)
    .map((s) => {
      const active = s.offers.filter((o) => !o.archived),
        total = active.reduce((n, o) => n + o.share, 0);
      const available = catalog.offers.filter(
        (o) => !s.offers.some((x) => x.offer_id === o.id),
      );
      return `<article class="stream ${s.dirty ? "dirty" : ""}" data-stream="${s.id}"><div class="stream-head"><div><h2 class="stream-title">${esc(s.name)}</h2><div class="stream-subtitle">Поток #${s.id} · ${esc(s.type)} · ${esc(s.schema)}</div></div><div class="stream-status">${s.dirty ? '<span class="draft">Есть изменения</span>' : '<span class="synced">Синхронизирован</span>'}${s.editable ? `<div class="total ${total !== 100 ? "invalid" : ""}">${total}%<span>сумма весов</span></div>` : ""}</div></div>
    ${
      !s.editable
        ? '<div class="readonly">Поток без редактора офферов. Настройки редиректа и фильтров сохраняются в Keitaro.</div>'
        : `
    <div class="table-wrap"><table><thead><tr><th>ОФФЕР</th><th>ВЕС / ФИКСАЦИЯ</th><th>ДЕЙСТВИЕ</th></tr></thead><tbody>${s.offers.map((o) => `<tr class="${o.archived ? "archived" : ""}" data-offer="${o.offer_id}"><td class="offer-name"><div class="offer-id">#${o.offer_id}</div>${esc(o.name)}</td><td class="share-cell">${o.archived ? '<span class="archive-label">0% · в архиве</span>' : `<input class="share-input" type="number" min="0" max="100" step="1" value="${o.share}" aria-label="Вес оффера ${o.offer_id}"> % <button class="small pin-button ${o.pinned ? "pinned" : ""}" data-action="${o.pinned ? "unpin" : "pin"}">${o.pinned ? "Закреплён" : "Закрепить"}</button>`}</td><td><button class="link ${o.archived ? "" : "danger"}" data-action="${o.archived ? "restore" : "remove"}">${o.archived ? "Вернуть" : "Убрать"}</button></td></tr>`).join("") || '<tr><td colspan="3" class="muted">Офферов пока нет. Добавьте первый.</td></tr>'}</tbody></table></div>
    <div class="stream-foot"><div class="offer-picker"><input type="search" class="offer-search" placeholder="Поиск оффера / ID" aria-label="Поиск оффера в потоке ${s.id}"><select class="offer-select" aria-label="Добавить оффер в поток ${s.id}">${options(available)}</select><button class="small" data-action="add" ${available.length ? "" : "disabled"}>＋</button></div><div class="actions"><button data-action="cancel" ${s.dirty ? "" : "disabled"}>Отменить</button><button data-action="push" class="primary" ${s.dirty && active.length && total === 100 ? "" : "disabled"}>Push to Keitaro ↑</button></div></div>`
    }</article>`;
    })
    .join("");
}
$("#campaign-search").addEventListener("input", campaignOptions);
$("#campaign").addEventListener("change", (e) =>
  task(() => selectCampaign(e.target.value)),
);
$("#fetch").addEventListener("click", () =>
  task(async () => {
    streams = await api(`/campaigns/${campaignId}/fetch`, {});
    notice("Потоки загружены. Внешние изменения учтены.");
  }),
);
$("#streams").addEventListener("input", (e) => {
  if (!e.target.matches(".offer-search")) return;
  const article = e.target.closest("[data-stream]"),
    stream = streams.find((s) => s.id === Number(article.dataset.stream));
  const query = e.target.value.toLowerCase();
  const rows = catalog.offers.filter(
    (o) =>
      !stream.offers.some((x) => x.offer_id === o.id) &&
      `${o.name} ${o.id}`.toLowerCase().includes(query),
  );
  article.querySelector(".offer-select").innerHTML = options(rows);
  article.querySelector("[data-action=add]").disabled = !rows.length;
});
$("#streams").addEventListener("click", (e) => {
  const button = e.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const article = button.closest("[data-stream]"),
    s = streams.find((s) => s.id === Number(article.dataset.stream)),
    action = button.dataset.action;
  const tr = button.closest("[data-offer]"),
    offerId = tr
      ? Number(tr.dataset.offer)
      : Number(article.querySelector(".offer-select")?.value);
  let share;
  if (action === "pin") {
    const input = tr.querySelector(".share-input");
    if (!input.reportValidity()) return;
    share = Number(input.value);
  }
  task(async () => {
    const data = ["push", "cancel"].includes(action)
      ? { version: s.version }
      : {
          action,
          offer_id: offerId,
          version: s.version,
          ...(share === undefined ? {} : { share }),
        };
    const updated = await api(
      `/streams/${s.id}/${["push", "cancel"].includes(action) ? action : "draft"}`,
      data,
    );
    streams = streams.map((row) => (row.id === s.id ? updated : row));
    notice(
      action === "push"
        ? "Изменения отправлены в Keitaro."
        : action === "cancel"
          ? "Черновик отменён."
          : "Черновик сохранён локально. Keitaro пока не изменён.",
    );
  });
});
$("#open-create").addEventListener("click", () => {
  $("#create-error").textContent = "";
  $("#create-dialog").showModal();
});
$("#close-create").addEventListener("click", () => $("#create-dialog").close());
$("#create-form").addEventListener("submit", (e) => {
  e.preventDefault();
  task(async () => {
    const body = Object.fromEntries(new FormData(e.target));
    ["offer_id", "domain_id", "group_id", "traffic_source_id"].forEach(
      (k) => (body[k] = Number(body[k])),
    );
    try {
      const created = await api("/campaigns", body);
      campaigns = await api("/campaigns");
      campaignId = created.campaign.id;
      streams = created.streams;
      $("#campaign-search").value = "";
      campaignOptions();
      $("#create-dialog").close();
      notice(
        `Кампания #${campaignId} создана: геофильтр и резервный поток настроены.`,
      );
    } catch (e) {
      $("#create-error").textContent = e.message;
    }
  });
});
async function boot() {
  await task(async () => {
    config = await api("/config");
    $("#mode").textContent = config.demo
      ? "DEMO · ЛОКАЛЬНЫЙ СИМУЛЯТОР"
      : "LIVE · KEITARO";
    $("#mode").className = "badge " + (config.demo ? "demo" : "live");
    catalog = await api("/catalog");
    campaigns = await api("/campaigns");
    $("#name").value = config.prefix;
    for (const [id, key] of Object.entries({
      country: "countries",
      offer_id: "offers",
      domain_id: "domains",
      group_id: "groups",
      traffic_source_id: "traffic_sources",
    }))
      $("#" + id).innerHTML = options(catalog[key]);
    $("#country").value = "PT";
    campaignOptions();
    if (config.demo && campaigns.length) await selectCampaign(campaigns[0].id);
  });
}
boot();
