"use strict";

const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const site = path.join(__dirname, "../site");
const script = fs.readFileSync(path.join(site, "app.js"), "utf8");
const html = fs.readFileSync(path.join(site, "index.html"), "utf8");
const page = "https://example.org/hot-news-trial/";
const date = "2026-11-20T10:00:00Z";

class Element {
  constructor(tag) {
    this.tag = tag;
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.textContent = "";
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  querySelectorAll(tag) {
    const descendants = this.children.flatMap(node => [node, ...node.querySelectorAll("*")]);
    return descendants.filter(node => tag === "*" || node.tag === tag || (tag === "details[open]" && node.open));
  }
}

function edition(id, published = date) {
  const item = {source_id: "weibo", title: "中文标题", title_en: "A translated headline", url: "https://weibo.com/1"};
  return {
    schema_version: 1, publish_hash: "hash-" + id, release_id: id, published_at: published,
    sources: [{source_id: "weibo", language: "zh", name: "Weibo", market: "CN", status: "unchanged"}],
    events: [{event_id: "one", title: item.title, title_en: item.title_en, markets: ["CN"], owners: ["weibo"], items: [item]}],
    latest: ["one"], hot: [], selected: []
  };
}

async function start() {
  const nodes = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], new Element()]));
  const document = Object.assign(new Element(), {
    baseURI: page, documentElement: {dataset: {uiHash: "layout1"}}, hidden: false,
    createElement: tag => new Element(tag), getElementById: id => nodes[id]
  });
  const state = {now: Date.parse(date), feed: edition("1"), urls: [], nodes, document, redirects: []};
  const fetch = async (url, options) => {
    state.urls.push(url);
    assert.equal(options.cache, "no-store");
    if (state.offline) throw new Error("offline");
    const {schema_version, release_id, publish_hash} = state.feed;
    const manifest = {schema_version, release_id, publish_hash, ui_hash: "layout1", ...state.override};
    const value = url.pathname.endsWith("/manifest.json") ? manifest : state.feed;
    return {ok: true, json: async () => structuredClone(value)};
  };
  class TestDate extends Date { static now() { return state.now; } }
  vm.runInNewContext(script, {
    document, fetch, URL, AbortSignal, Date: TestDate, Intl,
    window: {location: {href: page, replace: url => state.redirects.push(url)}},
    setInterval: (callback, ms) => { state.tick = callback; assert.equal(ms, 60_000); }
  });
  await new Promise(resolve => setImmediate(resolve));
  return state;
}

test("shows English with machine translation credits and preserves the Chinese source text", async () => {
  const {nodes, urls} = await start();
  assert.deepEqual(urls.map(url => url.pathname), ["/hot-news-trial/manifest.json", "/hot-news-trial/feed.json"]);
  const card = nodes.articles.children[0];
  const title = card.querySelectorAll("h2")[0].children[0];
  assert.equal(title.lang, "en");
  assert.equal(title.textContent, "A translated headline");
  assert.equal(title.href, "https://weibo.com/1");
  const matches = card.querySelectorAll("span").filter(n => n.textContent.includes("中文标题"));
  assert.equal(matches.length, 1);
  assert.equal(matches[0].lang, "zh");
  const source = card.querySelectorAll("li")[0];
  assert.equal(source.querySelectorAll("a")[0].lang, "en");
  assert.match(source.children[0].textContent, /Dịch máy/);
});

test("unchanged checks retain cards, only fetch the manifest, and distinguish old publications", async () => {
  const s = await start();
  const card = s.nodes.articles.children[0];
  card.querySelectorAll("details")[0].open = true;
  await s.nodes.refresh.listeners.click();
  assert.match(s.nodes.notice.textContent, /mới nhất/);
  s.now += 39 * 60_000;
  s.document.hidden = true;
  s.tick();
  assert.equal(s.urls.length, 3);
  s.document.hidden = false;
  s.document.listeners.visibilitychange();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.urls.length, 4);
  assert.equal(s.urls[3].pathname, "/hot-news-trial/manifest.json");
  assert.match(s.nodes.notice.textContent, /39 phút/);
  assert.match(s.nodes["last-checked"].textContent, /17:39/);
  assert.equal(s.nodes.published.dateTime, date);
  assert.equal(s.nodes.articles.children[0], card);
  assert.equal(card.querySelectorAll("details")[0].open, true);
});

test("explains a withheld translation once per affected headline and keeps the source link", async () => {
  const s = await start();
  s.feed = edition("2");
  const entry = s.feed.events[0];
  const primary = entry.items[0];
  delete entry.title_en;
  delete primary.title_en;
  entry.translation_status = primary.translation_status = "withheld";
  entry.items.push({...primary, title: "第二篇中文标题", url: "https://weibo.com/2"});
  entry.items.push({source_id: "weibo", title: "第三篇中文标题", url: "https://weibo.com/3"});
  await s.nodes.refresh.listeners.click();
  const card = s.nodes.articles.children[0];
  const members = card.querySelectorAll("li");
  const notice = /bản dịch chưa đạt kiểm tra/;
  assert.equal(card.querySelectorAll("span").filter(node => notice.test(node.textContent)).length, 2);
  assert.doesNotMatch(members[0].children[0].textContent, notice);
  assert.match(members[1].children[0].textContent, notice);
  assert.doesNotMatch(members[2].children[0].textContent, notice);
  assert.equal(members[1].querySelectorAll("a")[0].href, "https://weibo.com/2");
  assert.equal(members[1].querySelectorAll("a")[0].lang, "zh");
});

test("updates only a matching newer edition, preserves expanded events and handles failures", async () => {
  const s = await start();
  const card = s.nodes.articles.children[0];
  card.querySelectorAll("details")[0].open = true;
  s.override = {publish_hash: "different-hash"};
  await s.nodes.refresh.listeners.click();
  assert.equal(s.nodes.articles.children[0], card);
  assert.match(s.nodes.notice.textContent, /Chưa tải được/);
  s.override = {};
  s.feed = edition("2", "2026-11-20T10:09:00Z");
  s.now += 600_000;
  const entry = s.feed.events[0];
  delete entry.title_en;
  entry.items[0].title_en = entry.title;
  await s.nodes.refresh.listeners.click();
  assert.equal(s.urls.at(-1).searchParams.get("release"), "2");
  const nextCard = s.nodes.articles.children[0];
  assert.notEqual(nextCard, card);
  assert.equal(nextCard.querySelectorAll("details")[0].open, true);
  assert.equal(nextCard.querySelectorAll("h2")[0].children[0].lang, "zh");
  assert.equal(nextCard.querySelectorAll("p").length, 0);
  assert.match(s.nodes.notice.textContent, /Đã tải bản xuất mới/);
  s.feed = edition("1");
  await s.nodes.refresh.listeners.click();
  assert.equal(s.nodes.articles.children[0], nextCard);
  s.offline = true;
  await s.nodes.refresh.listeners.click();
  assert.equal(s.nodes.articles.children[0], nextCard);
  assert.match(s.nodes.notice.textContent, /Chưa tải được/);
});

test("fetches the versioned page on a UI upgrade", async () => {
  const s = await start();
  s.override = {ui_hash: "layout2"};
  await s.nodes.refresh.listeners.click();
  assert.deepEqual(s.redirects, [page + "?ui=layout2"]);
});
