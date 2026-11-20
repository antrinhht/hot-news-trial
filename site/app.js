"use strict";

(() => {
  const PAGE_SIZE = 18;
  const CHECK_INTERVAL = 60_000;
  const OLD_EDITION = 30 * CHECK_INTERVAL;
  const WITHHELD_LABEL = "Giữ nguyên văn · bản dịch chưa đạt kiểm tra";
  const $ = id => document.getElementById(id);
  const regions = {CN: "Trung Quốc", US: "Mỹ", intl: "Quốc tế"};
  const statuses = {
    changed: "Có cập nhật", unchanged: "Đã kiểm tra", empty: "Nguồn rỗng",
    failed: "Lỗi lấy tin", not_due: "Chưa đến lượt"
  };
  const emptyViews = {
    latest: "Chưa có sự kiện trong 48 giờ gần nhất.",
    hot: "Chưa có sự kiện đủ hai bên độc lập, trong đó có một nguồn biên tập.",
    selected: "Chưa có sự kiện đạt tiêu chí tuyển chọn."
  };
  const dateFormat = new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit",
    year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false
  });
  const clockFormat = new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", hour12: false
  });
  let feed = null;
  let sourceMap = new Map();
  let eventMap = new Map();
  let inFlight = false;
  let lastAttempt = 0;
  let view = "latest";
  let market = "all";
  let limit = PAGE_SIZE;

  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = text;
    if (className) el.className = className;
    return el;
  }

  function stamp(value) {
    const ms = value ? Date.parse(value) : NaN;
    return Number.isFinite(ms) ? dateFormat.format(ms) + " UTC+7" : "Chưa rõ giờ";
  }

  function observed(kind) {
    return /first.seen|observ|detect|discov/i.test(kind || "");
  }

  function when(kind, value) {
    return (observed(kind) ? "Phát hiện lần đầu" : "Giờ từ nguồn") + " · " + stamp(value);
  }

  function region(code) {
    if (code === "CN" || code === "US") return code;
    return ["DE", "GB", "QA"].includes(code) ? "intl" : null;
  }

  function sourceName(source, fallback) {
    if (source?.source_id === "bbc-world") return "BBC News";
    if (source?.source_id === "caixin-global") return "Caixin Global qua Google News";
    return source?.name || fallback || "Nguồn gốc";
  }

  function link(text, value, className) {
    let url;
    try { url = new URL(value); } catch { return node("span", text, className); }
    if (!["http:", "https:"].includes(url.protocol)) return node("span", text, className);
    const a = node("a", text, className);
    a.href = url.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }

  function listOf(value) {
    return Array.isArray(value) ? value : [];
  }

  function englishTitle(story) {
    const translated = story?.title_en?.trim();
    return translated && translated !== story.title?.trim() ? translated : "";
  }

  function card(event, opened) {
    const article = node("article", null, "article");
    const tags = [...new Set(listOf(event.markets).map(region).filter(Boolean))];
    const info = node("div", null, "info");
    if (tags.length) info.append(node("span", tags.map(t => regions[t]).join(" / "), "ev-market"));
    const time = node("time", when(event.time_kind, event.latest_at), "ev-time");
    if (event.latest_at) time.dateTime = event.latest_at;
    info.append(time);
    const items = listOf(event.items);
    const primary = items.find(item => item.title === event.title) || items[0];
    const title = node("h2");
    const translated = englishTitle(event);
    const withheld = !translated && event.translation_status === "withheld";
    const titleLink = link(translated || event.title, primary?.url, "headline");
    titleLink.lang = translated ? "en" : sourceMap.get(primary?.source_id)?.language || "";
    title.append(titleLink);
    article.append(info, title);
    const credit = node("div", null, "credit");
    if (primary) credit.append(link(sourceName(sourceMap.get(primary.source_id), primary.source_id), primary.url));
    const ownerCount = new Set(listOf(event.owners)).size;
    credit.append(node("span", ownerCount + " bên độc lập"));
    if (translated) credit.append(node("span", "Dịch máy Trung → Anh"));
    if (withheld) credit.append(node("span", WITHHELD_LABEL));
    article.append(credit);
    if (view === "selected" && event.selected_reason) {
      article.append(node("p", event.selected_reason, "reason"));
    }
    if (!items.length) return article;
    const details = node("details");
    details.dataset.eventId = event.event_id;
    details.open = opened.has(event.event_id);
    details.append(node("summary", "Đối chiếu " + items.length + " bài gốc"));
    const ul = node("ul", null, "source-links");
    for (const item of items) {
      const li = node("li");
      const source = sourceMap.get(item.source_id);
      const itemTime = observed(item.time_kind) ? item.first_seen_at : (item.source_time || item.first_seen_at);
      const translation = englishTitle(item);
      const attribution = sourceName(source, item.source_id) + " · " + when(item.time_kind, itemTime);
      let note = translation ? " · Dịch máy Trung → Anh" : "";
      if (!translation && item.translation_status === "withheld" && !(withheld && item.title === event.title)) {
        note = " · " + WITHHELD_LABEL;
      }
      li.append(node("span", attribution + note));
      const itemLink = link(translation || item.title || event.title, item.url);
      itemLink.lang = translation ? "en" : source?.language || "";
      li.append(itemLink);
      if (translation) {
        const original = node("p", "Bản gốc · ", "original-title");
        const text = node("span", item.title);
        text.lang = "zh";
        original.append(text);
        li.append(original);
      }
      ul.append(li);
    }
    details.append(ul);
    article.append(details);
    return article;
  }

  function renderEvents() {
    if (!feed) return;
    const articles = $("articles");
    const open = new Set(Array.from(articles.querySelectorAll("details[open]"), d => d.dataset.eventId));
    const previousFocus = document.activeElement;
    const focusId = previousFocus?.closest("details")?.dataset.eventId;
    const focusUrl = previousFocus?.tagName === "A" ? previousFocus.href : null;
    const all = feed[view].map(id => eventMap.get(id)).filter(Boolean);
    const visible = all.filter(e => market === "all" || listOf(e.markets).some(m => region(m) === market));
    const nodes = visible.slice(0, limit).map(e => card(e, open));
    if (!nodes.length) {
      const message = all.length && market !== "all"
        ? "Chưa có sự kiện phù hợp với thị trường " + regions[market] + "."
        : emptyViews[view];
      nodes.push(node("p", message, "empty"));
    }
    articles.replaceChildren(...nodes);
    $("event-count").textContent = visible.length + " sự kiện";
    $("more").hidden = visible.length <= limit;
    if (previousFocus && !previousFocus.isConnected && (focusId || focusUrl)) {
      const matchingDetails = [...articles.querySelectorAll("details")].find(d => d.dataset.eventId === focusId);
      const target = (focusUrl && [...(matchingDetails || articles).querySelectorAll("a")].find(a => a.href === focusUrl))
        || matchingDetails?.querySelector("summary");
      target?.focus({preventScroll: true});
    }
  }

  function renderSources() {
    const groups = [];
    for (const [code, label] of Object.entries(regions)) {
      const members = feed.sources.filter(source => region(source.market) === code);
      if (!members.length) continue;
      const section = node("section", null, "source-group");
      section.append(node("h3", label + " · " + members.length));
      const ul = node("ul");
      for (const source of members) {
        const li = node("li", null, "source-line");
        const label = node("span", sourceName(source), "source-name");
        if (source.retry_at) label.append(node("small", "Thử lại từ " + stamp(source.retry_at), "source-retry"));
        li.append(label);
        const state = node("span", source.issue === "rate_limited" ? "Giới hạn lượt gọi" : statuses[source.status] || "Chưa rõ", "source-state");
        state.dataset.state = source.status;
        li.append(state);
        ul.append(li);
      }
      section.append(ul);
      groups.push(section);
    }
    $("sources").replaceChildren(...groups);
    $("source-count").textContent = feed.sources.length;
  }

  function notice(text) {
    const el = $("notice");
    el.className = text ? "notice" : "";
    if (el.textContent !== text) el.textContent = text;
  }

  async function readJson(path, parameter, value) {
    const url = new URL(path, document.baseURI);
    url.searchParams.set(parameter, value);
    const response = await fetch(url, {cache: "no-store", signal: globalThis.AbortSignal?.timeout?.(20_000)});
    if (!response.ok) throw new Error("HTTP " + response.status);
    return response.json();
  }

  function needsNewPage(release) {
    const version = document.documentElement.dataset.uiHash;
    if (!version || !release.ui_hash || version === release.ui_hash) return false;
    const url = new URL(window.location.href);
    if (url.searchParams.get("ui") === release.ui_hash) throw new Error("Giao diện chưa đồng bộ");
    url.searchParams.set("ui", release.ui_hash);
    window.location.replace(url.href);
    return true;
  }

  function reportFreshness(manual, changed) {
    const now = Date.now();
    $("last-checked").textContent = "Đã kiểm tra lúc " + clockFormat.format(now) + " · tự kiểm tra mỗi phút";
    const age = now - Date.parse(feed.published_at);
    if (age >= OLD_EDITION) {
      const minutes = Math.floor(age / CHECK_INTERVAL);
      notice("Đã " + minutes + " phút kể từ bản xuất gần nhất. Trang sẽ tự hiển thị khi có bản mới.");
    } else if (manual) {
      notice(changed ? "Đã tải bản xuất mới." : "Bạn đang xem bản xuất mới nhất.");
    } else {
      notice("");
    }
  }

  async function refresh(manual = false) {
    if (inFlight) return;
    inFlight = true;
    lastAttempt = Date.now();
    $("refresh").disabled = true;
    try {
      const check = manual ? lastAttempt : Math.floor(lastAttempt / CHECK_INTERVAL);
      const release = await readJson("./manifest.json", "check", check);
      if (release.schema_version !== 1 || !release.publish_hash || !release.release_id) throw new Error("manifest");
      if (needsNewPage(release)) return;
      const changed = release.publish_hash !== feed?.publish_hash || release.release_id !== feed?.release_id;
      if (changed) {
        const next = await readJson("./feed.json", "release", release.release_id);
        const validLists = ["sources", "events", "latest", "hot", "selected"].every(k => Array.isArray(next[k]));
        const published = Date.parse(next.published_at);
        if (next.schema_version !== 1 || next.publish_hash !== release.publish_hash || next.release_id !== release.release_id ||
            !validLists || !Number.isFinite(published) || (feed && published < Date.parse(feed.published_at))) {
          throw new Error("feed");
        }
        feed = next;
        sourceMap = new Map(feed.sources.map(s => [s.source_id, s]));
        eventMap = new Map(feed.events.map(e => [e.event_id, e]));
        $("published").textContent = stamp(feed.published_at);
        if (feed.published_at) $("published").dateTime = feed.published_at;
        renderEvents();
        renderSources();
      }
      reportFreshness(manual, changed);
    } catch {
      if (feed) notice("Chưa tải được bản xuất mới. Bạn đang xem bản đã tải gần nhất.");
      else {
        $("published").textContent = "Chưa có dữ liệu";
        $("articles").replaceChildren(node("p", "Chưa tải được bảng tin. Dùng nút “Kiểm tra bản mới” để thử lại.", "empty"));
      }
    } finally {
      $("refresh").disabled = false;
      inFlight = false;
    }
  }

  function onChoice(containerId, key, select) {
    const group = $(containerId);
    group.addEventListener("click", event => {
      const choice = event.target.closest("button");
      if (!choice || !group.contains(choice)) return;
      for (const button of group.querySelectorAll("button")) {
        button.setAttribute("aria-pressed", String(button === choice));
      }
      select(choice.dataset[key]);
      limit = PAGE_SIZE;
      renderEvents();
    });
  }
  onChoice("views", "view", value => { view = value; });
  onChoice("markets", "market", value => { market = value; });
  $("more").addEventListener("click", () => { limit += PAGE_SIZE; renderEvents(); });
  $("refresh").addEventListener("click", () => refresh(true));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Date.now() - lastAttempt >= CHECK_INTERVAL) refresh();
  });
  setInterval(() => { if (!document.hidden && Date.now() - lastAttempt >= CHECK_INTERVAL) refresh(); }, CHECK_INTERVAL);
  refresh();
})();
