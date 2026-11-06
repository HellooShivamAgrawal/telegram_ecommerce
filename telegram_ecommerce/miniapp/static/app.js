"use strict";
const $ = (id) => document.getElementById(id);
const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
const initial = new URLSearchParams(location.search);
let query = initial.get("q") || "",
  category = initial.get("category") || "",
  page = 0,
  request = 0,
  controller,
  selected,
  botUsername = "";
$("query").value = query;
const money = (value) =>
  value === null
    ? "Price on request"
    : new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 0,
      }).format(value);
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function photo(product) {
  const box = el("div", null, "photo");
  if (product.image) {
    const img = el("img");
    img.src = product.image;
    img.alt = product.name;
    img.loading = "lazy";
    img.width = 720;
    img.height = 720;
    img.onerror = () => box.classList.add("unavailable");
    box.append(img);
  } else box.classList.add("unavailable");
  return box;
}
function price(product) {
  const node = el("div", money(product.price), "price");
  if (product.mrp > product.price && product.price !== null)
    node.append(el("del", money(product.mrp)));
  return node;
}
function closeDetail() {
  $("detail").close();
  tg?.BackButton?.hide();
}
function detail(product) {
  selected = product;
  const content = $("detail-content");
  content.replaceChildren(photo(product));
  const body = el("section");
  body.append(
    el("small", product.category),
    el("h2", product.name),
    price(product),
  );
  if (product.available === false)
    body.append(el("p", "Currently unavailable on the source store."));
  body.append(
    el(
      "p",
      product.description ||
        "Ask us in chat for more information about this product.",
    ),
  );
  content.append(body);
  $("detail").showModal();
  tg?.BackButton?.show();
}
$("close").onclick = closeDetail;
tg?.BackButton?.onClick(closeDetail);
$("detail").addEventListener("close", () => tg?.BackButton?.hide());
$("detail").onclick = (e) => {
  if (
    e.target === $("detail") &&
    e.clientY < $("detail").getBoundingClientRect().top
  )
    closeDetail();
};
$("ask").onclick = () => {
  if (!botUsername) return;
  const url = `https://t.me/${botUsername}?start=product_${selected.id}`;
  if (tg?.openTelegramLink) {
    tg.openTelegramLink(url);
    tg.close();
  } else window.open(url, "_blank", "noopener");
};
async function load(append = false) {
  const serial = ++request;
  controller?.abort();
  controller = new AbortController();
  $("more").disabled = true;
  $("retry").hidden = true;
  $("status").textContent = append
    ? "Loading more…"
    : "Finding your essentials…";
  if (!append) $("products").replaceChildren();
  try {
    const params = new URLSearchParams({
      q: query,
      category,
      page: String(page),
    });
    const response = await fetch("/api/catalog?" + params, {
      signal: controller.signal,
    });
    if (!response.ok) throw Error("Catalog unavailable");
    const data = await response.json();
    if (serial !== request) return;
    botUsername = data.bot_username;
    $("ask").hidden = !botUsername;
    $("brand").firstChild.textContent = data.brand;
    $("heading").textContent = query
      ? `Results for “${query}”`
      : category || "Good things. Daily.";
    $("count").textContent =
      `${data.total}${data.search_limited ? "+" : ""} items`;
    $("categories").replaceChildren();
    for (const name of ["", ...data.categories.map((x) => x.name)]) {
      const button = el("button", name || "All essentials");
      button.setAttribute("aria-pressed", String(category === name));
      button.onclick = () => {
        category = name;
        page = 0;
        load();
      };
      $("categories").append(button);
    }
    for (const product of data.products) {
      const card = el("button", null, "card");
      card.append(
        photo(product),
        el("small", product.category),
        el("h2", product.name),
        price(product),
      );
      if (product.related) card.append(el("span", "Related match", "tag"));
      if (product.available === false)
        card.append(el("span", "Currently unavailable", "tag"));
      card.onclick = () => detail(product);
      $("products").append(card);
    }
    $("status").textContent = data.total
      ? ""
      : "No products found. Try a shorter product name or another category.";
    $("more").hidden = !data.has_more;
    history.replaceState(
      null,
      "",
      "/?" + new URLSearchParams({ q: query, category }),
    );
  } catch (error) {
    if (error.name === "AbortError") return;
    $("status").textContent =
      "We couldn’t load the catalog. Your search is saved — please try again.";
    $("retry").hidden = false;
    $("more").hidden = true;
  } finally {
    if (serial === request) $("more").disabled = false;
  }
}
$("search").onsubmit = (event) => {
  event.preventDefault();
  query = $("query").value.trim();
  page = 0;
  load();
};
let timer;
$("query").oninput = () => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    query = $("query").value.trim();
    page = 0;
    load();
  }, 300);
};
$("more").onclick = () => {
  page++;
  load(true);
};
$("retry").onclick = () => load(page > 0);
load();
